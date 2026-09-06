import os
import tempfile

import pytest

from app import app
import db


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()
    os.environ["DATABASE_PATH"] = db_path
    os.environ["ADMIN_EMAIL"] = "yacov@drori.org"
    os.environ["ADMIN_INITIAL_PASSWORD"] = "A-very-strong-admin-password-2026!"

    db.DB_PATH = db_path
    db.init_db()
    db.seed_db()

    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client

    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


def _lead_count(slug="or-latefila"):
    conn = db.get_db()
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM project_interest_leads l
        JOIN projects p ON p.id = l.project_id
        WHERE p.slug = ?
        """,
        (slug,),
    ).fetchone()
    conn.close()
    return int(row["c"] or 0)


def _login_admin(client):
    return client.post(
        "/login",
        data={
            "email": "yacov@drori.org",
            "password": "A-very-strong-admin-password-2026!",
        },
    )


def test_submit_valid_interest_email_appears_on_manage(client):
    slug = "or-latefila"
    assert _lead_count(slug) == 0

    rv = client.post(
        f"/project/{slug}/interest",
        data={"email": "fan@example.com", "full_name": "מעריץ לדוגמה"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)
    assert _lead_count(slug) == 1

    _login_admin(client)
    manage = client.get(f"/project/{slug}/manage/backers")
    assert manage.status_code == 200
    body = manage.data.decode("utf-8")
    assert "fan@example.com" in body
    assert "מעריץ לדוגמה" in body
    assert "מתעניינים" in body
    assert 'data-stat="interest-leads"' in body
    assert 'data-stat="interest-leads">1</div>' in body.replace(",", "")


def test_duplicate_interest_email_does_not_create_second_row(client):
    slug = "or-latefila"
    client.post(
        f"/project/{slug}/interest",
        data={"email": "dup@example.com", "full_name": "ראשון"},
    )
    client.post(
        f"/project/{slug}/interest",
        data={"email": "DUP@example.com", "full_name": "שני"},
    )
    assert _lead_count(slug) == 1


def test_invalid_interest_email_rejected(client):
    slug = "or-latefila"
    before = _lead_count(slug)
    rv = client.post(
        f"/project/{slug}/interest",
        data={"email": "not-an-email", "full_name": "בדיקה"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert _lead_count(slug) == before
    assert "נא להזין כתובת אימייל תקינה".encode("utf-8") in rv.data


def test_unauthorized_cannot_see_interest_emails_on_manage(client):
    slug = "or-latefila"
    client.post(
        f"/project/{slug}/interest",
        data={"email": "secret-lead@example.com", "full_name": "סודי"},
    )
    assert _lead_count(slug) == 1

    # Not logged in
    rv = client.get(f"/project/{slug}/manage/backers", follow_redirects=False)
    assert rv.status_code in (302, 303)
    assert b"secret-lead@example.com" not in rv.data
    loc = rv.headers.get("Location", "")
    assert "/login" in loc

    # Logged in as a non-owner / non-admin user
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO users (email, password_hash, full_name, role, is_active, created_at)
        VALUES (?, ?, ?, 'user', 1, datetime('now'))
        """,
        (
            "outsider@example.com",
            # werkzeug hash for a throwaway password; login path uses check_password_hash
            __import__("werkzeug.security", fromlist=["generate_password_hash"]).generate_password_hash(
                "Outsider-Pass-2026!"
            ),
            "אורח חיצוני",
        ),
    )
    conn.commit()
    conn.close()

    client.get("/logout")
    client.post(
        "/login",
        data={"email": "outsider@example.com", "password": "Outsider-Pass-2026!"},
    )
    rv2 = client.get(f"/project/{slug}/manage/backers", follow_redirects=True)
    assert b"secret-lead@example.com" not in rv2.data
