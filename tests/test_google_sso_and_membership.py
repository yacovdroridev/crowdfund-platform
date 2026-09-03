import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from werkzeug.security import check_password_hash

from app import app
from db import UNUSABLE_PASSWORD_PREFIX, get_db, init_db, seed_db


@pytest.fixture
def client(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="headfund-sso-", suffix=".db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("ADMIN_EMAIL", "yacov@drori.org")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "A-very-strong-admin-password-2026!")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MIRIAM_INITIAL_PASSWORD", raising=False)

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


def register(client, email="member@example.com", password="Strong-user-password-2026!", name="חבר קמפיין"):
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


def test_login_page_without_google_env_has_no_oauth_redirect(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "המשך עם Google".encode() not in response.data
    assert b"/login/google" not in response.data

    start = client.get("/login/google", follow_redirects=False)
    assert start.status_code in (302, 303)
    location = start.headers.get("Location") or ""
    assert "accounts.google.com" not in location


def test_google_login_redirects_to_google_when_env_set(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")

    page = client.get("/login")
    assert page.status_code == 200
    assert "המשך עם Google".encode() in page.data

    response = client.get("/login/google?next=/create", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "accounts.google.com" in location
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["test-client-id.apps.googleusercontent.com"]
    assert params["response_type"] == ["code"]
    assert "state" in params and params["state"][0]
    assert "openid" in params["scope"][0]
    assert "email" in params["scope"][0]
    assert "profile" in params["scope"][0]
    with client.session_transaction() as sess:
        assert sess.get("google_oauth_state") == params["state"][0]



class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _complete_google_login(client, monkeypatch, email, google_id="gid-admin-1", name="יעקב דרורי"):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")

    def fake_post(url, **kwargs):
        assert "oauth2.googleapis.com/token" in url
        return _FakeResp({"access_token": "tok-test"})

    def fake_get(url, **kwargs):
        assert "userinfo" in url
        return _FakeResp({
            "email": email,
            "id": google_id,
            "name": name,
            "verified_email": True,
        })

    monkeypatch.setattr("app.requests.post", fake_post)
    monkeypatch.setattr("app.requests.get", fake_get)
    start = client.get("/login/google?next=/admin/users", follow_redirects=False)
    assert start.status_code in (302, 303)
    with client.session_transaction() as sess:
        state = sess.get("google_oauth_state")
    return client.get(f"/login/google/callback?code=ok-code&state={state}", follow_redirects=False)

def test_google_callback_invalid_state_does_not_login(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    client.get("/login/google")
    response = client.get("/login/google/callback?code=fake-code&state=wrong-state")
    assert response.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_header_cta_is_start_campaign_not_start_project():
    base = (Path(__file__).resolve().parents[1] / "templates" / "base.html").read_text(encoding="utf-8")
    assert "התחל גיוס" in base
    assert "התחל פרויקט" not in base


def test_home_shows_start_campaign_cta(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "התחל גיוס".encode() in response.data
    assert "התחל פרויקט".encode() not in response.data


def test_miriam_seed_owns_or_latefila(client):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", ("miriam@drori.org",)).fetchone()
    project = conn.execute("SELECT * FROM projects WHERE slug = ?", ("or-latefila",)).fetchone()
    member = conn.execute(
        "SELECT * FROM project_members WHERE project_id = ? AND user_id = ?",
        (project["id"], user["id"]),
    ).fetchone()
    conn.close()
    assert user is not None
    assert user["full_name"] == "מרים דרורי"
    assert user["role"] == "user"
    assert user["is_active"] == 1
    assert str(user["password_hash"]).startswith(UNUSABLE_PASSWORD_PREFIX)
    assert project["owner_user_id"] == user["id"]
    assert member is not None
    assert member["role"] == "owner"


def test_authorized_member_can_open_edit_stranger_cannot(client):
    register(client, email="editor@example.com", name="עורך קמפיין")
    client.get("/logout")

    client.post("/login", data={
        "email": "yacov@drori.org",
        "password": "A-very-strong-admin-password-2026!",
    })
    granted = client.post(
        "/project/or-latefila/grant-access",
        data={"target_email": "editor@example.com"},
        follow_redirects=True,
    )
    assert granted.status_code == 200
    assert "editor@example.com".encode() in granted.data
    client.get("/logout")

    client.post("/login", data={
        "email": "editor@example.com",
        "password": "Strong-user-password-2026!",
    })
    edit = client.get("/project/or-latefila/edit")
    assert edit.status_code == 200
    assert "עריכת פרטי הפרויקט".encode() in edit.data
    client.get("/logout")

    register(client, email="stranger@example.com", name="משתמש זר")
    denied = client.get("/project/or-latefila/edit", follow_redirects=False)
    assert denied.status_code in (302, 303)
    assert "/login" in (denied.headers.get("Location") or "")


def test_password_change_for_logged_in_user(client):
    register(client)
    changed = client.post(
        "/account",
        data={
            "current_password": "Strong-user-password-2026!",
            "new_password": "Even-Stronger-pass-2026!",
            "new_password_confirm": "Even-Stronger-pass-2026!",
        },
        follow_redirects=True,
    )
    assert changed.status_code == 200
    assert "עודכנה".encode() in changed.data or "עודכן".encode() in changed.data or "הסיסמה".encode() in changed.data
    client.get("/logout")

    old = client.post("/login", data={
        "email": "member@example.com",
        "password": "Strong-user-password-2026!",
    }, follow_redirects=True)
    assert "אימייל או סיסמה שגויים".encode() in old.data

    new = client.post("/login", data={
        "email": "member@example.com",
        "password": "Even-Stronger-pass-2026!",
    }, follow_redirects=False)
    assert new.status_code in (302, 303)


def test_google_only_user_can_set_password_without_current(client):
    conn = get_db()
    user = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", ("miriam@drori.org",)).fetchone()
    assert str(user["password_hash"]).startswith(UNUSABLE_PASSWORD_PREFIX)
    user_id = user["id"]
    conn.close()

    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess.permanent = True

    page = client.get("/account")
    assert page.status_code == 200
    assert b'name="current_password"' not in page.data

    set_pw = client.post(
        "/account",
        data={
            "new_password": "Miriam-sets-pass-2026!",
            "new_password_confirm": "Miriam-sets-pass-2026!",
        },
        follow_redirects=True,
    )
    assert set_pw.status_code == 200

    conn = get_db()
    updated = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    assert password_hash_is_ok(updated["password_hash"], "Miriam-sets-pass-2026!")


def password_hash_is_ok(password_hash, password):
    return check_password_hash(password_hash, password)

def test_google_login_links_existing_admin_by_email(client, monkeypatch):
    response = _complete_google_login(client, monkeypatch, "yacov@drori.org")
    assert response.status_code in (302, 303)
    assert "/admin/users" in (response.headers.get("Location") or "") or "/dashboard" in (response.headers.get("Location") or "")
    with client.session_transaction() as sess:
        user_id = sess.get("user_id")
    conn = get_db()
    admin = conn.execute("SELECT * FROM users WHERE email = ?", ("yacov@drori.org",)).fetchone()
    google_rows = conn.execute("SELECT id FROM users WHERE google_id = ?", ("gid-admin-1",)).fetchall()
    conn.close()
    assert admin["role"] == "admin"
    assert admin["id"] == user_id
    assert admin["google_id"] == "gid-admin-1"
    assert [row["id"] for row in google_rows] == [admin["id"]]


def test_google_login_merges_stray_google_row_into_email_account(client, monkeypatch):
    conn = get_db()
    admin = conn.execute("SELECT id FROM users WHERE email = ?", ("yacov@drori.org",)).fetchone()
    conn.execute(
        """INSERT INTO users
           (email, password_hash, full_name, phone, role, is_active, created_at, google_id)
           VALUES (?, ?, ?, NULL, 'user', 1, ?, ?)""",
        (
            "google-orphan@example.com",
            "unusable$x",
            "יתום גוגל",
            "2026-09-03 00:00:00",
            "gid-admin-1",
        ),
    )
    conn.commit()
    conn.close()

    response = _complete_google_login(client, monkeypatch, "yacov@drori.org")
    assert response.status_code in (302, 303)
    conn = get_db()
    admin = conn.execute("SELECT * FROM users WHERE email = ?", ("yacov@drori.org",)).fetchone()
    orphan = conn.execute("SELECT * FROM users WHERE email = ?", ("google-orphan@example.com",)).fetchone()
    conn.close()
    assert admin["google_id"] == "gid-admin-1"
    assert orphan["google_id"] in (None, "")
    with client.session_transaction() as sess:
        assert sess.get("user_id") == admin["id"]

def test_google_token_invalid_client_shows_secret_hint(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")

    def fake_post(url, **kwargs):
        return _FakeResp({"error": "invalid_client", "error_description": "Unauthorized"}, status_code=401)

    monkeypatch.setattr("app.requests.post", fake_post)
    start = client.get("/login/google", follow_redirects=False)
    with client.session_transaction() as sess:
        state = sess.get("google_oauth_state")
    response = client.get(f"/login/google/callback?code=ok-code&state={state}", follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "GOOGLE_CLIENT_SECRET" in html
    assert "לא ניתן היה להשלים" not in html or "GOOGLE_CLIENT" in html

