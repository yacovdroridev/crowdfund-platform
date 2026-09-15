import os
import tempfile
import pytest
from pathlib import Path
from app import app
from db import init_db, seed_db

@pytest.fixture
def client():
    fd, path = tempfile.mkstemp()
    os.environ["DATABASE_PATH"] = path
    import db
    db.DB_PATH = path
    init_db(); seed_db()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    os.close(fd)
    if os.path.exists(path): os.unlink(path)

def test_robots_and_sitemap(client):
    robots = client.get('/robots.txt')
    assert robots.status_code == 200
    assert b'Sitemap: https://headfundcoil.com/sitemap.xml' in robots.data
    sitemap = client.get('/sitemap.xml')
    assert sitemap.status_code == 200
    assert b'<urlset' in sitemap.data
    assert b'/project/synapse-guardian-iot' in sitemap.data


def test_trust_pages_and_legacy_redirects(client):
    for path in ('/about', '/how-it-works', '/faq'):
        assert client.get(path).status_code == 200
    assert client.get('/privacy').status_code == 301
    assert client.get('/terms').status_code == 301
    assert client.get('/accessibility').status_code == 301


def test_measurement_is_config_driven(client, monkeypatch):
    import app
    monkeypatch.setattr(app, 'GA_MEASUREMENT_ID', 'G-TEST123')
    monkeypatch.setattr(app, 'META_PIXEL_ID', '999')
    html = client.get('/').data.decode('utf-8')
    assert 'G-TEST123' in html
    assert "fbq('init', \"999\")" in html


def test_public_page_does_not_load_cropper(client):
    assert 'cropper.min.js' not in client.get('/').data.decode('utf-8')
    assert 'cropper.min.js' in Path('templates/create.html').read_text()
