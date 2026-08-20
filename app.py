import os
import re
import uuid
import secrets
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, abort, session
from db import get_db, init_db, seed_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "crowdfund-super-secret-key-2026")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "drori2026")

def is_admin():
    return session.get('is_admin', False)

def is_project_authorized(slug):
    if is_admin():
        return True
    authorized_list = session.get('authorized_projects', [])
    return slug in authorized_list

def authorize_project(slug):
    authorized = session.get('authorized_projects', [])
    if slug not in authorized:
        authorized.append(slug)
        session['authorized_projects'] = authorized

@app.context_processor
def inject_auth_context():
    return {
        'is_admin': is_admin(),
        'authorized_projects': session.get('authorized_projects', [])
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
    return p

# --- HTML Routes ---

@app.route('/')
def index():
    category = request.args.get('category', 'all')
    status_filter = request.args.get('status', 'all')
    search_query = request.args.get('q', '').strip()

    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM projects WHERE 1=1"
    params = []

    if category != 'all' and category in CATEGORIES:
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
        categories=CATEGORIES,
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

    if not raw_project:
        conn.close()
        abort(404)

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

    conn.close()

    return render_template(
        'project.html',
        project=project,
        rewards=rewards,
        updates=updates,
        comments=comments,
        backers=backers
    )

@app.route('/project/<slug>/pledge', methods=['POST'])
def submit_pledge(slug):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    project = cursor.fetchone()

    if not project:
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
    payment_method = request.form.get('payment_method', 'credit_card').strip()
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    transaction_id = f"TXN-{uuid.uuid4().hex[:10].upper()}"

    # Insert pledge
    cursor.execute("""
    INSERT INTO pledges (
        project_id, reward_id, amount, tip_amount, backer_name, backer_email,
        backer_phone, is_anonymous, greeting_message, shipping_address,
        payment_status, payment_method, transaction_id, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?)
    """, (
        project["id"], reward_id, total_charge, tip_amount, backer_name,
        backer_email, backer_phone, is_anonymous, greeting_message,
        shipping_address, payment_method, transaction_id, now_str
    ))
    pledge_id = cursor.lastrowid

    # Update project stats
    new_amount = project["current_amount"] + total_charge
    new_backers = project["backers_count"] + 1
    new_status = 'successful' if new_amount >= project["goal_amount"] else project["status"]

    cursor.execute("""
    UPDATE projects 
    SET current_amount = ?, backers_count = ?, status = ?
    WHERE id = ?
    """, (new_amount, new_backers, new_status, project["id"]))

    # Update reward claimed count if applicable
    if reward_id:
        cursor.execute("UPDATE rewards SET quantity_claimed = quantity_claimed + 1 WHERE id = ?", (reward_id,))

    # Add backer greeting as comment if present
    if greeting_message:
        cursor.execute("""
        INSERT INTO comments (project_id, author_name, content, created_at)
        VALUES (?, ?, ?, ?)
        """, (project["id"], backer_name if not is_anonymous else "תומך אנונימי", greeting_message, now_str))

    conn.commit()
    conn.close()

    return redirect(url_for('pledge_success', pledge_id=pledge_id))

@app.route('/success/<int:pledge_id>')
def pledge_success(pledge_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT p.*, pr.title as project_title, pr.slug as project_slug, pr.cover_image as project_cover,
           r.title as reward_title
    FROM pledges p
    JOIN projects pr ON p.project_id = pr.id
    LEFT JOIN rewards r ON p.reward_id = r.id
    WHERE p.id = ?
    """, (pledge_id,))
    pledge = cursor.fetchone()
    conn.close()

    if not pledge:
        abort(404)

    return render_template('success.html', pledge=dict(pledge))

@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get('next', url_for('dashboard'))
    if request.method == 'POST':
        pwd = request.form.get('password', '').strip()
        if pwd == ADMIN_PASSWORD:
            session['is_admin'] = True
            flash("התחברת בהצלחה כמנהל מערכת!", "success")
            return redirect(next_url or url_for('dashboard'))
        else:
            flash("סיסמת מנהל שגויה. נסו שנית.", "error")
    return render_template('login.html', next_url=next_url)

@app.route('/logout')
def logout():
    session.clear()
    flash("התנתקת מהמערכת בהצלחה.", "info")
    return redirect(url_for('index'))

@app.route('/project/<slug>/auth', methods=['GET', 'POST'])
def project_auth(slug):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    project = cursor.fetchone()
    conn.close()

    if not project:
        abort(404)

    if request.method == 'POST':
        auth_key = request.form.get('auth_key', '').strip()
        project_pin = str(project['edit_pin'] or '202600').strip()

        if auth_key == ADMIN_PASSWORD:
            session['is_admin'] = True
            authorize_project(slug)
            flash("אומת בהצלחה כמנהל מערכת!", "success")
            return redirect(url_for('edit_project', slug=slug))
        elif auth_key == project_pin:
            authorize_project(slug)
            flash("קוד עריכה אומת בהצלחה!", "success")
            return redirect(url_for('edit_project', slug=slug))
        else:
            flash("קוד PIN או סיסמה שגויים. נסו שוב.", "error")

    return render_template('project_auth.html', project=dict(project))

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
        flash("נדרש אימות קוד PIN או סיסמת מנהל לצורך עריכת הפרויקט.", "error")
        return redirect(url_for('project_auth', slug=slug))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subtitle = request.form.get('subtitle', '').strip()
        category = request.form.get('category', 'technology')
        goal_amount = float(request.form.get('goal_amount', project['goal_amount']))
        creator_name = request.form.get('creator_name', '').strip()
        creator_email = request.form.get('creator_email', '').strip()
        creator_phone = request.form.get('creator_phone', '').strip()
        creator_bio = request.form.get('creator_bio', '').strip()
        creator_avatar = request.form.get('creator_avatar', '').strip() or project['creator_avatar']
        cover_image = request.form.get('cover_image', '').strip() or project['cover_image']
        video_url = request.form.get('video_url', '').strip() or None
        story_html = request.form.get('story_html', '').strip()

        cursor.execute("""
        UPDATE projects SET
            title = ?, subtitle = ?, category = ?, goal_amount = ?,
            creator_name = ?, creator_email = ?, creator_phone = ?, creator_bio = ?,
            creator_avatar = ?, cover_image = ?, video_url = ?, story_html = ?
        WHERE id = ?
        """, (
            title, subtitle, category, goal_amount,
            creator_name, creator_email, creator_phone, creator_bio,
            creator_avatar, cover_image, video_url, story_html,
            project['id']
        ))

        # Handle rewards: remove old ones without pledges and update/insert
        tier_titles = request.form.getlist('reward_title[]')
        tier_amounts = request.form.getlist('reward_amount[]')
        tier_descriptions = request.form.getlist('reward_desc[]')
        tier_deliveries = request.form.getlist('reward_delivery[]')
        tier_limits = request.form.getlist('reward_limit[]')
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Clear existing rewards that can be safely recreated or modified
        cursor.execute("DELETE FROM rewards WHERE project_id = ? AND quantity_claimed = 0", (project['id'],))

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
                        project['id'], tier_titles[i].strip(), tier_descriptions[i].strip() if i < len(tier_descriptions) else "",
                        amt, tier_deliveries[i].strip() if i < len(tier_deliveries) and tier_deliveries[i] else "בקרוב",
                        lim, now_str
                    ))
                except ValueError:
                    continue

        conn.commit()
        conn.close()

        flash("פרטי הפרויקט עודכנו בהצלחה!", "success")
        return redirect(url_for('project_detail', slug=slug))

    cursor.execute("SELECT * FROM rewards WHERE project_id = ? ORDER BY amount ASC", (project['id'],))
    rewards = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return render_template('edit.html', project=dict(project), rewards=rewards, categories=CATEGORIES)

@app.route('/create', methods=['GET', 'POST'])
def create_project():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subtitle = request.form.get('subtitle', '').strip()
        category = request.form.get('category', 'technology')
        goal_amount = float(request.form.get('goal_amount', 10000))
        days_total = int(request.form.get('days_total', 30))
        creator_name = request.form.get('creator_name', '').strip()
        creator_email = request.form.get('creator_email', '').strip()
        creator_phone = request.form.get('creator_phone', '').strip()
        creator_bio = request.form.get('creator_bio', '').strip()
        creator_avatar = request.form.get('creator_avatar', '').strip() or 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200'
        cover_image = request.form.get('cover_image', '').strip() or 'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200'
        video_url = request.form.get('video_url', '').strip() or None
        story_html = request.form.get('story_html', '').strip()
        edit_pin = request.form.get('edit_pin', '').strip() or str(secrets.randbelow(900000) + 100000)

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
            goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, edit_pin, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, 'active', ?, ?)
        """, (
            slug, title, subtitle, category, creator_name, creator_bio, creator_avatar,
            creator_email, creator_phone, cover_image, video_url, story_html,
            goal_amount, days_total, now_str, end_date, edit_pin, now_str
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

        # Automatically authorize creator in session
        authorize_project(slug)

        flash(f"הפרויקט פורסם בהצלחה! קוד ה-PIN האישי שלך לעריכה הוא: {edit_pin}", "success")
        return redirect(url_for('project_detail', slug=slug))

    return render_template('create.html', categories=CATEGORIES)

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

    cursor.execute("""
    SELECT p.*, pr.title as project_title, pr.slug as project_slug
    FROM pledges p
    JOIN projects pr ON p.project_id = pr.id
    ORDER BY p.id DESC LIMIT 50
    """)
    recent_pledges = [dict(p) for p in cursor.fetchall()]

    conn.close()

    projects = [calculate_project_metrics(p) for p in raw_projects]
    total_raised = sum(p["current_amount"] for p in projects)
    total_backers = sum(p["backers_count"] for p in projects)
    successful_count = len([p for p in projects if p["percent"] >= 100])

    return render_template(
        'dashboard.html',
        projects=projects,
        recent_pledges=recent_pledges,
        total_raised=total_raised,
        total_backers=total_backers,
        successful_count=successful_count,
        total_projects=len(projects)
    )

@app.route('/project/<slug>/add-update', methods=['POST'])
def add_project_update(slug):
    title = request.form.get('update_title', '').strip()
    content = request.form.get('update_content', '').strip()
    author = request.form.get('update_author', 'יוזם הפרויקט').strip()

    if not title or not content:
        flash("יש למלא כותרת ותוכן עדכון", "error")
        return redirect(url_for('project_detail', slug=slug))

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

# --- REST API Endpoints ---

@app.route('/api/projects', methods=['GET'])
def api_get_projects():
    category = request.args.get('category', 'all')
    conn = get_db()
    cursor = conn.cursor()

    if category != 'all' and category in CATEGORIES:
        cursor.execute("SELECT * FROM projects WHERE category = ? ORDER BY id DESC", (category,))
    else:
        cursor.execute("SELECT * FROM projects ORDER BY id DESC")

    projects = [calculate_project_metrics(p) for p in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "count": len(projects), "projects": projects})

@app.route('/api/projects/<slug>', methods=['GET'])
def api_get_project(slug):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
    p = cursor.fetchone()
    if not p:
        conn.close()
        return jsonify({"success": False, "error": "Project not found"}), 404

    project = calculate_project_metrics(p)
    cursor.execute("SELECT * FROM rewards WHERE project_id = ?", (project["id"],))
    rewards = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return jsonify({"success": True, "project": project, "rewards": rewards})

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
