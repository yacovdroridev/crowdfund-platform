import os
import tempfile
import pytest
from app import app
from db import init_db, seed_db, get_db

@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()
    os.environ["DATABASE_PATH"] = db_path
    
    # Reload DB config
    import db
    db.DB_PATH = db_path
    init_db()
    seed_db()

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)

def test_home_page(client):
    rv = client.get('/')
    assert rv.status_code == 200
    assert b"HeadFund" in rv.data
    assert "SynApse Guardian".encode('utf-8') in rv.data

def test_category_filter(client):
    rv = client.get('/?category=technology')
    assert rv.status_code == 200
    assert "SynApse Guardian".encode('utf-8') in rv.data

def test_project_detail(client):
    rv = client.get('/project/synapse-guardian-iot')
    assert rv.status_code == 200
    assert "SynApse Guardian".encode('utf-8') in rv.data
    assert "בחרו מדרגת תמיכה ותשורה".encode('utf-8') in rv.data

def test_submit_pledge(client):
    # Get initial amount
    rv = client.get('/api/projects/synapse-guardian-iot')
    initial_amount = rv.json['project']['current_amount']
    initial_backers = rv.json['project']['backers_count']

    # Submit a pledge
    post_data = {
        'reward_id': '1',
        'amount': '50',
        'tip_amount': '10',
        'backer_name': 'דניאל כהן',
        'backer_email': 'daniel@example.com',
        'backer_phone': '050-9998877',
        'greeting_message': 'בהצלחה רבה לפרויקט!',
        'shipping_address': 'הרצל 1, תל אביב'
    }
    rv = client.post('/project/synapse-guardian-iot/pledge', data=post_data, follow_redirects=True)
    assert rv.status_code == 200
    assert "תודה ענקית על תמיכתך".encode('utf-8') in rv.data

    # Check updated stats
    rv = client.get('/api/projects/synapse-guardian-iot')
    assert rv.json['project']['current_amount'] == initial_amount + 60.0
    assert rv.json['project']['backers_count'] == initial_backers + 1

def test_create_project(client):
    post_data = {
        'title': 'רובוט גינון אוטונומי',
        'subtitle': 'מערכת רובוטית חכמה להשקיה וגיזום אוטונומי לגינה',
        'category': 'technology',
        'goal_amount': '80000',
        'days_total': '30',
        'creator_name': 'יעקב דרורי',
        'creator_email': 'yacov@drori.org',
        'creator_phone': '054-9103046',
        'creator_bio': 'מהנדס מערכות',
        'creator_avatar': 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200',
        'cover_image': 'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200',
        'story_html': '<p>סיפור הרובוט האוטונומי</p>',
        'reward_title[]': ['תומך ראשון', 'ערכת רובוט שלמה'],
        'reward_amount[]': ['100', '1500'],
        'reward_desc[]': ['תודה רבה', 'רובוט מלא'],
        'reward_delivery[]': ['ינואר 2027', 'מרץ 2027'],
        'reward_limit[]': ['', '50']
    }
    rv = client.post('/create', data=post_data, follow_redirects=True)
    assert rv.status_code == 200
    assert "רובוט גינון אוטונומי".encode('utf-8') in rv.data

def test_api_endpoints(client):
    # API stats
    rv = client.get('/api/stats')
    assert rv.status_code == 200
    assert rv.json['success'] is True
    assert rv.json['stats']['total_projects'] >= 3

    # API projects list
    rv = client.get('/api/projects')
    assert rv.status_code == 200
    assert len(rv.json['projects']) >= 3

def test_dashboard_protection(client):
    # Unauthenticated should redirect to login
    rv = client.get('/dashboard')
    assert rv.status_code == 302
    assert '/login' in rv.location

    # Login with admin password
    rv = client.post('/login', data={'password': 'drori2026'}, follow_redirects=True)
    assert rv.status_code == 200
    assert "לוח ניהול ומעקב גיוסים".encode('utf-8') in rv.data

def test_edit_project_security(client):
    # Unauthenticated user visiting edit is redirected to auth
    rv = client.get('/project/synapse-guardian-iot/edit')
    assert rv.status_code == 302
    assert '/project/synapse-guardian-iot/auth' in rv.location

    # Submit wrong PIN
    rv = client.post('/project/synapse-guardian-iot/auth', data={'auth_key': 'wrongpin'}, follow_redirects=True)
    assert "קוד PIN או סיסמה שגויים".encode('utf-8') in rv.data

    # Submit correct PIN
    rv = client.post('/project/synapse-guardian-iot/auth', data={'auth_key': '202601'}, follow_redirects=True)
    assert rv.status_code == 200
    assert "עריכת פרטי הפרויקט".encode('utf-8') in rv.data

    # Now authorized to post edits
    post_data = {
        'title': 'SynApse Guardian: מכשיר הגנה מעודכן',
        'subtitle': 'תקציר מעודכן לבדיקה',
        'category': 'technology',
        'goal_amount': '150000',
        'creator_name': 'יעקב דרורי',
        'creator_email': 'yacov@drori.org',
        'creator_phone': '054-9103046',
        'creator_bio': 'מהנדס מערכות ומפתח פתרונות AI',
        'creator_avatar': 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200',
        'cover_image': 'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200',
        'story_html': '<p>סיפור מעודכן</p>',
        'reward_title[]': ['תשורה מעודכנת'],
        'reward_amount[]': ['120'],
        'reward_desc[]': ['תיאור מעודכן'],
        'reward_delivery[]': ['דצמבר 2026'],
        'reward_limit[]': ['100']
    }
    rv = client.post('/project/synapse-guardian-iot/edit', data=post_data, follow_redirects=True)
    assert rv.status_code == 200
    assert "מכשיר הגנה מעודכן".encode('utf-8') in rv.data

