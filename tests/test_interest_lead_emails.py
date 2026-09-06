import os
import tempfile

import pytest

from app import app
import db


ADMIN_EMAIL = "yacov@drori.org"
ADMIN_PASSWORD = "A-very-strong-admin-password-2026!"
SLUG = "or-latefila"


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp(prefix="headfund-interest-mail-", suffix=".db")
    os.environ["DATABASE_PATH"] = db_path
    os.environ["ADMIN_EMAIL"] = ADMIN_EMAIL
    os.environ["ADMIN_INITIAL_PASSWORD"] = ADMIN_PASSWORD

    db.DB_PATH = db_path
    db.init_db()
    db.seed_db()

    app.config.update(TESTING=True, CSRF_ENABLED=False, OUTBOX=[])
    with app.test_client() as test_client:
        yield test_client

    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


def _login_admin(client):
    return client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        follow_redirects=True,
    )


def _add_lead(client, email, full_name="מתעניין"):
    return client.post(
        f"/project/{SLUG}/interest",
        data={"email": email, "full_name": full_name},
        follow_redirects=False,
    )


def test_new_interest_lead_appends_thank_you_to_outbox(client):
    app.config["OUTBOX"] = []
    rv = _add_lead(client, "fan@example.com", "מעריץ")
    assert rv.status_code in (302, 303)
    assert len(app.config["OUTBOX"]) == 1
    mail = app.config["OUTBOX"][0]
    assert mail["to"] == "fan@example.com"
    assert "תודה על ההתעניינות" in mail["subject"]
    assert "/project/" in mail["body"] or "or-latefila" in mail["body"]
    assert "מעריץ" in mail["body"]


def test_duplicate_interest_lead_does_not_send_again(client):
    app.config["OUTBOX"] = []
    _add_lead(client, "dup@example.com", "ראשון")
    assert len(app.config["OUTBOX"]) == 1
    _add_lead(client, "DUP@example.com", "שני")
    assert len(app.config["OUTBOX"]) == 1


def test_project_update_emails_each_interest_lead(client):
    app.config["OUTBOX"] = []
    _add_lead(client, "one@example.com", "אחד")
    _add_lead(client, "two@example.com", "שניים")
    assert len(app.config["OUTBOX"]) == 2
    app.config["OUTBOX"] = []

    _login_admin(client)
    rv = client.post(
        f"/project/{SLUG}/add-update",
        data={
            "update_title": "עדכון חשוב",
            "update_content": "הגענו ל-50% מהיעד!",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert len(app.config["OUTBOX"]) == 2
    recipients = sorted(m["to"] for m in app.config["OUTBOX"])
    assert recipients == ["one@example.com", "two@example.com"]
    for mail in app.config["OUTBOX"]:
        assert "עדכון חשוב" in mail["subject"] or "עדכון חשוב" in mail["body"]
        assert "הגענו ל-50%" in mail["body"]
        assert "or-latefila" in mail["body"] or "/project/" in mail["body"]


def test_manage_blast_emails_interest_leads(client):
    app.config["OUTBOX"] = []
    _add_lead(client, "blast1@example.com", "א")
    _add_lead(client, "blast2@example.com", "ב")
    app.config["OUTBOX"] = []

    _login_admin(client)
    page = client.get(f"/project/{SLUG}/manage/backers")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "שלחו הודעה למתעניינים" in html

    rv = client.post(
        f"/project/{SLUG}/manage/interest-blast",
        data={
            "subject": "הודעה מיוחדת",
            "body": "שלום לכולם, תודה על ההתעניינות!",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "נשלחה הודעה ל-2 מתעניינים" in rv.get_data(as_text=True)
    assert len(app.config["OUTBOX"]) == 2
    recipients = sorted(m["to"] for m in app.config["OUTBOX"])
    assert recipients == ["blast1@example.com", "blast2@example.com"]
    for mail in app.config["OUTBOX"]:
        assert mail["subject"] == "הודעה מיוחדת"
        assert "תודה על ההתעניינות" in mail["body"]
