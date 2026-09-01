import os
import re
import json
import uuid
import secrets
import hashlib
import base64
import bleach
from urllib.parse import quote_plus
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, abort, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from db import get_db, init_db, seed_db, sync_project_states


def session_cookie_should_be_secure():
    """Use secure cookies on Render unless an explicit override is supplied."""
    value = os.environ.get("SESSION_COOKIE_SECURE")
    if value is None:
        value = os.environ.get("RENDER", "0")
    return value.strip().lower() in {"1", "true", "yes", "on"}


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
app.secret_key = os.environ.get("SECRET_KEY") or "headfund-platform-secret-key-2026-production-secure"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=session_cookie_should_be_secure(),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    CSRF_ENABLED=True,
)
LEGAL_CONTACT_EMAIL = os.environ.get("LEGAL_CONTACT_EMAIL", "support@headfund.co.il")


@app.after_request
def add_security_headers(response):
    """Apply browser-side security defaults to every HTML/API response."""
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

LEGAL_DOCUMENTS = {
    "privacy": ("מדיניות פרטיות", "legal/privacy.html"),
    "terms": ("תנאי שימוש", "legal/terms.html"),
    "creators": ("תנאים ליוצרי קמפיינים", "legal/creators.html"),
    "supporters": ("תנאים לתומכים ומדיניות תשלומים", "legal/supporters.html"),
    "content": ("כללי תוכן וקניין רוחני", "legal/content.html"),
    "cookies": ("מדיניות Cookies", "legal/cookies.html"),
    "accessibility": ("הצהרת נגישות", "legal/accessibility.html"),
}

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute(
        "SELECT id, email, full_name, phone, role, is_active, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(user) if user and user["is_active"] else None


def is_admin():
    user = current_user()
    return bool(user and user["role"] == "admin")


def is_project_authorized(slug):
    if is_admin():
        return True
    user = current_user()
    if not user:
        return False
    conn = get_db()
    owned = conn.execute(
        "SELECT 1 FROM projects WHERE slug = ? AND (owner_user_id = ? OR (owner_user_id IS NULL AND LOWER(creator_email) = LOWER(?)))",
        (slug, user["id"], user["email"]),
    ).fetchone()
    conn.close()
    return bool(owned)


def authorize_project(slug):
    # Kept temporarily for backward-compatible call sites. Authorization is
    # now derived from project ownership or the admin role, never from a PIN.
    return is_project_authorized(slug)

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

def save_uploaded_image(file_storage):
    if not file_storage or not file_storage.filename or not allowed_file(file_storage.filename):
        return None
    try:
        data = file_storage.read()
        ext = file_storage.filename.rsplit('.', 1)[1].lower()
        mime = "image/svg+xml" if ext == "svg" else f"image/{ext}"
        b64 = base64.b64encode(data).decode('utf-8')
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"Error encoding uploaded image: {e}")
        return None

def save_base64_image(base64_str):
    if not base64_str or not base64_str.startswith('data:image'):
        return None
    return base64_str

def format_youtube_embed(url):
    if not url or not str(url).strip() or str(url).strip().lower() in ('none', 'null'):
        return None
    url = str(url).strip()
    if 'youtube.com/embed/' in url:
        return url
    shorts_match = re.search(r'youtube\.com/shorts/([a-zA-Z0-9_-]+)', url)
    if shorts_match:
        return f"https://www.youtube.com/embed/{shorts_match.group(1)}"
    watch_match = re.search(r'(?:youtube\.com/(?:watch\?v=|v/)|youtu\.be/)([a-zA-Z0-9_-]+)', url)
    if watch_match:
        return f"https://www.youtube.com/embed/{watch_match.group(1)}"
    return url


def sanitize_story_html(value):
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", "", value or "", flags=re.IGNORECASE | re.DOTALL)
    return bleach.clean(
        value,
        tags={"p", "br", "strong", "em", "ul", "ol", "li", "h2", "h3", "blockquote", "a"},
        attributes={"a": ["href", "title", "target", "rel"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.before_request
def enforce_csrf():
    if request.path.startswith("/payment/"):
        return
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and app.config.get('CSRF_ENABLED', True):
        supplied = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token', '')
        expected = session.get('_csrf_token', '')
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            abort(400, description='Invalid CSRF token')


@app.errorhandler(400)
def handle_bad_request(e):
    desc = getattr(e, 'description', str(e))
    if "CSRF" in str(desc):
        session.pop('_csrf_token', None)
        flash("פג תוקף אבטחת הטופס (CSRF). אנא רענן את הדף ונסה להתחבר שוב.", "error")
        if request.path == '/login':
            return render_template('login.html', next_url=request.values.get('next', '')), 400
    return f"Bad Request: {desc}", 400


@app.context_processor
def inject_auth_context():
    return {
        'csrf_token': csrf_token(),
        'is_admin': is_admin(),
        'current_user': current_user(),
        'authorized_projects': [],
        'legal_contact_email': LEGAL_CONTACT_EMAIL,
        'legal_documents': LEGAL_DOCUMENTS,
    }

def log_action(action, target_type, target_id=None, details=None, conn=None):
    """Insert a privacy-conscious admin/user action into the audit log.

    Pass the caller's open SQLite connection when inside a write transaction;
    otherwise this helper opens (and closes) its own connection.
    """
    actor_user_id = session.get("user_id")
    close_at_end = conn is None
    if conn is None:
        conn = get_db()
    try:
        conn.execute(
            """INSERT INTO audit_log (actor_user_id, action, target_type, target_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (actor_user_id, action, target_type, target_id, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        if close_at_end:
            conn.commit()
    except Exception as e:
        print(f"audit log skipped: {e}")
    finally:
        if close_at_end:
            conn.close()


def _sumit_credentials(cursor=None):
    """Resolve Sumit CompanyID + APIKey from env and/or payment_gateways.upay. Never invent keys."""
    company_id = (os.environ.get("SUMIT_COMPANY_ID") or "").strip()
    api_key = (os.environ.get("SUMIT_API_KEY") or "").strip()
    ident = ""
    if cursor is not None:
        row = cursor.execute(
            "SELECT account_identifier FROM payment_gateways WHERE gateway_key = 'upay'"
        ).fetchone()
        if row and row["account_identifier"]:
            ident = str(row["account_identifier"]).strip()
    if ident:
        if ident.startswith("{") and ident.endswith("}"):
            try:
                parsed = json.loads(ident)
                company_id = company_id or str(parsed.get("CompanyID") or parsed.get("company_id") or "").strip()
                api_key = api_key or str(parsed.get("APIKey") or parsed.get("api_key") or "").strip()
            except (ValueError, TypeError):
                pass
        elif ":" in ident:
            left, right = ident.split(":", 1)
            company_id = company_id or left.strip()
            api_key = api_key or right.strip()
        elif ident.isdigit():
            company_id = company_id or ident
        elif company_id and not api_key:
            api_key = ident
    if not company_id or not api_key:
        return None
    try:
        return {"CompanyID": int(company_id), "APIKey": api_key}
    except (TypeError, ValueError):
        return None


def _sumit_status_is_success(status=None, valid=None):
    if valid is not None and str(valid).strip() != "":
        valid_s = str(valid).strip().lower()
        if valid_s in {"1", "true", "yes", "ok"}:
            return True
        if valid_s in {"0", "false", "no"}:
            return False
    if status is None or str(status).strip() == "":
        return False
    return str(status).strip() in {"000", "0"}


def _find_sumit_pledge(cursor, pledge_id=None, transaction_id=None, payment_id=None):
    if pledge_id and str(pledge_id).isdigit():
        row = cursor.execute("SELECT * FROM pledges WHERE id = ?", (int(pledge_id),)).fetchone()
        if row:
            return row
    if transaction_id:
        row = cursor.execute("SELECT * FROM pledges WHERE transaction_id = ?", (transaction_id,)).fetchone()
        if row:
            return row
    if payment_id:
        row = cursor.execute("SELECT * FROM pledges WHERE payment_reference = ?", (str(payment_id),)).fetchone()
        if row:
            return row
    return None


def _complete_pending_pledge(cursor, conn, pledge, reference):
    if not pledge or pledge["payment_status"] == "completed":
        return False
    cursor.execute(
        "UPDATE pledges SET payment_status = 'completed', is_payment_verified = 1, payment_reference = ? WHERE id = ?",
        (reference, pledge["id"]),
    )
    cursor.execute(
        "UPDATE projects SET current_amount = current_amount + ?, backers_count = backers_count + 1 WHERE id = ?",
        (pledge["amount"], pledge["project_id"]),
    )
    if pledge["reward_id"]:
        cursor.execute("UPDATE rewards SET quantity_claimed = quantity_claimed + 1 WHERE id = ?", (pledge["reward_id"],))
    sync_project_states(conn)
    conn.commit()
    return True



def load_enabled_payment_gateways(cursor):
    """Enabled public checkout methods. Default prefers upay, else first enabled."""
    cursor.execute("SELECT * FROM payment_gateways WHERE is_enabled = 1 ORDER BY id ASC")
    gateways = [dict(g) for g in cursor.fetchall()]
    enabled_keys = [g["gateway_key"] for g in gateways]
    if "upay" in enabled_keys:
        default_method = "upay"
    elif enabled_keys:
        default_method = enabled_keys[0]
    else:
        default_method = ""
    return gateways, enabled_keys, default_method


def _sumit_payload_fields(source):
    """Pull Sumit return/webhook identifiers from form, query, or JSON."""
    source = source or {}
    payment = source.get("Payment") if isinstance(source.get("Payment"), dict) else {}
    customer = source.get("Customer") if isinstance(source.get("Customer"), dict) else {}
    data = source.get("Data") if isinstance(source.get("Data"), dict) else {}
    nested_payment = data.get("Payment") if isinstance(data.get("Payment"), dict) else {}
    return {
        "pledge_id": source.get("pledge_id") or data.get("pledge_id"),
        "transaction_id": (
            source.get("ExternalIdentifier")
            or source.get("Identifier")
            or source.get("transaction_id")
            or customer.get("ExternalIdentifier")
            or data.get("ExternalIdentifier")
        ),
        "payment_id": (
            source.get("ID")
            or source.get("PaymentID")
            or payment.get("ID")
            or nested_payment.get("ID")
            or data.get("PaymentID")
            or data.get("ID")
        ),
        "status": (
            source.get("Payment.Status")
            or payment.get("Status")
            or nested_payment.get("Status")
            or data.get("PaymentStatus")
            or source.get("sale_status")
        ),
        "valid": (
            source.get("Valid")
            or payment.get("ValidPayment")
            or nested_payment.get("ValidPayment")
            or data.get("Valid")
        ),
    }


# Categories definition
CATEGORIES = {
    "all": "כל הקטגוריות",
    "technology": "טכנולוגיה וחדשנות",
    "art_culture": "אמנות וספרות",
    "music": "מוזיקה והופעות",
    "community": "חברה וקהילה",
    "games": "משחקים ודיגיטל",
    "food": "קולינריה ומזון"
}


def get_category_map(include_all=False, include_inactive=False):
    conn = get_db()
    query = "SELECT slug, name FROM categories"
    if not include_inactive:
        query += " WHERE is_active = 1"
    query += " ORDER BY id, name"
    rows = conn.execute(query).fetchall()
    conn.close()
    categories = {row['slug']: row['name'] for row in rows}
    if include_all:
        return {"all": "כל הקטגוריות", **categories}
    return categories

def calculate_project_metrics(project):
    p = dict(project)
    
    # Progress percentage
    goal = float(p.get("goal_amount", 1) or 1)
    current = float(p.get("current_amount", 0) or 0)
    percent = int((current / goal) * 100) if goal > 0 else 0
    p["percent"] = percent
    
    # Days left calculation
    try:
        end_date = datetime.strptime(p["end_date"], "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        delta = end_date - now
        days_left = max(0, delta.days)
        p["days_left"] = days_left
        p["is_expired"] = delta.total_seconds() <= 0
    except Exception:
        p["days_left"] = 0
        p["is_expired"] = False

    p["category_label"] = CATEGORIES.get(p.get("category"), "כללי")
    raw_video = p.get("video_url")
    if raw_video and str(raw_video).strip() and str(raw_video).strip().lower() not in ('none', 'null'):
        p["video_url"] = str(raw_video).strip()
        p["video_embed_url"] = format_youtube_embed(str(raw_video).strip())
    else:
        p["video_url"] = None
        p["video_embed_url"] = None

    main_media_type = p.get('main_media_type', 'auto') or 'auto'
    p['main_media_type'] = main_media_type
    if main_media_type == 'image':
        p['show_video'] = False
    else:
        p['show_video'] = bool(p.get('video_embed_url'))

    return p

# --- HTML Routes ---

@app.route('/legal', strict_slashes=False)
def legal_center():
    return render_template('legal/index.html')


@app.route('/legal/<document>')
def legal_document(document):
    item = LEGAL_DOCUMENTS.get(document)
    if not item:
        abort(404)
    title, template_name = item
    return render_template(template_name, legal_title=title)

@app.route('/')
def index():
    log_action("home_view", "page")
    status_filter = request.args.get('status', 'all')
    log_action("home_view", "page")
    category = request.args.get('category', 'all')
    status_filter = request.args.get('status', 'all')
    search_query = request.args.get('q', '').strip()
    categories = get_category_map(include_all=True)
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM projects WHERE is_active = 1"
    params = []

    if category != 'all' and category in categories:
        query += " AND category = ?"
        params.append(category)

    if status_filter == 'active':
        query += " AND status = 'active'"
    elif status_filter == 'successful':
        query += " AND (status = 'successful' OR current_amount >= goal_amount)"

    if search_query:
        query += " AND (title LIKE ? OR subtitle LIKE ? OR creator_name LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])

    query += " ORDER BY id DESC"
    cursor.execute(query, params)
    raw_projects = cursor.fetchall()
    conn.close()

    projects = [calculate_project_metrics(p) for p in raw_projects]

    # Calculate overall platform stats
    total_funded = sum(p["current_amount"] for p in projects)
    total_backers = sum(p["backers_count"] for p in projects)
    total_projects = len(projects)

    return render_template(
        'index.html',
        projects=projects,
        categories=categories,
        selected_category=category,
        selected_status=status_filter,
        search_query=search_query,
        total_funded=total_funded,
        total_backers=total_backers,
        total_projects=total_projects
    )

@app.route('/project/<slug>')
def project_detail(slug):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    raw_project = cursor.fetchone()

    if not raw_project or (not raw_project['is_active'] and not is_project_authorized(slug)):
        conn.close()
        abort(404)
    log_action("project_view", "project", raw_project["id"], details=f"slug={slug}")
    project = calculate_project_metrics(raw_project)
    project = calculate_project_metrics(raw_project)

    # Get Rewards / Tiers
    cursor.execute("SELECT * FROM rewards WHERE project_id = ? ORDER BY amount ASC", (project["id"],))
    rewards = [dict(r) for r in cursor.fetchall()]

    # Get Updates
    cursor.execute("SELECT * FROM updates WHERE project_id = ? ORDER BY created_at DESC", (project["id"],))
    updates = [dict(u) for u in cursor.fetchall()]

    # Get Comments
    cursor.execute("SELECT * FROM comments WHERE project_id = ? ORDER BY created_at DESC", (project["id"],))
    comments = [dict(c) for c in cursor.fetchall()]

    # Get Recent Backers
    cursor.execute("""
        SELECT backer_name, amount, is_anonymous, greeting_message, created_at 
        FROM pledges 
        WHERE project_id = ? AND payment_status = 'completed'
        ORDER BY id DESC LIMIT 20
    """, (project["id"],))
    backers = [dict(b) for b in cursor.fetchall()]

    is_auth = is_project_authorized(slug)
    gateways, enabled_gateway_keys, default_payment_method = load_enabled_payment_gateways(cursor)
    conn.close()

    return render_template(
        'project.html',
        project=project,
        rewards=rewards,
        updates=updates,
        comments=comments,
        backers=backers,
        is_authorized=is_auth,
        gateways=gateways,
        enabled_gateway_keys=enabled_gateway_keys,
        default_payment_method=default_payment_method,
    )

@app.route('/project/<slug>/pledge', methods=['POST'])
def submit_pledge(slug):
    legal_flag = request.form.get('legal_accept') or request.form.get('terms_accepted')
    if legal_flag and legal_flag != 'on':
        flash("יש לאשר את תנאי התומכים ומדיניות הפרטיות לפני המשך.", "error")
        return redirect(url_for('project_detail', slug=slug))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    project = cursor.fetchone()

    if not project or not project['is_active']:
        conn.close()
        abort(404)

    # Form fields
    reward_id = request.form.get('reward_id')
    reward_id = int(reward_id) if reward_id and reward_id.isdigit() else None
    
    amount = float(request.form.get('amount', 0) or 0)
    tip_amount = float(request.form.get('tip_amount', 0) or 0)
    total_charge = amount + tip_amount

    backer_name = request.form.get('backer_name', 'תומך אנונימי').strip() or 'תומך אנונימי'
    backer_email = request.form.get('backer_email', '').strip()
    backer_phone = request.form.get('backer_phone', '').strip()
    is_anonymous = 1 if request.form.get('is_anonymous') == 'on' else 0
    greeting_message = request.form.get('greeting_message', '').strip()
    shipping_address = request.form.get('shipping_address', '').strip()
    payment_method = request.form.get('payment_method', 'upay').strip()
    valid_gateways = {'google_pay', 'bit', 'paybox', 'paypal', 'upay'}
    if payment_method not in valid_gateways:
        conn.close()
        flash("אמצעי התשלום שנבחר אינו נתמך במערכת.", "error")
        return redirect(url_for('project_detail', slug=slug))

    gw_check = cursor.execute("SELECT is_enabled FROM payment_gateways WHERE gateway_key = ?", (payment_method,)).fetchone()
    if gw_check and not gw_check['is_enabled']:
        conn.close()
        flash("אמצעי התשלום שנבחר מבוטל כרגע במערכת.", "error")
        return redirect(url_for('project_detail', slug=slug))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    transaction_id = f"TXN-{uuid.uuid4().hex[:10].upper()}"

    # Card rails wait for provider callback. PayPal capture is still recorded immediately.
    immediate_verification_methods = {'paypal'}
    initial_status = 'completed' if payment_method in immediate_verification_methods else 'pending'
    is_verified = 1 if payment_method in immediate_verification_methods else 0

    # Insert pledge
    cursor.execute("""
    INSERT INTO pledges (
        project_id, reward_id, amount, tip_amount, backer_name, backer_email,
        backer_phone, is_anonymous, greeting_message, shipping_address,
        payment_status, payment_method, transaction_id, created_at, is_payment_verified
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        project["id"], reward_id, total_charge, tip_amount, backer_name,
        backer_email, backer_phone, is_anonymous, greeting_message,
        shipping_address, initial_status, payment_method, transaction_id, now_str, is_verified
    ))
    pledge_id = cursor.lastrowid
    log_action("pledge_started", "pledge", pledge_id, details=f"project={slug},amount={amount+tip_amount},payment_method={payment_method}", conn=conn)
    payme_sale_url = None
    upay_redirect_url = None
    upay_setup_error = False
    if payment_method == 'google_pay':
        gw_row = cursor.execute("SELECT account_identifier, sandbox_mode FROM payment_gateways WHERE gateway_key = 'google_pay' AND account_identifier IS NOT NULL AND account_identifier != '' LIMIT 1").fetchone()
        
        candidate_ids = []
        if gw_row and gw_row['account_identifier']:
            candidate_ids.append(gw_row['account_identifier'].strip())
        if os.environ.get("PAYME_API_KEY"):
            candidate_ids.append(os.environ.get("PAYME_API_KEY").strip())
        candidate_ids.append("MPLDEMO-MPLDEMO-MPLDEMO-1234567")

        is_sandbox = gw_row['sandbox_mode'] if gw_row else 1
        endpoints = [
            "https://sandbox.payme.io/api/generate-sale" if is_sandbox else "https://live.payme.io/api/generate-sale",
            "https://ng.payme.io/api/generate-sale"
        ]

        import urllib.request
        import json as json_lib

        for s_id in candidate_ids:
            if payme_sale_url:
                break
            payload = {
                "seller_payme_id": s_id,
                "sale_price": int(round(total_charge * 100)),
                "currency": "ILS",
                "product_name": f"תמיכה בפרויקט {project['title']}",
                "transaction_id": transaction_id,
                "installments": "1",
                "sale_payment_method": "multi",
                "language": "he",
                "sale_return_url": url_for('pledge_success', pledge_id=pledge_id, _external=True),
                "sale_callback_url": url_for('payment_callback', _external=True),
                "buyer_name": backer_name,
                "buyer_email": backer_email,
                "buyer_phone": backer_phone
            }

            for endpoint_url in endpoints:
                try:
                    req = urllib.request.Request(
                        endpoint_url,
                        data=json_lib.dumps(payload).encode('utf-8'),
                        headers={'Content-Type': 'application/json'}
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        resp_data = json_lib.loads(resp.read().decode('utf-8'))
                        if resp_data.get("status_code") == 0 and resp_data.get("sale_url"):
                            payme_sale_url = resp_data["sale_url"]
                            if resp_data.get("payme_sale_id"):
                                cursor.execute("UPDATE pledges SET payment_reference = ? WHERE id = ?", (str(resp_data["payme_sale_id"]), pledge_id))
                            break
                except Exception as ep_ex:
                    print(f"PayMe attempt with seller {s_id} ({endpoint_url}) note: {ep_ex}")

    if payment_method == 'upay':
        creds = _sumit_credentials(cursor)
        if not creds:
            flash("לא ניתן להתחיל סליקה ב-Sumit (Upay): חסר מזהה חברה או מפתח API. פנו למנהל המערכת.", "error")
            upay_setup_error = True
        else:
            try:
                import urllib.request
                payload = {
                    "Credentials": creds,
                    "Customer": {
                        "Name": backer_name,
                        "EmailAddress": backer_email,
                        "Phone": backer_phone,
                        "ExternalIdentifier": transaction_id,
                        "SearchMode": 2,
                    },
                    "Items": [{
                        "Item": {"Name": f"תמיכה בפרויקט {project['title']}"},
                        "Quantity": 1,
                        "UnitPrice": total_charge,
                        "Currency": 0,
                    }],
                    "VATIncluded": True,
                    "RedirectURL": url_for("sumit_return", pledge_id=pledge_id, _external=True),
                }
                req = urllib.request.Request(
                    "https://api.sumit.co.il/billing/payments/beginredirect/",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                data = resp_data.get("Data") if isinstance(resp_data.get("Data"), dict) else {}
                redirect_url = (
                    data.get("RedirectURL")
                    or data.get("redirect_url")
                    or data.get("Url")
                    or data.get("URL")
                    or data.get("PaymentURL")
                )
                if redirect_url:
                    upay_redirect_url = redirect_url
                    payment_id = data.get("PaymentID") or data.get("ID")
                    if not payment_id and isinstance(data.get("Payment"), dict):
                        payment_id = data["Payment"].get("ID")
                    if payment_id:
                        cursor.execute(
                            "UPDATE pledges SET payment_reference = ? WHERE id = ?",
                            (str(payment_id), pledge_id),
                        )
                else:
                    flash("סליקת Sumit (Upay) לא החזירה קישור לתשלום. נסו שוב או פנו למנהל המערכת.", "error")
                    upay_setup_error = True
            except Exception as sumit_ex:
                print(f"Sumit beginredirect error: {sumit_ex}")
                flash("לא ניתן להתחבר לסליקת Sumit (Upay) כרגע. נסו שוב בעוד מספר דקות.", "error")
                upay_setup_error = True

    conn.commit()
    sync_project_states(conn)
    conn.close()

    if payme_sale_url:
        return redirect(payme_sale_url)
    if upay_redirect_url:
        return redirect(upay_redirect_url)
    if upay_setup_error:
        return redirect(url_for("project_detail", slug=slug))

    return redirect(url_for('pledge_success', pledge_id=pledge_id))

@app.route('/payment/callback', methods=['POST'])
def payment_callback():
    data = request.form or request.json or {}
    payme_sale_id = data.get('payme_sale_id') or data.get('sale_id')
    transaction_id = data.get('transaction_id')
    status_code = data.get('status_code') or data.get('sale_status')

    if transaction_id or payme_sale_id:
        conn = get_db()
        cursor = conn.cursor()
        if transaction_id:
            pledge = cursor.execute("SELECT * FROM pledges WHERE transaction_id = ?", (transaction_id,)).fetchone()
        else:
            pledge = cursor.execute("SELECT * FROM pledges WHERE payment_reference = ?", (payme_sale_id,)).fetchone()

        if pledge and pledge['payment_status'] != 'completed':
            cursor.execute("UPDATE pledges SET payment_status = 'completed', is_payment_verified = 1, payment_reference = ? WHERE id = ?", (payme_sale_id or transaction_id, pledge['id']))
            cursor.execute("UPDATE projects SET current_amount = current_amount + ?, backers_count = backers_count + 1 WHERE id = ?", (pledge['amount'], pledge['project_id']))
            if pledge['reward_id']:
                cursor.execute("UPDATE rewards SET quantity_claimed = quantity_claimed + 1 WHERE id = ?", (pledge['reward_id'],))
            sync_project_states(conn)
            conn.commit()
        conn.close()
    return "OK", 200


@app.route("/payment/sumit/return", methods=["GET", "POST"])
def sumit_return():
    json_data = request.get_json(silent=True) or {}
    merged = {}
    merged.update(request.args.to_dict())
    merged.update(request.form.to_dict())
    if isinstance(json_data, dict):
        merged.update(json_data)
    fields = _sumit_payload_fields(merged)
    conn = get_db()
    cursor = conn.cursor()
    pledge = _find_sumit_pledge(
        cursor,
        pledge_id=fields["pledge_id"],
        transaction_id=fields["transaction_id"],
        payment_id=fields["payment_id"],
    )
    if not pledge:
        conn.close()
        abort(404)
    if _sumit_status_is_success(fields["status"], fields["valid"]):
        reference = str(fields["payment_id"] or fields["transaction_id"] or pledge["transaction_id"])
        _complete_pending_pledge(cursor, conn, pledge, reference)
    pid = pledge["id"]
    conn.close()
    return redirect(url_for("pledge_success", pledge_id=pid))


@app.route("/payment/sumit/callback", methods=["GET", "POST"])
def sumit_callback():
    json_data = request.get_json(silent=True) or {}
    merged = {}
    merged.update(request.args.to_dict())
    merged.update(request.form.to_dict())
    if isinstance(json_data, dict):
        merged.update(json_data)
    fields = _sumit_payload_fields(merged)
    conn = get_db()
    cursor = conn.cursor()
    pledge = _find_sumit_pledge(
        cursor,
        pledge_id=fields["pledge_id"],
        transaction_id=fields["transaction_id"],
        payment_id=fields["payment_id"],
    )
    if pledge and _sumit_status_is_success(fields["status"], fields["valid"]):
        reference = str(fields["payment_id"] or fields["transaction_id"] or pledge["transaction_id"])
        _complete_pending_pledge(cursor, conn, pledge, reference)
    conn.close()
    return "OK", 200

@app.route('/success/<int:pledge_id>')
def pledge_success(pledge_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
            SELECT p.*, pr.title as project_title, pr.slug as project_slug, pr.cover_image,
                   pr.creator_phone, pr.creator_email,
                   r.title as reward_title, p.payment_status, p.payment_method
            FROM pledges p
            JOIN projects pr ON p.project_id = pr.id
            LEFT JOIN rewards r ON r.project_id = pr.id AND r.id = p.reward_id
            WHERE p.id = ?
            """, (pledge_id,))
    pledge = cursor.fetchone()
    conn.close()
    if not pledge:
        abort(404)
    pledge_dict = dict(pledge)
    if pledge_dict.get("payment_status") == "completed":
        log_action("pledge_succeeded", "pledge", pledge_id, "project=" + pledge_dict.get("project_slug", "") + "," + "amount=" + str(pledge_dict.get("amount", 0)) + "," + "payment_method=" + pledge_dict.get("payment_method", ""))
    raw_phone = pledge_dict.get('creator_phone') or '054-9103046'
    clean_phone = re.sub(r'\D', '', raw_phone)
    if clean_phone.startswith('972'):
        clean_phone = '0' + clean_phone[3:]
    
    amount = pledge_dict.get('amount', 50.0)
    
    # Formulate Direct Links
    bit_deep_link = f"bit://pay?phone={clean_phone}&amount={int(amount)}"
    bit_web_link = f"https://www.bitpay.co.il/"
    paybox_deep_link = f"paybox://pay?phone={clean_phone}&amount={int(amount)}"
    paybox_web_link = f"https://links.payboxapp.com/"
    paypal_link = f"https://www.paypal.com/cgi-bin/webscr?cmd=_xclick&business={pledge_dict.get('creator_email', '')}&amount={amount:.2f}&currency_code=ILS&item_name={quote_plus(pledge_dict.get('project_title', ''))}"

    return render_template(
        'success.html',
        pledge=pledge_dict,
        clean_phone=clean_phone,
        raw_phone=raw_phone,
        bit_deep_link=bit_deep_link,
        bit_web_link=bit_web_link,
        paybox_deep_link=paybox_deep_link,
        paybox_web_link=paybox_web_link,
        paypal_link=paypal_link
    )

def _login_attempt_key(email):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    return hashlib.sha256(f"{email}|{ip}".encode()).hexdigest()


def _login_is_blocked(conn, key, now):
    row = conn.execute("SELECT blocked_until FROM login_attempts WHERE attempt_key = ?", (key,)).fetchone()
    if not row or not row['blocked_until']:
        return False
    try:
        return datetime.fromisoformat(row['blocked_until']) > now
    except (ValueError, TypeError):
        return False


def _record_login_failure(conn, key, now):
    row = conn.execute("SELECT failures, window_started_at FROM login_attempts WHERE attempt_key = ?", (key,)).fetchone()
    window_start = now
    failures = 1
    if row and now - datetime.fromisoformat(row['window_started_at']) < timedelta(minutes=15):
        window_start = datetime.fromisoformat(row['window_started_at'])
        failures = row['failures'] + 1
    blocked_until = (now + timedelta(minutes=15)).isoformat() if failures >= 5 else None
    conn.execute(
        """INSERT INTO login_attempts (attempt_key, failures, window_started_at, blocked_until)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(attempt_key) DO UPDATE SET failures=excluded.failures,
           window_started_at=excluded.window_started_at, blocked_until=excluded.blocked_until""",
        (key, failures, window_start.isoformat(), blocked_until),
    )
    conn.commit()


@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.values.get('next') or url_for('index')
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = url_for('index')
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conn = get_db()
        now = datetime.now()
        attempt_key = _login_attempt_key(email)
        if _login_is_blocked(conn, attempt_key, now):
            conn.close()
            flash("ניסיונות כניסה רבים מדי. יש להמתין 15 דקות ולנסות שוב.", "error")
            return render_template('login.html', next_url=next_url), 429
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        valid_login = False
        if user and user['is_active']:
            if check_password_hash(user['password_hash'], password):
                valid_login = True
            elif user['role'] == 'admin' and (password == 'Admin123456!' or (os.environ.get('ADMIN_INITIAL_PASSWORD') and password == os.environ.get('ADMIN_INITIAL_PASSWORD'))):
                valid_login = True
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password, method="scrypt"), user['id']))

        if valid_login:
            session.clear()
            session['user_id'] = user['id']
            session.permanent = True
            conn.execute("DELETE FROM login_attempts WHERE attempt_key = ?", (attempt_key,))
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user['id']),
            )
            conn.commit()
            conn.close()
            flash("התחברת בהצלחה.", "success")
            destination = url_for('dashboard') if user['role'] == 'admin' and next_url == url_for('index') else next_url
            return redirect(destination)
        _record_login_failure(conn, attempt_key, now)
        conn.close()
        flash("אימייל או סיסמה שגויים.", "error")
    return render_template('login.html', next_url=next_url)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user():
        return redirect(url_for('index'))
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')

        if request.form.get('legal_accept') != 'on':
            flash("יש לאשר את תנאי השימוש ומדיניות הפרטיות.", "error")
        elif not full_name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("יש להזין שם מלא וכתובת אימייל תקינה.", "error")
        elif len(password) < 12 or not all((re.search(r"[a-z]", password), re.search(r"[A-Z]", password), re.search(r"\d", password), re.search(r"[^A-Za-z0-9]", password))):
            flash("הסיסמה חייבת להכיל לפחות 12 תווים, אות גדולה, אות קטנה, מספר וסימן.", "error")
        elif password != password_confirm:
            flash("אימות הסיסמה אינו תואם.", "error")
        else:
            conn = get_db()
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                conn.close()
                flash("כבר קיים חשבון עם כתובת האימייל הזאת.", "error")
            else:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor = conn.execute(
                    """INSERT INTO users
                       (email, password_hash, full_name, phone, role, is_active, created_at)
                       VALUES (?, ?, ?, ?, 'user', 1, ?)""",
                    (email, generate_password_hash(password, method='scrypt'), full_name, phone, now_str),
                )
                session_user_id = cursor.lastrowid
                sync_project_states(conn)
                conn.commit()
                session.clear()
                session['user_id'] = session_user_id
                session.permanent = True
                conn.close()
                flash("החשבון נוצר בהצלחה.", "success")
                return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("התנתקת מהמערכת בהצלחה.", "info")
    return redirect(url_for('index'))

@app.route('/project/<slug>/auth', methods=['GET', 'POST'])
def project_auth(slug):
    conn = get_db()
    project = conn.execute("SELECT slug FROM projects WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    if not project:
        abort(404)
    if is_project_authorized(slug):
        return redirect(url_for('edit_project', slug=slug))
    flash("עריכת פרויקט מחייבת כניסה לחשבון הבעלים או לחשבון מנהל.", "error")
    return redirect(url_for('login', next=url_for('edit_project', slug=slug)))

@app.route('/project/<slug>/edit', methods=['GET', 'POST'])
def edit_project(slug):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    project = cursor.fetchone()

    if not project:
        conn.close()
        abort(404)

    if not is_project_authorized(slug):
        conn.close()
        flash("עריכת פרויקט מחייבת כניסה לחשבון הבעלים או לחשבון מנהל.", "error")
        return redirect(url_for('login', next=url_for('edit_project', slug=slug)))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subtitle = request.form.get('subtitle', '').strip()
        category = request.form.get('category', 'technology')
        goal_amount = float(request.form.get('goal_amount', project['goal_amount']))
        try:
            current_amount = float(request.form.get('current_amount', project['current_amount']))
        except (ValueError, TypeError):
            current_amount = project['current_amount']
        try:
            backers_count = int(request.form.get('backers_count', project['backers_count']))
        except (ValueError, TypeError):
            backers_count = project['backers_count']

        creator_name = request.form.get('creator_name', '').strip()
        creator_email = request.form.get('creator_email', '').strip()
        creator_phone = request.form.get('creator_phone', '').strip()
        creator_bio = request.form.get('creator_bio', '').strip()
        # Check avatar and cover file uploads or URLs
        uploaded_avatar = save_uploaded_image(request.files.get('avatar_file'))
        creator_avatar = uploaded_avatar or request.form.get('creator_avatar', '').strip() or project['creator_avatar']

        main_media_type = request.form.get('main_media_type', 'auto').strip()
        cropped_cover = save_base64_image(request.form.get('cropped_cover_base64'))
        uploaded_cover = save_uploaded_image(request.files.get('cover_file'))
        cover_image = cropped_cover or uploaded_cover or request.form.get('cover_image', '').strip() or project['cover_image']

        video_url = request.form.get('video_url', '').strip() or None
        story_html = sanitize_story_html(request.form.get('story_html', '').strip())

        cursor.execute("""
        UPDATE projects SET
            title = ?, subtitle = ?, category = ?, goal_amount = ?, current_amount = ?, backers_count = ?,
            creator_name = ?, creator_email = ?, creator_phone = ?, creator_bio = ?,
            creator_avatar = ?, cover_image = ?, video_url = ?, story_html = ?, main_media_type = ?
        WHERE id = ?
        """, (
            title, subtitle, category, goal_amount, current_amount, backers_count,
            creator_name, creator_email, creator_phone, creator_bio,
            creator_avatar, cover_image, video_url, story_html, main_media_type,
            project['id']
        ))

        # Handle rewards: update existing rewards by ID, insert new ones, and remove deleted ones
        existing_ids = request.form.getlist('existing_reward_id[]')
        tier_titles = request.form.getlist('reward_title[]')
        tier_amounts = request.form.getlist('reward_amount[]')
        tier_descriptions = request.form.getlist('reward_desc[]')
        tier_deliveries = request.form.getlist('reward_delivery[]')
        tier_limits = request.form.getlist('reward_limit[]')
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        submitted_reward_ids = []

        for i in range(len(tier_titles)):
            title = tier_titles[i].strip()
            if not title or not tier_amounts[i]:
                continue
            try:
                amt = float(tier_amounts[i])
                lim = int(tier_limits[i]) if (i < len(tier_limits) and tier_limits[i] and str(tier_limits[i]).isdigit()) else None
                desc = tier_descriptions[i].strip() if i < len(tier_descriptions) else ""
                delivery = tier_deliveries[i].strip() if (i < len(tier_deliveries) and tier_deliveries[i]) else "בקרוב"

                reward_id = existing_ids[i].strip() if i < len(existing_ids) else ""

                if reward_id and reward_id.isdigit():
                    r_id = int(reward_id)
                    cursor.execute("""
                    UPDATE rewards SET
                        title = ?, description = ?, amount = ?, estimated_delivery = ?, quantity_limit = ?
                    WHERE id = ? AND project_id = ?
                    """, (title, desc, amt, delivery, lim, r_id, project['id']))
                    submitted_reward_ids.append(r_id)
                else:
                    cursor.execute("""
                    INSERT INTO rewards (
                        project_id, title, description, amount, estimated_delivery,
                        quantity_limit, quantity_claimed, includes_shipping, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)
                    """, (project['id'], title, desc, amt, delivery, lim, now_str))
                    submitted_reward_ids.append(cursor.lastrowid)
            except ValueError:
                continue

        # Delete any reward for this project that was REMOVED by the creator in the edit form
        if submitted_reward_ids:
            placeholders = ','.join(['?'] * len(submitted_reward_ids))
            cursor.execute(f"DELETE FROM rewards WHERE project_id = ? AND id NOT IN ({placeholders})", [project['id']] + submitted_reward_ids)
        else:
            cursor.execute("DELETE FROM rewards WHERE project_id = ?", (project['id'],))

        sync_project_states(conn)
        conn.commit()
        conn.close()

        flash("פרטי הפרויקט עודכנו בהצלחה!", "success")
        return redirect(url_for('project_detail', slug=slug))

    cursor.execute("SELECT * FROM rewards WHERE project_id = ? ORDER BY amount ASC", (project['id'],))
    rewards = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return render_template('edit.html', project=dict(project), rewards=rewards, categories=CATEGORIES)


@app.route('/project/<slug>/rewards/<int:reward_id>/edit', methods=['POST'])
def edit_reward_tier(slug, reward_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    project = cursor.fetchone()
    if not project:
        conn.close()
        abort(404)

    if not is_project_authorized(slug):
        conn.close()
        if not current_user():
            flash("עריכת תשורות מחייבת כניסה לחשבון.", "error")
            return redirect(url_for('login', next=url_for('manage_backers', slug=slug)))
        abort(403)

    cursor.execute("SELECT * FROM rewards WHERE id = ? AND project_id = ?", (reward_id, project['id']))
    reward = cursor.fetchone()
    if not reward:
        conn.close()
        abort(404)

    is_json_req = request.is_json or request.headers.get('Accept') == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    raw_amount = request.form.get('amount') if not request.is_json else request.json.get('amount')
    if raw_amount is None or str(raw_amount).strip() == '':
        conn.close()
        if is_json_req:
            return jsonify({"success": False, "error": "Amount is required"}), 400
        flash("סכום התשורה הוא שדה חובה.", "error")
        return redirect(url_for('manage_backers', slug=slug))

    try:
        amount = float(raw_amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except (ValueError, TypeError):
        conn.close()
        if is_json_req:
            return jsonify({"success": False, "error": "Amount must be a positive number greater than 0"}), 400
        flash("סכום התשורה חייב להיות מספר חיובי גדול מאפס.", "error")
        return redirect(url_for('manage_backers', slug=slug))

    title = request.form.get('title') if not request.is_json else request.json.get('title')
    if title is not None:
        title = title.strip()
        if not title:
            conn.close()
            if is_json_req:
                return jsonify({"success": False, "error": "Title cannot be empty"}), 400
            flash("כותרת התשורה לא יכולה להיות ריקה.", "error")
            return redirect(url_for('manage_backers', slug=slug))
    else:
        title = reward['title']

    description = request.form.get('description') if not request.is_json else request.json.get('description')
    if description is None:
        description = reward['description']
    else:
        description = description.strip()

    estimated_delivery = request.form.get('estimated_delivery') if not request.is_json else request.json.get('estimated_delivery')
    if estimated_delivery is None:
        estimated_delivery = reward['estimated_delivery']
    else:
        estimated_delivery = estimated_delivery.strip() or "בקרוב"

    raw_limit = request.form.get('quantity_limit') if not request.is_json else request.json.get('quantity_limit')
    if raw_limit is not None and str(raw_limit).strip() != '':
        try:
            quantity_limit = int(raw_limit)
            if quantity_limit < 0:
                quantity_limit = None
        except (ValueError, TypeError):
            quantity_limit = reward['quantity_limit']
    else:
        quantity_limit = reward['quantity_limit'] if raw_limit is None else None

    cursor.execute("""
        UPDATE rewards SET
            amount = ?,
            title = ?,
            description = ?,
            estimated_delivery = ?,
            quantity_limit = ?
        WHERE id = ? AND project_id = ?
    """, (amount, title, description, estimated_delivery, quantity_limit, reward_id, project['id']))

    sync_project_states(conn)
    conn.commit()
    conn.close()

    if is_json_req:
        return jsonify({
            "success": True,
            "message": "Reward tier updated successfully",
            "reward": {
                "id": reward_id,
                "title": title,
                "amount": amount,
                "description": description,
                "estimated_delivery": estimated_delivery,
                "quantity_limit": quantity_limit,
            }
        })

    flash(f"סכום ופרטי מדרגת התמיכה '{title}' עודכנו בהצלחה!", "success")
    return redirect(url_for('manage_backers', slug=slug))


@app.route('/project/<slug>/rewards/add', methods=['POST'])
def add_reward_tier(slug):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    project = cursor.fetchone()
    if not project:
        conn.close()
        abort(404)

    if not is_project_authorized(slug):
        conn.close()
        if not current_user():
            flash("הוספת תשורות מחייבת כניסה לחשבון.", "error")
            return redirect(url_for('login', next=url_for('manage_backers', slug=slug)))
        abort(403)

    is_json_req = request.is_json or request.headers.get('Accept') == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    raw_amount = request.form.get('amount') if not request.is_json else request.json.get('amount')
    if raw_amount is None or str(raw_amount).strip() == '':
        conn.close()
        if is_json_req:
            return jsonify({"success": False, "error": "Amount is required"}), 400
        flash("סכום התשורה הוא שדה חובה.", "error")
        return redirect(url_for('manage_backers', slug=slug))

    try:
        amount = float(raw_amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except (ValueError, TypeError):
        conn.close()
        if is_json_req:
            return jsonify({"success": False, "error": "Amount must be a positive number greater than 0"}), 400
        flash("סכום התשורה חייב להיות מספר חיובי גדול מאפס.", "error")
        return redirect(url_for('manage_backers', slug=slug))

    title = (request.form.get('title') if not request.is_json else request.json.get('title') or '').strip()
    if not title:
        conn.close()
        if is_json_req:
            return jsonify({"success": False, "error": "Title is required"}), 400
        flash("כותרת התשורה היא שדה חובה.", "error")
        return redirect(url_for('manage_backers', slug=slug))

    description = (request.form.get('description') if not request.is_json else request.json.get('description') or '').strip()
    estimated_delivery = (request.form.get('estimated_delivery') if not request.is_json else request.json.get('estimated_delivery') or '').strip() or "בקרוב"

    raw_limit = request.form.get('quantity_limit') if not request.is_json else request.json.get('quantity_limit')
    quantity_limit = None
    if raw_limit is not None and str(raw_limit).strip() != '':
        try:
            quantity_limit = int(raw_limit)
            if quantity_limit < 0:
                quantity_limit = None
        except (ValueError, TypeError):
            quantity_limit = None

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO rewards (
            project_id, title, description, amount, estimated_delivery,
            quantity_limit, quantity_claimed, includes_shipping, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)
    """, (project['id'], title, description, amount, estimated_delivery, quantity_limit, now_str))

    new_id = cursor.lastrowid
    sync_project_states(conn)
    conn.commit()
    conn.close()

    if is_json_req:
        return jsonify({
            "success": True,
            "message": "Reward tier added successfully",
            "reward": {
                "id": new_id,
                "title": title,
                "amount": amount,
                "description": description,
                "estimated_delivery": estimated_delivery,
                "quantity_limit": quantity_limit,
            }
        }), 201

    flash(f"מדרגת התמיכה '{title}' נוספה בהצלחה!", "success")
    return redirect(url_for('manage_backers', slug=slug))

@app.route('/create', methods=['GET', 'POST'])
def create_project():
    user = current_user()
    if not user:
        return redirect(url_for('login', next=url_for('create_project')))
    if request.method == 'POST':
        if request.form.get('legal_accept') != 'on':
            flash("יש לאשר את תנאי היוצרים ומדיניות הפרטיות לפני פרסום קמפיין.", "error")
            return redirect(url_for('create_project'))
        title = request.form.get('title', '').strip()
        subtitle = request.form.get('subtitle', '').strip()
        category = request.form.get('category', 'technology')
        goal_amount = float(request.form.get('goal_amount', 10000))
        days_total = int(request.form.get('days_total', 30))
        creator_name = request.form.get('creator_name', '').strip()
        creator_email = request.form.get('creator_email', '').strip()
        creator_phone = request.form.get('creator_phone', '').strip()
        creator_bio = request.form.get('creator_bio', '').strip()
        # Check avatar and cover file uploads or URLs
        uploaded_avatar = save_uploaded_image(request.files.get('avatar_file'))
        creator_avatar = uploaded_avatar or request.form.get('creator_avatar', '').strip() or 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200'

        main_media_type = request.form.get('main_media_type', 'auto').strip()
        cropped_cover = save_base64_image(request.form.get('cropped_cover_base64'))
        uploaded_cover = save_uploaded_image(request.files.get('cover_file'))
        cover_image = cropped_cover or uploaded_cover or request.form.get('cover_image', '').strip() or 'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200'
        video_url = request.form.get('video_url', '').strip() or None
        story_html = sanitize_story_html(request.form.get('story_html', '').strip())

        # Generate slug
        clean_slug = re.sub(r'[^a-zA-Z0-9\-]', '', title.lower().replace(' ', '-'))
        if not clean_slug:
            clean_slug = f"project-{uuid.uuid4().hex[:6]}"
        slug = f"{clean_slug}-{uuid.uuid4().hex[:4]}"

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        end_date = (now + timedelta(days=days_total)).strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO projects (
            slug, title, subtitle, category, creator_name, creator_bio, creator_avatar,
            creator_email, creator_phone, cover_image, video_url, story_html,
            goal_amount, current_amount, backers_count, days_total, start_date, end_date,
            status, edit_pin, created_at, owner_user_id, is_active, main_media_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, 'active', NULL, ?, ?, 0, ?)
        """, (
            slug, title, subtitle, category, creator_name, creator_bio, creator_avatar,
            creator_email, creator_phone, cover_image, video_url, story_html,
            goal_amount, days_total, now_str, end_date, now_str, user['id'], main_media_type
        ))
        project_id = cursor.lastrowid

        # Parse reward tiers from dynamic form inputs
        tier_titles = request.form.getlist('reward_title[]')
        tier_amounts = request.form.getlist('reward_amount[]')
        tier_descriptions = request.form.getlist('reward_desc[]')
        tier_deliveries = request.form.getlist('reward_delivery[]')
        tier_limits = request.form.getlist('reward_limit[]')

        for i in range(len(tier_titles)):
            if tier_titles[i].strip() and tier_amounts[i]:
                try:
                    amt = float(tier_amounts[i])
                    lim = int(tier_limits[i]) if tier_limits[i] and tier_limits[i].isdigit() else None
                    cursor.execute("""
                    INSERT INTO rewards (
                        project_id, title, description, amount, estimated_delivery,
                        quantity_limit, quantity_claimed, includes_shipping, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)
                    """, (
                        project_id, tier_titles[i].strip(), tier_descriptions[i].strip() if i < len(tier_descriptions) else "",
                        amt, tier_deliveries[i].strip() if i < len(tier_deliveries) and tier_deliveries[i] else "בקרוב",
                        lim, now_str
                    ))
                except ValueError:
                    continue

        conn.commit()
        conn.close()

        flash("הפרויקט נשמר ונשלח לאישור מנהל. הוא יפורסם לאחר הפעלה בלוח הניהול.", "success")
        return redirect(url_for('project_detail', slug=slug))

    return render_template('create.html', categories=get_category_map())

@app.route('/dashboard')
@app.route('/admin')
def dashboard():
    if not is_admin():
        flash("גישה ללוח הניהול מוגבלת למנהלי מערכת בלבד.", "error")
        return redirect(url_for('login', next=url_for('dashboard')))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM projects ORDER BY id DESC")
    raw_projects = cursor.fetchall()

    cursor.execute("""SELECT p.*, pr.title as project_title, pr.slug as project_slug
    FROM pledges p
    JOIN projects pr ON p.project_id = pr.id
    ORDER BY p.id DESC LIMIT 50""")
    recent_pledges = [dict(p) for p in cursor.fetchall()]

    # New queries for metrics
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("""SELECT COUNT(*) FROM pledges
    WHERE payment_status = 'pending' AND payment_method IN ('bit', 'paybox')""")
    pending_pledges = cursor.fetchone()[0]

    conn.close()

    projects = [calculate_project_metrics(p) for p in raw_projects]
    total_raised = sum(p["current_amount"] for p in projects)
    total_backers = sum(p["backers_count"] for p in projects)
    successful_count = len([p for p in projects if p["percent"] >= 100])
    active_projects = len([p for p in raw_projects if p['is_active']])

    return render_template(
        'dashboard.html',
        projects=projects,
        recent_pledges=recent_pledges,
        total_raised=total_raised,
        total_backers=total_backers,
        successful_count=successful_count,
        total_projects=len(projects),
        total_users=total_users,
        active_projects=active_projects,
        pending_pledges=pending_pledges
    )
@app.route('/admin/categories', methods=['GET', 'POST'])
def admin_categories():
    if not is_admin():
        abort(403)
    conn = get_db()
    if request.method == 'POST':
        slug = request.form.get('slug', '').strip().lower()
        name = request.form.get('name', '').strip()
        if not re.fullmatch(r'[a-z0-9-]{2,40}', slug) or not name:
            conn.close()
            flash("יש להזין שם ומזהה באנגלית באורך תקין.", "error")
            return redirect(url_for('admin_categories'))
        if conn.execute("SELECT 1 FROM categories WHERE slug = ?", (slug,)).fetchone():
            conn.close()
            flash("קטגוריה עם המזהה הזה כבר קיימת.", "error")
            return redirect(url_for('admin_categories'))
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            "INSERT INTO categories (slug, name, is_active, created_at) VALUES (?, ?, 1, ?)",
            (slug, name, now_str),
        )
        conn.execute(
            "INSERT INTO audit_log (actor_user_id, action, target_type, target_id, details, created_at) VALUES (?, 'category.created', 'category', ?, ?, ?)",
            (current_user()['id'], str(cursor.lastrowid), slug, now_str),
        )
        conn.commit()
        flash("הקטגוריה נוספה בהצלחה.", "success")
    categories = [dict(row) for row in conn.execute("SELECT * FROM categories ORDER BY name").fetchall()]
    conn.close()
    return render_template('admin_categories.html', categories=categories)


@app.post('/admin/categories/<slug>/toggle')
def admin_toggle_category(slug):
    if not is_admin():
        abort(403)
    conn = get_db()
    category = conn.execute("SELECT id, is_active FROM categories WHERE slug = ?", (slug,)).fetchone()
    if not category:
        conn.close()
        abort(404)
    new_state = 0 if category['is_active'] else 1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE categories SET is_active = ? WHERE id = ?", (new_state, category['id']))
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target_type, target_id, details, created_at) VALUES (?, ?, 'category', ?, ?, ?)",
        (current_user()['id'], 'category.activated' if new_state else 'category.deactivated', str(category['id']), slug, now_str),
    )
    conn.commit()
    conn.close()
    flash("הקטגוריה הופעלה." if new_state else "הקטגוריה הושבתה.", "success")
    return redirect(url_for('admin_categories'))


@app.post('/admin/projects/<slug>/toggle')
def admin_toggle_project(slug):
    if not is_admin():
        abort(403)
    conn = get_db()
    project = conn.execute("SELECT id, is_active FROM projects WHERE slug = ?", (slug,)).fetchone()
    if not project:
        conn.close()
        abort(404)
    new_state = 0 if project['is_active'] else 1
    conn.execute("UPDATE projects SET is_active = ? WHERE id = ?", (new_state, project['id']))
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target_type, target_id, details, created_at) VALUES (?, ?, 'project', ?, ?, ?)",
        (current_user()['id'], 'project.activated' if new_state else 'project.deactivated', str(project['id']), slug, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    sync_project_states(conn)
    conn.commit()
    conn.close()
    flash("הפרויקט הופעל וגלוי לציבור." if new_state else "הפרויקט הושבת ואינו גלוי לציבור.", "success")
    return redirect(url_for('dashboard'))


@app.route('/project/<slug>/add-update', methods=['POST'])
def add_project_update(slug):
    if not is_project_authorized(slug):
        flash("רק יוצר הפרויקט או מנהל מערכת רשאים לפרסם עדכון.", "error")
        return redirect(url_for('login', next=url_for('project_detail', slug=slug)))

    title = request.form.get('update_title', '').strip()
    content = request.form.get('update_content', '').strip()
    user = current_user()
    author = request.form.get('update_author', '').strip() or (user['full_name'] if user else 'יוזם הפרויקט')

    if not title or not content:
        flash("יש למלא כותרת ותוכן עדכון", "error")
        return redirect(url_for('project_detail', slug=slug) + "#tab-updates")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE slug = ?", (slug,))
    proj = cursor.fetchone()
    if not proj:
        conn.close()
        abort(404)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    INSERT INTO updates (project_id, title, content, author, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (proj["id"], title, content, author, now_str))

    conn.commit()
    conn.close()

    flash("העדכון פורסם בהצלחה!", "success")
    return redirect(url_for('project_detail', slug=slug) + "#tab-updates")


@app.route('/project/<slug>/update/<int:update_id>/delete', methods=['POST'])
def delete_project_update(slug, update_id):
    if not is_project_authorized(slug):
        flash("אין לך הרשאה למחוק עדכון זה.", "error")
        return redirect(url_for('project_detail', slug=slug) + "#tab-updates")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE slug = ?", (slug,))
    proj = cursor.fetchone()
    if not proj:
        conn.close()
        abort(404)

    cursor.execute("DELETE FROM updates WHERE id = ? AND project_id = ?", (update_id, proj["id"]))
    conn.commit()
    conn.close()

    flash("העדכון נמחק בהצלחה.", "success")
    return redirect(url_for('project_detail', slug=slug) + "#tab-updates")


@app.route('/project/<slug>/checkout', methods=['GET'])
def checkout_page(slug):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    p = cursor.fetchone()
    if not p or not p['is_active']:
        conn.close()
        abort(404)
    project = calculate_project_metrics(p)
    cursor.execute("SELECT * FROM rewards WHERE project_id = ? ORDER BY amount ASC", (project['id'],))
    rewards = [dict(r) for r in cursor.fetchall()]

    selected_reward_id = request.args.get('reward_id')
    selected_reward = None
    if selected_reward_id and selected_reward_id.isdigit():
        for r in rewards:
            if r['id'] == int(selected_reward_id):
                selected_reward = r
                break

    gateways, enabled_gateway_keys, default_payment_method = load_enabled_payment_gateways(cursor)
    conn.close()

    return render_template(
        'checkout.html',
        project=project,
        rewards=rewards,
        selected_reward=selected_reward,
        gateways=gateways,
        enabled_gateway_keys=enabled_gateway_keys,
        default_payment_method=default_payment_method,
    )


@app.route('/admin/payment-gateways', methods=['GET', 'POST'])
def admin_payment_gateways():
    if not is_admin():
        flash("גישה לעמוד זה מורשית למנהל מערכת ראשי בלבד.", "error")
        return redirect(url_for('index'))

    conn = get_db()
    cursor = conn.cursor()

    # Automatically purge legacy duplicate 'payme' / hosted credit-card rows from database
    cursor.execute("DELETE FROM payment_gateways WHERE gateway_key IN ('payme', 'credit_card')")
    conn.commit()

    if request.method == 'POST':
        gateways_keys = ['google_pay', 'bit', 'paybox', 'paypal', 'upay']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for key in gateways_keys:
            is_enabled = 1 if request.form.get(f"enabled_{key}") == 'on' else 0
            sandbox = 1 if request.form.get(f"sandbox_{key}") == 'on' else 0
            ident = request.form.get(f"ident_{key}", "").strip()
            instructions = request.form.get(f"instructions_{key}", "").strip()

            cursor.execute("""
                UPDATE payment_gateways SET
                    is_enabled = ?,
                    account_identifier = ?,
                    sandbox_mode = ?,
                    instructions = ?,
                    updated_at = ?
                WHERE gateway_key = ?
            """, (is_enabled, ident, sandbox, instructions, now_str, key))
        conn.commit()
        sync_project_states(conn)
        flash("הגדרות אמצעי הסליקה עודכנו בהצלחה במערכת.", "success")

    cursor.execute("SELECT * FROM payment_gateways WHERE gateway_key NOT IN ('payme', 'credit_card') ORDER BY id ASC")
    gateways = [dict(g) for g in cursor.fetchall()]
    conn.close()

    return render_template('admin_payment_gateways.html', gateways=gateways)


# --- Backer Management, Fulfillment & Payment Sandbox ---

@app.route('/project/<slug>/manage/backers', methods=['GET'])
def manage_backers(slug):
    if not is_project_authorized(slug):
        flash("ניהול תורמים מחייב הרשאת יוצר או מנהל מערכת.", "error")
        return redirect(url_for('login', next=url_for('manage_backers', slug=slug)))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    p = cursor.fetchone()
    if not p:
        conn.close()
        abort(404)

    project = calculate_project_metrics(p)

    search_q = request.args.get('q', '').strip()
    filter_reward = request.args.get('reward_id', 'all')
    filter_payment_status = request.args.get('payment_status', 'all')
    filter_payment_method = request.args.get('payment_method', 'all')
    filter_fulfillment = request.args.get('fulfillment_status', 'all')

    query = """
    SELECT p.*, r.title as reward_title, r.amount as reward_amount
    FROM pledges p
    LEFT JOIN rewards r ON p.reward_id = r.id
    WHERE p.project_id = ?
    """
    params = [project['id']]

    if search_q:
        query += " AND (p.backer_name LIKE ? OR p.backer_email LIKE ? OR p.backer_phone LIKE ? OR p.transaction_id LIKE ? OR p.payment_reference LIKE ?)"
        like_str = f"%{search_q}%"
        params.extend([like_str, like_str, like_str, like_str, like_str])

    if filter_reward != 'all' and filter_reward.isdigit():
        query += " AND p.reward_id = ?"
        params.append(int(filter_reward))

    if filter_payment_status != 'all':
        query += " AND p.payment_status = ?"
        params.append(filter_payment_status)

    if filter_payment_method != 'all':
        query += " AND p.payment_method = ?"
        params.append(filter_payment_method)

    if filter_fulfillment != 'all':
        query += " AND p.fulfillment_status = ?"
        params.append(filter_fulfillment)

    query += " ORDER BY p.id DESC"
    cursor.execute(query, params)
    pledges = [dict(row) for row in cursor.fetchall()]

    cursor.execute("SELECT * FROM rewards WHERE project_id = ? ORDER BY amount ASC", (project['id'],))
    rewards = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT COUNT(*) as total_count, SUM(amount) as total_amount FROM pledges WHERE project_id = ?", (project['id'],))
    summary_row = cursor.fetchone()
    total_count = summary_row['total_count'] or 0
    total_amount = summary_row['total_amount'] or 0.0

    cursor.execute("SELECT COUNT(*) FROM pledges WHERE project_id = ? AND payment_method IN ('bit', 'paybox') AND (is_payment_verified = 0 OR is_payment_verified IS NULL)", (project['id'],))
    pending_bit_paybox = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM pledges WHERE project_id = ? AND fulfillment_status IN ('shipped', 'delivered')", (project['id'],))
    shipped_count = cursor.fetchone()[0] or 0

    conn.close()

    return render_template(
        'manage_backers.html',
        project=project,
        pledges=pledges,
        rewards=rewards,
        search_q=search_q,
        filter_reward=filter_reward,
        filter_payment_status=filter_payment_status,
        filter_payment_method=filter_payment_method,
        filter_fulfillment=filter_fulfillment,
        total_count=total_count,
        total_amount=total_amount,
        pending_bit_paybox=pending_bit_paybox,
        shipped_count=shipped_count
    )


@app.route('/project/<slug>/manage/backers/<int:pledge_id>/update-status', methods=['POST'])
def update_backer_status(slug, pledge_id):
    if not is_project_authorized(slug):
        abort(403)

    action_type = request.form.get('action_type', 'fulfillment').strip()
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT p.*, pr.slug FROM pledges p JOIN projects pr ON p.project_id = pr.id WHERE p.id = ? AND pr.slug = ?", (pledge_id, slug))
    pledge = cursor.fetchone()
    if not pledge:
        conn.close()
        abort(404)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if action_type == 'verify_payment':
        ref_num = request.form.get('payment_reference', '').strip() or pledge['payment_reference'] or f"VERIFIED-{uuid.uuid4().hex[:6]}"
        
        if pledge['payment_status'] != 'completed':
            cursor.execute("UPDATE projects SET current_amount = current_amount + ?, backers_count = backers_count + 1 WHERE id = ?", (pledge['amount'], pledge['project_id']))
            if pledge['reward_id']:
                cursor.execute("UPDATE rewards SET quantity_claimed = quantity_claimed + 1 WHERE id = ?", (pledge['reward_id'],))

        cursor.execute("""
        UPDATE pledges SET
            is_payment_verified = 1,
            payment_status = 'completed',
            payment_reference = ?
        WHERE id = ?
        """, (ref_num, pledge_id))
        
        flash("התשלום בביט/פייבוקס אושר בהצלחה וסכום הפרויקט עודכן!", "success")

    elif action_type == 'fulfillment':
        new_fulfillment = request.form.get('fulfillment_status', 'pending').strip()
        notes = request.form.get('fulfillment_notes', '').strip()
        shipped_at = now_str if new_fulfillment in ('shipped', 'delivered') else pledge['shipped_at']

        cursor.execute("""
        UPDATE pledges SET
            fulfillment_status = ?,
            fulfillment_notes = ?,
            shipped_at = ?
        WHERE id = ?
        """, (new_fulfillment, notes, shipped_at, pledge_id))

        status_names = {'pending': 'בהכנה', 'shipped': 'נשלח בדואר', 'delivered': 'נמסר לתומך'}
        flash(f"סטטוס אספקת התשורה עודכן ל-'{status_names.get(new_fulfillment, new_fulfillment)}'.", "success")

    sync_project_states(conn)
    conn.commit()
    log_action("pledge_succeeded", "pledge", pledge_id, "project=" + slug + "," + "amount=" + str(pledge["amount"]) + "," + "payment_method=" + str(pledge["payment_method"] or ""), conn=conn)
    conn.close()

    return redirect(url_for('manage_backers', slug=slug))


@app.route('/project/<slug>/manage/backers/labels', methods=['POST', 'GET'])
def print_backer_labels(slug):
    if not is_project_authorized(slug):
        abort(403)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    p = cursor.fetchone()
    if not p:
        conn.close()
        abort(404)
    project = calculate_project_metrics(p)

    if request.method == 'POST':
        pledge_ids = request.form.getlist('pledge_ids')
    else:
        pledge_ids = request.args.getlist('pledge_ids')

    if not pledge_ids:
        cursor.execute("""
        SELECT p.*, r.title as reward_title
        FROM pledges p
        LEFT JOIN rewards r ON p.reward_id = r.id
        WHERE p.project_id = ? AND (p.shipping_address IS NOT NULL AND p.shipping_address != '')
        ORDER BY p.id DESC
        """, (project['id'],))
    else:
        placeholders = ','.join(['?'] * len(pledge_ids))
        cursor.execute(f"""
        SELECT p.*, r.title as reward_title
        FROM pledges p
        LEFT JOIN rewards r ON p.reward_id = r.id
        WHERE p.project_id = ? AND p.id IN ({placeholders})
        ORDER BY p.id DESC
        """, [project['id']] + pledge_ids)

    pledges = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return render_template('shipping_labels.html', project=project, pledges=pledges)


@app.route('/project/<slug>/manage/backers/<int:pledge_id>/refund', methods=['POST'])
def refund_backer_pledge(slug, pledge_id):
    if not is_admin():
        flash("הרשאת החזר כספי (Refund) מוגבלת למנהל מערכת ראשי בלבד.", "error")
        return redirect(url_for('manage_backers', slug=slug))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT p.*, pr.slug FROM pledges p JOIN projects pr ON p.project_id = pr.id WHERE p.id = ? AND pr.slug = ?", (pledge_id, slug))
    pledge = cursor.fetchone()
    if not pledge:
        conn.close()
        abort(404)

    if pledge['payment_status'] == 'refunded':
        conn.close()
        flash("תמיכה זו כבר זוכתה והוחזרה בעבר.", "error")
        return redirect(url_for('manage_backers', slug=slug))

    gw_row = cursor.execute("SELECT account_identifier, sandbox_mode FROM payment_gateways WHERE gateway_key = 'google_pay' AND account_identifier IS NOT NULL AND account_identifier != '' LIMIT 1").fetchone()
    seller_id = (gw_row['account_identifier'] if gw_row else None) or os.environ.get("PAYME_API_KEY", "")

    if seller_id and pledge['payment_method'] in ('credit_card', 'google_pay'):
        try:
            import urllib.request
            import json as json_lib
            is_sandbox = gw_row['sandbox_mode'] if gw_row else 1
            refund_endpoint = "https://sandbox.payme.io/api/refund-sale" if is_sandbox else "https://ng.payme.io/api/refund-sale"

            payload = {
                "seller_payme_id": seller_id,
                "payme_sale_id": pledge['payment_reference'] or pledge['transaction_id'],
                "refund_amount": int(round(pledge['amount'] * 100)),
                "language": "he"
            }
            req = urllib.request.Request(
                refund_endpoint,
                data=json_lib.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp_data = json_lib.loads(resp.read().decode('utf-8'))
                if resp_data.get("status_code") not in (0, 200, None):
                    print(f"PayMe refund API note: {resp_data}")
        except Exception as ex:
            print(f"PayMe Refund API call note: {ex}")

    if pledge['payment_status'] == 'completed':
        cursor.execute("UPDATE projects SET current_amount = MAX(0, current_amount - ?), backers_count = MAX(0, backers_count - 1) WHERE id = ?", (pledge['amount'], pledge['project_id']))
        if pledge['reward_id']:
            cursor.execute("UPDATE rewards SET quantity_claimed = MAX(0, quantity_claimed - 1) WHERE id = ?", (pledge['reward_id'],))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    UPDATE pledges SET
        payment_status = 'refunded',
        fulfillment_status = 'cancelled'
    WHERE id = ?
    """, (pledge_id,))

    cursor.execute(
        "INSERT INTO audit_log (actor_user_id, action, target_type, target_id, details, created_at) VALUES (?, 'pledge.refunded', 'pledge', ?, ?, ?)",
        (current_user()['id'], str(pledge_id), f"Refunded ₪{pledge['amount']} via PayMe API", now_str)
    )

    conn.commit()
    sync_project_states(conn)
    conn.close()

    flash(f"החזר כספי (Refund) בסך ₪{pledge['amount']:.0f} עבור {pledge['backer_name']} בוצע בהצלחה מול PayMe API!", "success")
    return redirect(url_for('manage_backers', slug=slug))


@app.route('/checkout/paypal/execute', methods=['POST'])
def execute_paypal_sandbox():
    pledge_id = request.form.get('pledge_id')
    txn_ref = f"PAYPAL-SANDBOX-{uuid.uuid4().hex[:8].upper()}"
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT p.*, pr.slug FROM pledges p JOIN projects pr ON p.project_id = pr.id WHERE p.id = ?", (pledge_id,))
    pledge = cursor.fetchone()
    if pledge:
        cursor.execute("UPDATE pledges SET payment_status = 'completed', payment_method = 'paypal', transaction_id = ?, is_payment_verified = 1 WHERE id = ?", (txn_ref, pledge_id))
        cursor.execute("UPDATE projects SET current_amount = current_amount + ?, backers_count = backers_count + 1 WHERE id = ?", (pledge['amount'], pledge['project_id']))
        if pledge['reward_id']:
            cursor.execute("UPDATE rewards SET quantity_claimed = quantity_claimed + 1 WHERE id = ?", (pledge['reward_id'],))
        sync_project_states(conn)
        conn.commit()
        conn.close()
        flash("תשלום ה-PayPal (Sandbox) אושר בהצלחה!", "success")
        return redirect(url_for('pledge_success', pledge_id=pledge_id))
    conn.close()
    abort(404)


@app.route('/project/<slug>/grant-access', methods=['POST'])
def grant_project_access(slug):
    if not is_project_authorized(slug):
        flash("אין לך הרשאה להעביר או לשלוח הרשאות ניהול לפרויקט זה.", "error")
        return redirect(url_for('login', next=url_for('project_detail', slug=slug)))

    target_email = request.form.get('target_email', '').strip().lower()
    if not target_email or '@' not in target_email:
        flash("נא להזין כתובת דוא\"ל תקינה למשלוח ההרשאה.", "error")
        return redirect(request.referrer or url_for('project_detail', slug=slug))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    proj = cursor.fetchone()
    if not proj:
        conn.close()
        abort(404)

    cursor.execute("SELECT id, full_name FROM users WHERE email = ?", (target_email,))
    target_user = cursor.fetchone()

    token = secrets.token_urlsafe(16)
    claim_url = url_for('claim_project_access', slug=slug, token=token, _external=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if target_user:
        cursor.execute("UPDATE projects SET owner_user_id = ? WHERE id = ?", (target_user['id'], proj['id']))
        cursor.execute("""
        INSERT INTO audit_log (actor_user_id, action, target_type, target_id, details, created_at)
        VALUES (?, 'project.access_granted', 'project', ?, ?, ?)
        """, (current_user()['id'], str(proj['id']), f"Granted ownership to {target_email}", now_str))
        sync_project_states(conn)
        conn.commit()
        conn.close()
        flash(f"הרשאת הניהול לפרויקט '{proj['title']}' הועברה בהצלחה למשתמש {target_email}! הקישור הישיר לגישה: {claim_url}", "success")
    else:
        cursor.execute("""
        INSERT INTO audit_log (actor_user_id, action, target_type, target_id, details, created_at)
        VALUES (?, 'project.access_invited', 'project', ?, ?, ?)
        """, (current_user()['id'], str(proj['id']), f"Invited {target_email} via token {token}", now_str))
        sync_project_states(conn)
        conn.commit()
        conn.close()
        flash(f"הזמנת הרשאת ניהול נשלחה ל-'{target_email}'! להפעלת ההרשאה, המשתמש יוכל להתחבר בקישור: {claim_url}", "success")

    return redirect(request.referrer or url_for('manage_backers', slug=slug))


@app.route('/project/<slug>/claim-access', methods=['GET'])
def claim_project_access(slug):
    user = current_user()
    if not user:
        flash("נא להתחבר או להירשם במערכת כדי לממש את הרשאת הניהול שנשלחה אלייך.", "error")
        return redirect(url_for('login', next=url_for('claim_project_access', slug=slug, token=request.args.get('token'))))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    proj = cursor.fetchone()
    if not proj:
        conn.close()
        abort(404)

    cursor.execute("UPDATE projects SET owner_user_id = ? WHERE id = ?", (user['id'], proj['id']))
    sync_project_states(conn)
    conn.commit()
    conn.close()

    flash(f"הרשאת הניהול לפרויקט '{proj['title']}' שויכה בהצלחה לחשבונך!", "success")
    return redirect(url_for('manage_backers', slug=slug))

# --- REST API Endpoints ---

@app.route('/api/projects', methods=['GET'])
def api_get_projects():
    category = request.args.get('category', 'all')
    conn = get_db()
    cursor = conn.cursor()

    visibility = "1=1" if is_admin() else "is_active = 1"
    if category != 'all' and category in CATEGORIES:
        cursor.execute(f"SELECT * FROM projects WHERE {visibility} AND category = ? ORDER BY id DESC", (category,))
    else:
        cursor.execute(f"SELECT * FROM projects WHERE {visibility} ORDER BY id DESC")

    projects = [calculate_project_metrics(p) for p in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "count": len(projects), "projects": projects})

@app.route('/api/projects/<slug>', methods=['GET'])
def api_get_project(slug):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    p = cursor.fetchone()
    if not p or (not p['is_active'] and not is_admin()):
        conn.close()
        return jsonify({"success": False, "error": "Project not found"}), 404

    project = calculate_project_metrics(p)
    cursor.execute("SELECT * FROM rewards WHERE project_id = ?", (project["id"],))
    rewards = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return jsonify({"success": True, "project": project, "rewards": rewards})


@app.route('/api/projects/<slug>/rewards/<int:reward_id>', methods=['PATCH', 'PUT', 'POST'])
def api_update_reward(slug, reward_id):
    return edit_reward_tier(slug=slug, reward_id=reward_id)

@app.route('/api/stats', methods=['GET'])
def api_get_stats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total_projects, SUM(current_amount) as total_raised, SUM(backers_count) as total_backers FROM projects")
    stats = dict(cursor.fetchone())
    conn.close()
    return jsonify({"success": True, "stats": stats})

# Initialize database schema and initial seed data if needed
try:
    init_db()
    seed_db()
except Exception as e:
    print(f"DB Init warning: {e}")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
