import os
import tempfile
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash

from app import app
from db import get_db, init_db, seed_db


@pytest.fixture
def client(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="headfund-test-", suffix=".db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("ADMIN_EMAIL", "yacov@drori.org")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "A-very-strong-admin-password-2026!")

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


def register(client, email="member@example.com", password="Strong-user-password-2026!"):
    return client.post(
        "/register",
        data={
            "full_name": "ישראל ישראלי",
            "email": email,
            "phone": "050-1234567",
            "password": password,
            "password_confirm": password,
            "legal_accept": "on",
        },
        follow_redirects=True,
    )


def login(client, email, password, follow_redirects=True):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=follow_redirects,
    )


def test_render_config_uses_the_admin_password_variable_expected_by_the_app():
    render_config = (Path(__file__).resolve().parents[1] / "render.yaml").read_text(encoding="utf-8")
    assert "key: ADMIN_INITIAL_PASSWORD" in render_config
    assert "key: ADMIN_PASSWORD\n" not in render_config
    assert "key: SESSION_COOKIE_SECURE" in render_config


def test_security_headers_and_legal_center_trailing_slash(client):
    response = client.get("/", base_url="https://headfund.example")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"

    legal = client.get("/legal/", base_url="https://headfund.example")
    assert legal.status_code == 200
    assert "מרכז מידע משפטי".encode() in legal.data


def test_visual_theme_is_white_first_without_purple_tokens():
    template_dir = Path(__file__).resolve().parents[1] / "templates"
    rendered_templates = "\n".join(
        path.read_text(encoding="utf-8") for path in template_dir.rglob("*.html")
    )
    for token in ("purple-", "violet-", "fuchsia-", "indigo-"):
        assert token not in rendered_templates
    base = (template_dir / "base.html").read_text(encoding="utf-8")
    assert 'background-color: #ffffff' in base
    assert '<footer class="bg-white' in base


def test_legal_center_documents_render_and_footer_avoids_false_payment_claims(client):
    home = client.get("/")
    assert "סליקה מאובטחת SSL".encode() not in home.data
    assert b"/legal/privacy" in home.data

    for path, heading in [
        ("/legal", "מרכז מידע משפטי"),
        ("/legal/privacy", "מדיניות פרטיות"),
        ("/legal/terms", "תנאי שימוש"),
        ("/legal/creators", "תנאים ליוצרי קמפיינים"),
        ("/legal/supporters", "תנאים לתומכים ומדיניות תשלומים"),
        ("/legal/content", "כללי תוכן וקניין רוחני"),
        ("/legal/cookies", "מדיניות Cookies"),
        ("/legal/accessibility", "הצהרת נגישות"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert heading.encode() in response.data
        assert "טיוטה טכנית".encode() in response.data


def test_registration_normalizes_email_hashes_password_and_logs_user_in(client):
    response = register(client, email=" Member@Example.COM ")

    assert response.status_code == 200
    assert "ישראל ישראלי".encode() in response.data

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", ("member@example.com",)).fetchone()
    conn.close()

    assert user is not None
    assert user["password_hash"] != "Strong-user-password-2026!"
    assert check_password_hash(user["password_hash"], "Strong-user-password-2026!")
    assert user["role"] == "user"
    assert user["is_active"] == 1


def test_registration_rejects_duplicate_email_case_insensitively(client):
    register(client, email="member@example.com")
    client.get("/logout")
    response = register(client, email="MEMBER@example.com")

    assert "כבר קיים חשבון".encode() in response.data
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM users WHERE email = ?", ("member@example.com",)).fetchone()[0]
    conn.close()
    assert count == 1


def test_login_uses_email_and_password_and_rejects_wrong_password_generically(client):
    register(client)
    client.get("/logout")

    failed = login(client, "member@example.com", "not-the-password")
    assert "אימייל או סיסמה שגויים".encode() in failed.data

    success = login(client, "MEMBER@EXAMPLE.COM", "Strong-user-password-2026!")
    assert "ישראל ישראלי".encode() in success.data


def test_csrf_protects_state_changing_requests(client):
    import re

    app.config["CSRF_ENABLED"] = True
    missing = client.post("/login", data={"email": "nobody@example.com", "password": "wrong"})
    assert missing.status_code == 400

    login_page = client.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
    protected = client.post(
        "/login",
        data={"email": "nobody@example.com", "password": "wrong", "csrf_token": token},
    )
    assert protected.status_code == 200
    assert "אימייל או סיסמה שגויים".encode() in protected.data


def test_admin_is_provisioned_from_environment_and_can_open_dashboard(client):
    conn = get_db()
    admin = conn.execute("SELECT * FROM users WHERE email = ?", ("yacov@drori.org",)).fetchone()
    conn.close()

    assert admin is not None
    assert admin["role"] == "admin"
    assert admin["password_hash"] != "A-very-strong-admin-password-2026!"

    response = login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")
    assert "לוח ניהול ומעקב גיוסים".encode() in response.data
    assert "ניהול קטגוריות".encode() in response.data
    assert b"/admin/projects/synapse-guardian-iot/toggle" in response.data


def test_repeated_failed_logins_are_temporarily_blocked(client):
    for _ in range(5):
        response = client.post(
            "/login",
            data={"email": "yacov@drori.org", "password": "wrong-password"},
        )
        assert response.status_code == 200

    blocked = client.post(
        "/login",
        data={"email": "yacov@drori.org", "password": "A-very-strong-admin-password-2026!"},
    )
    assert blocked.status_code == 429
    assert "ניסיונות כניסה רבים מדי".encode() in blocked.data


def test_project_edit_uses_account_ownership_not_legacy_pin(client):
    response = client.get("/project/synapse-guardian-iot/edit")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert "/project/synapse-guardian-iot/edit" in response.headers["Location"]

    legacy = client.post(
        "/project/synapse-guardian-iot/auth",
        data={"auth_key": "202600"},
    )
    assert legacy.status_code == 302
    assert "/login" in legacy.headers["Location"]


def test_project_story_html_is_sanitized_on_create(client):
    register(client, password="Creator-Sanitize-Password-2026!")
    client.post(
        "/create",
        data={
            "title": "פרויקט בטוח",
            "subtitle": "בדיקת ניקוי תוכן",
            "category": "technology",
            "goal_amount": "1000",
            "days_total": "30",
            "creator_name": "דנה ישראלי",
            "creator_email": "dana@example.com",
            "creator_phone": "0501234567",
            "creator_bio": "יוצרת",
            "story_html": '<script>alert("xss")</script><p>תוכן תקין</p>',
            "legal_accept": "on",
        },
    )
    conn = get_db()
    story = conn.execute("SELECT story_html FROM projects WHERE title = ?", ("פרויקט בטוח",)).fetchone()[0]
    conn.close()
    assert "<script" not in story
    assert "alert" not in story
    assert "<p>תוכן תקין</p>" in story


def test_new_project_requires_login_and_is_owned_by_user_pending_admin_activation(client):
    anonymous = client.get("/create")
    assert anonymous.status_code == 302
    assert "/login" in anonymous.location

    register(client)
    response = client.post(
        "/create",
        data={
            "title": "מיזם בדיקה מאובטח",
            "subtitle": "מיזם חדש שממתין לאישור מנהל לפני פרסום",
            "category": "technology",
            "goal_amount": "10000",
            "days_total": "30",
            "creator_name": "ישראל ישראלי",
            "creator_email": "member@example.com",
            "creator_phone": "050-1234567",
            "creator_bio": "יוצר המיזם",
            "cover_image": "https://example.com/cover.jpg",
            "story_html": "<p>סיפור המיזם</p>",
            "reward_title[]": "תשורת תודה",
            "reward_amount[]": "50",
            "reward_desc[]": "תודה",
            "reward_delivery[]": "ינואר 2027",
            "reward_limit[]": "",
            "legal_accept": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    conn = get_db()
    project = conn.execute("SELECT * FROM projects WHERE title = ?", ("מיזם בדיקה מאובטח",)).fetchone()
    user = conn.execute("SELECT id FROM users WHERE email = ?", ("member@example.com",)).fetchone()
    conn.close()
    assert project["owner_user_id"] == user["id"]
    assert project["is_active"] == 0


def test_admin_can_toggle_project_visibility(client):
    login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")

    response = client.post(
        "/admin/projects/synapse-guardian-iot/toggle",
        follow_redirects=True,
    )
    assert "הושבת".encode() in response.data
    client.get("/logout")
    assert client.get("/project/synapse-guardian-iot").status_code == 404

    login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")
    response = client.post(
        "/admin/projects/synapse-guardian-iot/toggle",
        follow_redirects=True,
    )
    assert "הופעל".encode() in response.data
    assert client.get("/project/synapse-guardian-iot").status_code == 200


def test_admin_can_add_and_disable_category(client):
    login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")

    added = client.post(
        "/admin/categories",
        data={"slug": "health", "name": "בריאות ורווחה"},
        follow_redirects=True,
    )
    assert "בריאות ורווחה".encode() in added.data

    disabled = client.post(
        "/admin/categories/health/toggle",
        follow_redirects=True,
    )
    assert "הושבתה".encode() in disabled.data

    conn = get_db()
    category = conn.execute("SELECT * FROM categories WHERE slug = 'health'").fetchone()
    conn.close()
    assert category["is_active"] == 0


def test_disabled_category_is_not_offered_to_project_creators(client):
    login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")
    client.post("/admin/categories", data={"name": "בריאות", "slug": "health"})
    page = client.get("/create")
    assert "בריאות".encode() in page.data

    client.post("/admin/categories/health/toggle")
    page = client.get("/create")
    assert "בריאות".encode() not in page.data


def test_category_management_controls_public_filter(client):
    login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")
    client.post("/admin/categories", data={"name": "בריאות", "slug": "health"})
    assert "בריאות".encode() in client.get("/").data
    client.post("/admin/categories/health/toggle")
    assert "בריאות".encode() not in client.get("/").data


def test_inactive_project_cannot_receive_new_pledges(client):
    conn = get_db()
    conn.execute("UPDATE projects SET is_active = 0 WHERE slug = ?", ("synapse-guardian-iot",))
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM pledges").fetchone()[0]
    conn.close()

    response = client.post(
        "/project/synapse-guardian-iot/pledge",
        data={"amount": "50", "payment_method": "bit", "legal_accept": "on"},
    )
    assert response.status_code == 404
    conn = get_db()
    after = conn.execute("SELECT COUNT(*) FROM pledges").fetchone()[0]
    conn.close()
    assert after == before


def test_inactive_project_is_hidden_from_public_api_but_visible_to_admin(client):
    login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")
    client.post("/admin/projects/synapse-guardian-iot/toggle")
    client.get("/logout")

    assert client.get("/api/projects/synapse-guardian-iot").status_code == 404
    assert all(p["slug"] != "synapse-guardian-iot" for p in client.get("/api/projects").json["projects"])

    login(client, "yacov@drori.org", "A-very-strong-admin-password-2026!")
    assert client.get("/api/projects/synapse-guardian-iot").status_code == 200
