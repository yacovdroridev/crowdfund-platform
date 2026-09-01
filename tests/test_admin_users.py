import os
import tempfile

import pytest
from werkzeug.security import check_password_hash

from app import app
from db import UNUSABLE_PASSWORD_PREFIX, get_db, init_db, seed_db


ADMIN_EMAIL = "yacov@drori.org"
ADMIN_PASSWORD = "A-very-strong-admin-password-2026!"


@pytest.fixture
def client(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="headfund-admin-users-", suffix=".db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", ADMIN_PASSWORD)

    import db
    db.DB_PATH = db_path
    init_db()
    seed_db()

    app.config.update(TESTING=True, CSRF_ENABLED=False)
    with app.test_client() as test_client:
        yield test_client

    os.close(fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


def login_admin(client):
    return client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        follow_redirects=True,
    )


def register(client, email="member@example.com", password="Strong-user-password-2026!", name="ישראל ישראלי"):
    return client.post(
        "/register",
        data={
            "full_name": name,
            "email": email,
            "phone": "050-1234567",
            "password": password,
            "password_confirm": password,
            "legal_accept": "on",
        },
        follow_redirects=True,
    )


def user_id_for(email):
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row["id"]


def test_non_admin_cannot_open_users_page(client):
    anonymous = client.get("/admin/users", follow_redirects=False)
    assert anonymous.status_code in (302, 303)
    assert "/login" in (anonymous.headers.get("Location") or "")

    register(client)
    denied = client.get("/admin/users", follow_redirects=False)
    assert denied.status_code in (302, 303, 403)


def test_admin_can_list_users_without_password_hashes(client):
    login_admin(client)
    page = client.get("/admin/users")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "ניהול משתמשים" in body
    assert "yacov@drori.org" in body
    assert "מרים דרורי" in body
    assert "miriam@drori.org" in body
    assert "אור לתפילה" in body or "or-latefila" in body
    assert "Google" in body or "הזמנה" in body
    assert "סיסמה" in body
    assert "scrypt:" not in body
    assert "unusable$" not in body
    assert "password_hash" not in body
    assert "pbkdf2:" not in body


def test_admin_users_search_by_name_and_email(client):
    login_admin(client)
    by_name = client.get("/admin/users?q=מרים")
    assert by_name.status_code == 200
    name_body = by_name.get_data(as_text=True)
    assert "miriam@drori.org" in name_body
    assert "admin@example.com" not in name_body

    by_email = client.get("/admin/users?q=miriam@drori.org")
    email_body = by_email.get_data(as_text=True)
    assert "מרים דרורי" in email_body
    assert "demo@example.com" not in email_body


def test_admin_can_toggle_admin_flag_but_not_last_admin(client):
    login_admin(client)
    demo_id = user_id_for("demo@example.com")

    granted = client.post(f"/admin/users/{demo_id}/toggle-admin", follow_redirects=True)
    assert granted.status_code == 200
    conn = get_db()
    role = conn.execute("SELECT role FROM users WHERE id = ?", (demo_id,)).fetchone()[0]
    conn.close()
    assert role == "admin"

    revoked = client.post(f"/admin/users/{demo_id}/toggle-admin", follow_redirects=True)
    assert revoked.status_code == 200
    conn = get_db()
    role = conn.execute("SELECT role FROM users WHERE id = ?", (demo_id,)).fetchone()[0]
    conn.close()
    assert role == "user"

    example_admin_id = user_id_for("admin@example.com")
    client.post(f"/admin/users/{example_admin_id}/toggle-admin", follow_redirects=True)

    self_id = user_id_for(ADMIN_EMAIL)
    blocked = client.post(f"/admin/users/{self_id}/toggle-admin", follow_redirects=True)
    assert blocked.status_code == 200
    assert "המנהל האחרון".encode() in blocked.data or "לא ניתן".encode() in blocked.data
    conn = get_db()
    still_admin = conn.execute("SELECT role FROM users WHERE id = ?", (self_id,)).fetchone()[0]
    admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
    conn.close()
    assert still_admin == "admin"
    assert admin_count == 1


def test_admin_can_grant_and_revoke_campaign_access(client):
    register(client, email="editor@example.com", name="עורך קמפיין")
    client.get("/logout")
    login_admin(client)
    editor_id = user_id_for("editor@example.com")

    granted = client.post(
        f"/admin/users/{editor_id}/grant-access",
        data={"project_slug": "or-latefila"},
        follow_redirects=True,
    )
    assert granted.status_code == 200
    assert "or-latefila".encode() in granted.data or "אור לתפילה".encode() in granted.data

    conn = get_db()
    member = conn.execute(
        """SELECT m.role FROM project_members m
           JOIN projects p ON p.id = m.project_id
           WHERE p.slug = ? AND m.user_id = ?""",
        ("or-latefila", editor_id),
    ).fetchone()
    conn.close()
    assert member is not None
    assert member["role"] in ("editor", "owner")

    client.get("/logout")
    client.post("/login", data={"email": "editor@example.com", "password": "Strong-user-password-2026!"})
    edit = client.get("/project/or-latefila/edit")
    assert edit.status_code == 200

    client.get("/logout")
    login_admin(client)
    revoked = client.post(
        f"/admin/users/{editor_id}/revoke-access",
        data={"project_slug": "or-latefila"},
        follow_redirects=True,
    )
    assert revoked.status_code == 200
    conn = get_db()
    gone = conn.execute(
        """SELECT 1 FROM project_members m
           JOIN projects p ON p.id = m.project_id
           WHERE p.slug = ? AND m.user_id = ?""",
        ("or-latefila", editor_id),
    ).fetchone()
    conn.close()
    assert gone is None


def test_admin_can_create_password_and_invite_users(client):
    login_admin(client)
    created = client.post(
        "/admin/users",
        data={
            "full_name": "משתמש חדש",
            "email": "newbie@example.com",
            "password": "Fresh-user-pass-2026!",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "newbie@example.com".encode() in created.data

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", ("newbie@example.com",)).fetchone()
    conn.close()
    assert user["full_name"] == "משתמש חדש"
    assert user["role"] == "user"
    assert check_password_hash(user["password_hash"], "Fresh-user-pass-2026!")

    invited = client.post(
        "/admin/users",
        data={"full_name": "מוזמן", "email": "invitee@example.com"},
        follow_redirects=True,
    )
    assert invited.status_code == 200
    conn = get_db()
    invitee = conn.execute("SELECT * FROM users WHERE email = ?", ("invitee@example.com",)).fetchone()
    conn.close()
    assert str(invitee["password_hash"]).startswith(UNUSABLE_PASSWORD_PREFIX)
    assert "unusable$".encode() not in invited.data


def test_dashboard_and_admin_nav_link_to_users_page(client):
    login_admin(client)
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert b"/admin/users" in dashboard.data
    assert "ניהול משתמשים".encode() in dashboard.data

def test_admin_can_edit_user_and_cannot_deactivate_self(client):
    login_admin(client)
    miriam_id = user_id_for("miriam@drori.org")
    self_id = user_id_for(ADMIN_EMAIL)

    edited = client.post(
        f"/admin/users/{miriam_id}/edit",
        data={
            "full_name": "מרים דרורי מעודכן",
            "phone": "050-9998877",
            "role": "user",
            "is_active": "on",
        },
        follow_redirects=True,
    )
    assert edited.status_code == 200
    conn = get_db()
    updated = conn.execute("SELECT full_name, phone, role, is_active FROM users WHERE id = ?", (miriam_id,)).fetchone()
    conn.close()
    assert updated["full_name"] == "מרים דרורי מעודכן"
    assert updated["phone"] == "050-9998877"
    assert updated["role"] == "user"
    assert updated["is_active"] == 1

    blocked = client.post(
        f"/admin/users/{self_id}/edit",
        data={
            "full_name": "מנהל מערכת (יעקב דרורי)",
            "phone": "",
            "role": "admin",
        },
        follow_redirects=True,
    )
    assert blocked.status_code == 200
    conn = get_db()
    still = conn.execute("SELECT role, is_active FROM users WHERE id = ?", (self_id,)).fetchone()
    conn.close()
    assert still["role"] == "admin"
    assert still["is_active"] == 1


def test_admin_memberships_form_attaches_to_or_latefila(client):
    login_admin(client)
    client.post(
        "/admin/users",
        data={"full_name": "עורך אור", "email": "or-editor@example.com"},
        follow_redirects=True,
    )
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("or-editor@example.com",)).fetchone()["id"]
    project_id = conn.execute("SELECT id FROM projects WHERE slug = ?", ("or-latefila",)).fetchone()["id"]
    conn.close()

    attached = client.post(
        "/admin/users/memberships",
        data={"user_id": str(user_id), "project_id": str(project_id), "member_role": "editor"},
        follow_redirects=True,
    )
    assert attached.status_code == 200
    conn = get_db()
    member = conn.execute(
        "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    conn.close()
    assert member is not None
    assert member["role"] == "editor"
