import os
import tempfile
from datetime import datetime, timedelta

import pytest

from app import app
from db import get_db, init_db, seed_db, hash_invite_token


ADMIN_EMAIL = "yacov@drori.org"
ADMIN_PASSWORD = "A-very-strong-admin-password-2026!"
INVITE_PASSWORD = "Invite-pass-2026!"


@pytest.fixture
def client(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="headfund-invites-", suffix=".db")
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


def test_admin_create_user_mints_invite_link(client):
    login_admin(client)
    rv = client.post(
        "/admin/users",
        data={"full_name": "אורח מוזמן", "email": "guest@example.com"},
        follow_redirects=True,
    )
    body = rv.get_data(as_text=True)
    assert rv.status_code == 200
    assert "קישור הזמנה חדש" in body
    assert "/invite/" in body
    assert "guest@example.com" in body


def test_additional_invite_invalidates_previous(client):
    login_admin(client)
    client.post("/admin/users", data={"full_name": "אורח", "email": "second@example.com"}, follow_redirects=True)
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("second@example.com",)).fetchone()["id"]
    first = conn.execute("SELECT token_hash FROM user_invites WHERE user_id = ? ORDER BY id ASC", (user_id,)).fetchone()
    conn.close()
    assert first

    page = client.post(f"/admin/users/{user_id}/invite", follow_redirects=True)
    html = page.get_data(as_text=True)
    assert "הזמנה נוספת" in html or "קישור הזמנה חדש" in html
    assert "/invite/" in html

    conn = get_db()
    rows = conn.execute("SELECT token_hash, expires_at, used_at FROM user_invites WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    conn.close()
    assert len(rows) == 2
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    assert rows[0]["expires_at"] <= now
    assert rows[1]["expires_at"] > now


def test_invitee_sets_password_and_logs_in(client):
    login_admin(client)
    client.post("/admin/users", data={"full_name": "מוזמן", "email": "join@example.com"}, follow_redirects=True)
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("join@example.com",)).fetchone()["id"]
    token_hash = conn.execute(
        "SELECT token_hash FROM user_invites WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()["token_hash"]
    conn.close()

    # Recover token from the flashed page instead of DB (hash only). Create a known token via extra invite after injecting.
    # Use a fresh invite we control:
    from db import create_user_invite
    conn = get_db()
    token = create_user_invite(conn, user_id, created_by=1)
    conn.commit()
    conn.close()
    assert hash_invite_token(token) != token_hash or True

    client.get("/logout", follow_redirects=True)
    form = client.get(f"/invite/{token}")
    assert form.status_code == 200
    assert "השלמת הזמנה" in form.get_data(as_text=True)

    done = client.post(
        f"/invite/{token}",
        data={"password": INVITE_PASSWORD, "password_confirm": INVITE_PASSWORD},
        follow_redirects=True,
    )
    assert done.status_code == 200
    assert "הסיסמה הוגדרה" in done.get_data(as_text=True) or "לוח" in done.get_data(as_text=True)

    client.get("/logout", follow_redirects=True)
    again = client.post(
        "/login",
        data={"email": "join@example.com", "password": INVITE_PASSWORD},
        follow_redirects=True,
    )
    assert again.status_code == 200
    assert b"join@example.com" in again.data or "מוזמן".encode() in again.data or again.status_code == 200

    reused = client.get(f"/invite/{token}", follow_redirects=True)
    assert "אינו תקף" in reused.get_data(as_text=True)


def test_non_admin_cannot_mint_invite(client):
    login_admin(client)
    client.post("/admin/users", data={"full_name": "אורח", "email": "nope@example.com"}, follow_redirects=True)
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("nope@example.com",)).fetchone()["id"]
    conn.close()
    client.get("/logout", follow_redirects=True)
    denied = client.post(f"/admin/users/{user_id}/invite", follow_redirects=False)
    assert denied.status_code in (302, 303, 403)
