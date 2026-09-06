import os
import tempfile

import pytest

from app import app, is_bot_user_agent
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


def _page_views(slug):
    conn = db.get_db()
    row = conn.execute("SELECT page_views FROM projects WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    return int(row["page_views"] or 0)


def test_is_bot_user_agent_helpers():
    assert is_bot_user_agent("Mozilla/5.0 (compatible; Googlebot/2.1)")
    assert is_bot_user_agent("facebookexternalhit/1.1")
    assert is_bot_user_agent("TelegramBot (like TwitterBot)")
    assert is_bot_user_agent("")
    assert not is_bot_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )


def test_public_project_view_increments_page_views(client):
    slug = "or-latefila"
    before = _page_views(slug)
    rv = client.get(
        f"/project/{slug}",
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        },
    )
    assert rv.status_code == 200
    assert _page_views(slug) == before + 1

    rv2 = client.get(
        f"/project/{slug}",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
        },
    )
    assert rv2.status_code == 200
    assert _page_views(slug) == before + 2


def test_bot_user_agent_does_not_increment_page_views(client):
    slug = "or-latefila"
    before = _page_views(slug)
    for ua in (
        "Googlebot/2.1",
        "facebookexternalhit/1.1",
        "TelegramBot (like TwitterBot)",
        "WhatsApp/2.23.0",
    ):
        rv = client.get(f"/project/{slug}", headers={"User-Agent": ua})
        assert rv.status_code == 200
    assert _page_views(slug) == before


def test_manage_backers_shows_stats_tiles(client):
    client.post(
        "/login",
        data={
            "email": "yacov@drori.org",
            "password": "A-very-strong-admin-password-2026!",
        },
    )
    # Seed a couple of human views so the tile is non-zero.
    client.get(
        "/project/or-latefila",
        headers={"User-Agent": "Mozilla/5.0 (compatible; HeadFundTest/1.0)"},
    )
    client.get(
        "/project/or-latefila",
        headers={"User-Agent": "Mozilla/5.0 (compatible; HeadFundTest/1.0)"},
    )

    rv = client.get("/project/or-latefila/manage/backers")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8")

    assert 'data-stat="page-views"' in body
    assert 'data-stat="backers-count"' in body
    assert 'data-stat="raised-goal"' in body
    assert 'data-stat="raised-percent"' in body
    assert 'data-stat="average-pledge"' in body
    assert 'data-stat="payments-split"' in body
    assert 'data-stat="days-left"' in body

    assert "צפיות בדף" in body
    assert 'סה"כ תומכים' in body
    assert "גויס / יעד" in body
    assert "ממוצע תרומה" in body
    assert "תשלומים הושלמו / ממתינים" in body
    assert "ימים שנותרו" in body

    views = _page_views("or-latefila")
    assert views >= 2
    assert f'data-stat="page-views">{views}</div>' in body.replace(",", "") or str(views) in body
