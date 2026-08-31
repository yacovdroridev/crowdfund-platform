import os
import tempfile
import pytest
from app import app
from db import init_db, seed_db, get_db

@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()
    os.environ["DATABASE_PATH"] = db_path
    os.environ["ADMIN_EMAIL"] = "yacov@drori.org"
    os.environ["ADMIN_INITIAL_PASSWORD"] = "A-very-strong-admin-password-2026!"
    
    # Reload DB config
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

    # Submit a pledge with Bit
    post_data = {
        'reward_id': '1',
        'amount': '50',
        'tip_amount': '10',
        'backer_name': 'דניאל כהן',
        'backer_email': 'daniel@example.com',
        'backer_phone': '050-9998877',
        'payment_method': 'bit',
        'greeting_message': 'בהצלחה רבה לפרויקט!',
        'shipping_address': 'הרצל 1, תל אביב'
        ,'legal_accept': 'on'
    }
    rv = client.post('/project/synapse-guardian-iot/pledge', data=post_data, follow_redirects=True)
    assert rv.status_code == 200
    assert "תודה ענקית על תמיכתך".encode('utf-8') in rv.data
    assert "ביט (Bit)".encode('utf-8') in rv.data

    # Pending payment requests are not counted before a verified provider webhook.
    rv = client.get('/api/projects/synapse-guardian-iot')
    assert rv.json['project']['current_amount'] == initial_amount
    assert rv.json['project']['backers_count'] == initial_backers

def test_create_project(client):
    client.post('/register', data={
        'full_name': 'יעקב דרורי', 'email': 'creator@example.com', 'phone': '0501234567',
        'password': 'Strong-creator-password-2026!',
        'password_confirm': 'Strong-creator-password-2026!', 'legal_accept': 'on'
    })
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
        'reward_limit[]': ['', '50'],
        'legal_accept': 'on'
    }
    rv = client.post('/create', data=post_data, follow_redirects=True)
    assert rv.status_code == 200
    assert "נשלח לאישור מנהל".encode('utf-8') in rv.data

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

    # Login with the provisioned admin account
    rv = client.post('/login', data={
        'email': 'yacov@drori.org',
        'password': 'A-very-strong-admin-password-2026!'
    }, follow_redirects=True)
    assert rv.status_code == 200
    assert "לוח ניהול ומעקב גיוסים".encode('utf-8') in rv.data

def test_edit_project_security(client):
    # Unauthenticated user visiting edit is redirected to account login.
    rv = client.get('/project/synapse-guardian-iot/edit')
    assert rv.status_code == 302
    assert '/login' in rv.location

    # The administrator can edit without a legacy project PIN.
    rv = client.post('/login', data={
        'email': 'yacov@drori.org',
        'password': 'A-very-strong-admin-password-2026!'
    }, follow_redirects=True)
    assert rv.status_code == 200
    rv = client.get('/project/synapse-guardian-iot/edit')
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



def _latest_pledge():
    from db import get_db
    conn = get_db()
    row = conn.execute("SELECT * FROM pledges ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def test_pledge_success_missing_returns_404(client):
    rv = client.get("/success/999999")
    assert rv.status_code == 404


def test_submit_pledge_credit_card_is_rejected(client):
    from db import get_db
    conn = get_db()
    before = conn.execute("SELECT COUNT(*) AS c FROM pledges").fetchone()["c"]
    conn.close()
    rv = client.post(
        "/project/synapse-guardian-iot/pledge",
        data={
            "reward_id": "1",
            "amount": "50",
            "tip_amount": "0",
            "backer_name": "Test Card",
            "backer_email": "card@example.com",
            "payment_method": "credit_card",
            "legal_accept": "on",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "אמצעי התשלום שנבחר אינו נתמך במערכת".encode("utf-8") in rv.data
    conn = get_db()
    after = conn.execute("SELECT COUNT(*) AS c FROM pledges").fetchone()["c"]
    conn.close()
    assert after == before


def test_submit_pledge_google_pay_stays_pending(client, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("network disabled")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    rv = client.post(
        "/project/synapse-guardian-iot/pledge",
        data={
            "reward_id": "1",
            "amount": "50",
            "tip_amount": "0",
            "backer_name": "Test Google Pay",
            "backer_email": "gpay@example.com",
            "payment_method": "google_pay",
            "legal_accept": "on",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "תודה ענקית על תמיכתך".encode("utf-8") in rv.data
    pledge = _latest_pledge()
    assert pledge["payment_method"] == "google_pay"
    assert pledge["payment_status"] == "pending"
    assert pledge["is_payment_verified"] == 0


def test_submit_pledge_upay_missing_credentials(client, monkeypatch):
    monkeypatch.delenv("SUMIT_COMPANY_ID", raising=False)
    monkeypatch.delenv("SUMIT_API_KEY", raising=False)
    rv = client.post(
        "/project/synapse-guardian-iot/pledge",
        data={
            "reward_id": "1",
            "amount": "50",
            "tip_amount": "0",
            "backer_name": "Test Upay",
            "backer_email": "upay@example.com",
            "payment_method": "upay",
            "legal_accept": "on",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "חסר מזהה חברה או מפתח API".encode("utf-8") in rv.data
    pledge = _latest_pledge()
    assert pledge["payment_method"] == "upay"
    assert pledge["payment_status"] == "pending"
    assert pledge["is_payment_verified"] == 0


def test_submit_pledge_upay_redirects_to_hosted_page(client, monkeypatch):
    import json
    monkeypatch.setenv("SUMIT_COMPANY_ID", "12345")
    monkeypatch.setenv("SUMIT_API_KEY", "test-key")

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({
                "Status": "Success",
                "Data": {"RedirectURL": "https://pay.sumit.co.il/r/abc"},
            }).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    rv = client.post(
        "/project/synapse-guardian-iot/pledge",
        data={
            "reward_id": "1",
            "amount": "80",
            "tip_amount": "0",
            "backer_name": "Sumit User",
            "backer_email": "sumit@example.com",
            "payment_method": "upay",
            "legal_accept": "on",
        },
        follow_redirects=False,
    )
    assert rv.status_code == 302
    assert rv.location == "https://pay.sumit.co.il/r/abc"
    pledge = _latest_pledge()
    assert pledge["payment_status"] == "pending"


def test_sumit_callback_marks_completed(client, monkeypatch):
    monkeypatch.delenv("SUMIT_COMPANY_ID", raising=False)
    monkeypatch.delenv("SUMIT_API_KEY", raising=False)
    client.post(
        "/project/synapse-guardian-iot/pledge",
        data={
            "reward_id": "1",
            "amount": "50",
            "tip_amount": "0",
            "backer_name": "Webhook User",
            "backer_email": "hook@example.com",
            "payment_method": "upay",
            "legal_accept": "on",
        },
    )
    pledge = _latest_pledge()
    assert pledge["payment_status"] == "pending"
    rv = client.post(
        "/payment/sumit/callback",
        json={
            "Payment": {"Status": "000", "ID": "SUMIT-99"},
            "Customer": {"ExternalIdentifier": pledge["transaction_id"]},
        },
    )
    assert rv.status_code == 200
    from db import get_db
    conn = get_db()
    updated = conn.execute("SELECT payment_status, is_payment_verified, payment_reference FROM pledges WHERE id = ?", (pledge["id"],)).fetchone()
    conn.close()
    assert updated["payment_status"] == "completed"
    assert updated["is_payment_verified"] == 1
    assert updated["payment_reference"] == "SUMIT-99"


def test_sumit_return_valid_completes(client, monkeypatch):
    monkeypatch.delenv("SUMIT_COMPANY_ID", raising=False)
    monkeypatch.delenv("SUMIT_API_KEY", raising=False)
    client.post(
        "/project/synapse-guardian-iot/pledge",
        data={
            "reward_id": "1",
            "amount": "50",
            "tip_amount": "0",
            "backer_name": "Return User",
            "backer_email": "return@example.com",
            "payment_method": "upay",
            "legal_accept": "on",
        },
    )
    pledge = _latest_pledge()
    rv = client.get(
        f"/payment/sumit/return?pledge_id={pledge['id']}&Valid=1&ID=888",
        follow_redirects=False,
    )
    assert rv.status_code == 302
    assert f"/success/{pledge['id']}" in rv.location
    from db import get_db
    conn = get_db()
    updated = conn.execute("SELECT payment_status, is_payment_verified FROM pledges WHERE id = ?", (pledge["id"],)).fetchone()
    conn.close()
    assert updated["payment_status"] == "completed"
    assert updated["is_payment_verified"] == 1


def _set_gateway_enabled(key, enabled):
    from db import get_db
    conn = get_db()
    conn.execute(
        "UPDATE payment_gateways SET is_enabled = ? WHERE gateway_key = ?",
        (1 if enabled else 0, key),
    )
    conn.commit()
    conn.close()


def test_disabled_google_pay_hidden_on_checkout_and_project(client):
    _set_gateway_enabled("google_pay", False)
    checkout = client.get("/project/synapse-guardian-iot/checkout")
    assert checkout.status_code == 200
    assert b'id="co-btn-google_pay"' not in checkout.data
    assert b'id="co-pane-google_pay"' not in checkout.data
    assert b'id="co-btn-upay"' in checkout.data
    assert b'id="co-payment-method" value="upay"' in checkout.data

    project = client.get("/project/synapse-guardian-iot")
    assert project.status_code == 200
    assert b'id="btn-pay-google_pay"' not in project.data
    assert b'id="pane-google_pay"' not in project.data
    assert b'id="btn-pay-upay"' in project.data


def test_enabled_google_pay_shown_on_checkout_and_project(client):
    _set_gateway_enabled("google_pay", True)
    checkout = client.get("/project/synapse-guardian-iot/checkout")
    assert checkout.status_code == 200
    assert b'id="co-btn-google_pay"' in checkout.data
    assert b'id="co-pane-google_pay"' in checkout.data
    project = client.get("/project/synapse-guardian-iot")
    assert project.status_code == 200
    assert b'id="btn-pay-google_pay"' in project.data
    assert b'id="pane-google_pay"' in project.data


def test_submit_pledge_disabled_google_pay_rejected(client):
    from db import get_db
    _set_gateway_enabled("google_pay", False)
    conn = get_db()
    before = conn.execute("SELECT COUNT(*) AS c FROM pledges").fetchone()["c"]
    conn.close()
    rv = client.post(
        "/project/synapse-guardian-iot/pledge",
        data={
            "reward_id": "1",
            "amount": "50",
            "tip_amount": "0",
            "backer_name": "Disabled GPay",
            "backer_email": "disabled-gpay@example.com",
            "payment_method": "google_pay",
            "legal_accept": "on",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert "אמצעי התשלום שנבחר מבוטל כרגע במערכת".encode("utf-8") in rv.data
    conn = get_db()
    after = conn.execute("SELECT COUNT(*) AS c FROM pledges").fetchone()["c"]
    conn.close()
    assert after == before


def test_checkout_defaults_to_first_enabled_when_upay_off(client):
    _set_gateway_enabled("upay", False)
    rv = client.get("/project/synapse-guardian-iot/checkout")
    assert rv.status_code == 200
    assert b'id="co-btn-upay"' not in rv.data
    assert b'id="co-btn-google_pay"' in rv.data
    assert b'id="co-payment-method" value="google_pay"' in rv.data
    project = client.get("/project/synapse-guardian-iot")
    assert project.status_code == 200
    assert b'id="btn-pay-upay"' not in project.data
    assert b'id="selected-payment-method" value="google_pay"' in project.data


def test_checkout_unavailable_when_no_gateways_enabled(client):
    from db import get_db
    conn = get_db()
    conn.execute("UPDATE payment_gateways SET is_enabled = 0")
    conn.commit()
    conn.close()
    checkout = client.get("/project/synapse-guardian-iot/checkout")
    assert checkout.status_code == 200
    assert "התשלום אינו זמין כרגע".encode("utf-8") in checkout.data
    assert b'id="btn-co-submit"' not in checkout.data
    assert b'id="co-btn-upay"' not in checkout.data
    assert b'id="co-btn-google_pay"' not in checkout.data
    project = client.get("/project/synapse-guardian-iot")
    assert project.status_code == 200
    assert "התשלום אינו זמין כרגע".encode("utf-8") in project.data
    assert b'id="btn-submit-pledge"' not in project.data
