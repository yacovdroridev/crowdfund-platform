import os
import tempfile

import pytest

from app import app
from db import get_db, init_db, seed_db


ADMIN_EMAIL = "yacov@drori.org"
ADMIN_PASSWORD = "A-very-strong-admin-password-2026!"
NEW_PASSWORD = "Reset-ok-2026!"


@pytest.fixture
def client(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="headfund-forgot-", suffix=".db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", ADMIN_PASSWORD)

    import db
    db.DB_PATH = db_path
    init_db()
    seed_db()

    app.config.update(TESTING=True, CSRF_ENABLED=False, OUTBOX=[])
    with app.test_client() as test_client:
        yield test_client

    os.close(fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_login_has_forgot_password_link(client):
    html = client.get("/login").get_data(as_text=True)
    assert "שכחתי סיסמה" in html
    assert "/forgot-password" in html


def test_unknown_email_does_not_send_and_does_not_leak(client):
    rv = client.post("/forgot-password", data={"email": "nobody@example.com"}, follow_redirects=True)
    html = rv.get_data(as_text=True)
    assert "אם קיים חשבון" in html
    assert app.config["OUTBOX"] == []


def test_known_email_sends_reset_link_and_sets_password(client):
    rv = client.post("/forgot-password", data={"email": ADMIN_EMAIL}, follow_redirects=True)
    assert "אם קיים חשבון" in rv.get_data(as_text=True)
    assert len(app.config["OUTBOX"]) == 1
    mail = app.config["OUTBOX"][0]
    assert mail["to"] == ADMIN_EMAIL
    assert "איפוס סיסמה" in mail["subject"]
    assert "/reset/" in mail["body"]
    token = mail["body"].split("/reset/")[1].split()[0].strip()

    page = client.get(f"/reset/{token}")
    assert page.status_code == 200
    assert "איפוס סיסמה" in page.get_data(as_text=True)

    done = client.post(
        f"/reset/{token}",
        data={"password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
        follow_redirects=True,
    )
    assert done.status_code == 200

    client.get("/logout", follow_redirects=True)
    login = client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": NEW_PASSWORD},
        follow_redirects=True,
    )
    assert login.status_code == 200
    assert "התחברת בהצלחה" in login.get_data(as_text=True) or "לוח" in login.get_data(as_text=True)
