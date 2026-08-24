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
        # Log in as admin
        client.post('/login', data={'email': 'yacov@drori.org', 'password': 'A-very-strong-admin-password-2026!'})
        yield client

    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)

def test_manage_backers_dashboard_access(client):
    rv = client.get('/project/or-latefila/manage/backers')
    assert rv.status_code == 200
    assert "👥 ניהול תורמים ותשורות".encode('utf-8') in rv.data
    assert "דניאל כהן".encode('utf-8') in rv.data

def test_filter_backers_by_payment_method(client):
    rv = client.get('/project/or-latefila/manage/backers?payment_method=bit')
    assert rv.status_code == 200
    assert "מיכל לוי".encode('utf-8') in rv.data

def test_verify_bit_payment(client):
    # Fetch first pending bit pledge id
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM pledges WHERE payment_method = 'bit' AND is_payment_verified = 0 LIMIT 1")
    pledge = cursor.fetchone()
    conn.close()

    assert pledge is not None
    pledge_id = pledge['id']

    rv = client.post(f'/project/or-latefila/manage/backers/{pledge_id}/update-status', data={
        'action_type': 'verify_payment',
        'payment_reference': 'BIT-TEST-REF-100'
    }, follow_redirects=True)
    
    assert rv.status_code == 200
    assert "התשלום בביט/פייבוקס אושר בהצלחה".encode('utf-8') in rv.data

    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_payment_verified, payment_status, payment_reference FROM pledges WHERE id = ?", (pledge_id,))
    updated = cursor.fetchone()
    conn.close()

    assert updated['is_payment_verified'] == 1
    assert updated['payment_status'] == 'completed'
    assert updated['payment_reference'] == 'BIT-TEST-REF-100'

def test_update_fulfillment_status(client):
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM pledges LIMIT 1")
    pledge_id = cursor.fetchone()['id']
    conn.close()

    rv = client.post(f'/project/or-latefila/manage/backers/{pledge_id}/update-status', data={
        'action_type': 'fulfillment',
        'fulfillment_status': 'shipped',
        'fulfillment_notes': 'RR123456789IL'
    }, follow_redirects=True)

    assert rv.status_code == 200
    assert "סטטוס אספקת התשורה עודכן".encode('utf-8') in rv.data

    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT fulfillment_status, fulfillment_notes FROM pledges WHERE id = ?", (pledge_id,))
    updated = cursor.fetchone()
    conn.close()

    assert updated['fulfillment_status'] == 'shipped'
    assert updated['fulfillment_notes'] == 'RR123456789IL'

def test_print_shipping_labels(client):
    rv = client.get('/project/or-latefila/manage/backers/labels')
    assert rv.status_code == 200
    assert "מדבקות משלוח להדפסה".encode('utf-8') in rv.data

def test_paypal_sandbox_execution(client):
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM pledges WHERE payment_method = 'paypal' OR payment_status = 'pending' LIMIT 1")
    pledge_id = cursor.fetchone()['id']
    conn.close()

    rv = client.post('/checkout/paypal/execute', data={'pledge_id': pledge_id}, follow_redirects=True)
    assert rv.status_code == 200
    assert "תשלום ה-PayPal (Sandbox) אושר בהצלחה".encode('utf-8') in rv.data
