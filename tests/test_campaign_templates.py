import os
import tempfile

import pytest
from app import app
from db import init_db, seed_db, get_db, normalize_campaign_template, CAMPAIGN_TEMPLATES


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()
    os.environ["DATABASE_PATH"] = db_path
    os.environ["ADMIN_EMAIL"] = "yacov@drori.org"
    os.environ["ADMIN_INITIAL_PASSWORD"] = "A-very-strong-admin-password-2026!"

    import db
    db.DB_PATH = db_path
    init_db()
    seed_db()

    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client

    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


def _login_admin(client):
    return client.post(
        "/login",
        data={"email": "yacov@drori.org", "password": "A-very-strong-admin-password-2026!"},
        follow_redirects=True,
    )


def test_normalize_campaign_template():
    assert normalize_campaign_template(None) == "classic"
    assert normalize_campaign_template("HONEY") == "honey"
    assert normalize_campaign_template("nope") == "classic"
    assert set(CAMPAIGN_TEMPLATES) == {"classic", "honey", "linen"}


def test_seeded_project_defaults_to_classic(client):
    conn = get_db()
    row = conn.execute("SELECT template FROM projects WHERE slug = ?", ("synapse-guardian-iot",)).fetchone()
    conn.close()
    assert row["template"] == "classic"
    rv = client.get("/project/synapse-guardian-iot")
    html = rv.data.decode("utf-8")
    assert 'data-template="classic"' in html
    assert "campaign-themes.css" in html


def test_project_and_checkout_use_saved_template(client):
    conn = get_db()
    conn.execute("UPDATE projects SET template = ? WHERE slug = ?", ("honey", "synapse-guardian-iot"))
    conn.commit()
    conn.close()

    page = client.get("/project/synapse-guardian-iot").data.decode("utf-8")
    assert 'data-template="honey"' in page
    assert "Frank+Ruhl+Libre" in page

    checkout = client.get("/project/synapse-guardian-iot/checkout").data.decode("utf-8")
    assert 'data-template="honey"' in checkout
    assert "checkout-page" in checkout


def test_invalid_template_rejected_on_edit(client):
    _login_admin(client)
    conn = get_db()
    project = dict(conn.execute("SELECT * FROM projects WHERE slug = ?", ("synapse-guardian-iot",)).fetchone())
    conn.close()

    rv = client.post(
        "/project/synapse-guardian-iot/edit",
        data={
            "title": project["title"],
            "subtitle": project["subtitle"],
            "category": project["category"],
            "goal_amount": project["goal_amount"],
            "current_amount": project["current_amount"],
            "backers_count": project["backers_count"],
            "creator_name": project["creator_name"],
            "creator_email": project["creator_email"] or "",
            "creator_phone": project["creator_phone"] or "",
            "creator_bio": project["creator_bio"] or "",
            "creator_avatar": project["creator_avatar"],
            "cover_image": project["cover_image"],
            "story_html": project["story_html"],
            "main_media_type": project["main_media_type"] or "auto",
            "template": "neon",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    conn = get_db()
    saved = conn.execute("SELECT template FROM projects WHERE slug = ?", ("synapse-guardian-iot",)).fetchone()
    conn.close()
    assert saved["template"] == "classic"


def test_edit_saves_linen_template(client):
    _login_admin(client)
    conn = get_db()
    project = dict(conn.execute("SELECT * FROM projects WHERE slug = ?", ("synapse-guardian-iot",)).fetchone())
    conn.close()

    rv = client.post(
        "/project/synapse-guardian-iot/edit",
        data={
            "title": project["title"],
            "subtitle": project["subtitle"],
            "category": project["category"],
            "goal_amount": project["goal_amount"],
            "current_amount": project["current_amount"],
            "backers_count": project["backers_count"],
            "creator_name": project["creator_name"],
            "creator_email": project["creator_email"] or "",
            "creator_phone": project["creator_phone"] or "",
            "creator_bio": project["creator_bio"] or "",
            "creator_avatar": project["creator_avatar"],
            "cover_image": project["cover_image"],
            "story_html": project["story_html"],
            "main_media_type": project["main_media_type"] or "auto",
            "template": "linen",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    conn = get_db()
    saved = conn.execute("SELECT template FROM projects WHERE slug = ?", ("synapse-guardian-iot",)).fetchone()
    conn.close()
    assert saved["template"] == "linen"
    html = client.get("/project/synapse-guardian-iot").data.decode("utf-8")
    assert 'data-template="linen"' in html


def test_create_and_edit_show_template_picker(client):
    _login_admin(client)
    create_html = client.get("/create").data.decode("utf-8")
    assert "מראה דף הקמפיין" in create_html
    assert 'value="honey"' in create_html
    assert 'value="linen"' in create_html

    edit_html = client.get("/project/synapse-guardian-iot/edit").data.decode("utf-8")
    assert "מראה דף הקמפיין" in edit_html
    assert 'value="classic"' in edit_html

def test_or_latefila_stays_classic_by_default(client):
    conn = get_db()
    row = conn.execute("SELECT template FROM projects WHERE slug = ?", ("or-latefila",)).fetchone()
    conn.close()
    assert row["template"] == "classic"
    html = client.get("/project/or-latefila").data.decode("utf-8")
    assert 'data-template="classic"' in html

