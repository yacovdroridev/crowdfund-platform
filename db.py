import sqlite3
import os
import json
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "crowdfund.db"))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        phone TEXT,
        role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
        is_active BOOLEAN NOT NULL DEFAULT 1,
        last_login_at TEXT,
        created_at TEXT NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL COLLATE NOCASE UNIQUE,
        name TEXT NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    """)

    # Projects table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT NOT NULL,
        category TEXT NOT NULL,
        creator_name TEXT NOT NULL,
        creator_bio TEXT,
        creator_avatar TEXT,
        creator_email TEXT,
        creator_phone TEXT,
        cover_image TEXT NOT NULL,
        video_url TEXT,
        story_html TEXT NOT NULL,
        goal_amount REAL NOT NULL,
        current_amount REAL DEFAULT 0,
        backers_count INTEGER DEFAULT 0,
        days_total INTEGER DEFAULT 45,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        edit_pin TEXT DEFAULT '202600',
        created_at TEXT NOT NULL,
        owner_user_id INTEGER,
        is_active BOOLEAN NOT NULL DEFAULT 1,
        FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)

    # Ensure edit_pin exists on older databases
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN edit_pin TEXT DEFAULT '202600'")
    except sqlite3.OperationalError:
        pass

    for migration in (
        "ALTER TABLE projects ADD COLUMN owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE projects ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1",
    ):
        try:
            cursor.execute(migration)
        except sqlite3.OperationalError:
            pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS login_attempts (
        attempt_key TEXT PRIMARY KEY,
        failures INTEGER NOT NULL DEFAULT 0,
        window_started_at TEXT NOT NULL,
        blocked_until TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS passkey_credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        credential_id BLOB NOT NULL UNIQUE,
        public_key BLOB NOT NULL,
        sign_count INTEGER NOT NULL DEFAULT 0,
        transports TEXT,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_user_id INTEGER,
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT,
        details TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    default_categories = {
        "technology": "טכנולוגיה וחדשנות",
        "art_culture": "אמנות וספרות",
        "music": "מוזיקה והופעות",
        "community": "חברה וקהילה",
        "games": "משחקים ודיגיטל",
        "food": "קולינריה ומזון",
    }
    cursor.executemany(
        "INSERT OR IGNORE INTO categories (slug, name, is_active, created_at) VALUES (?, ?, 1, ?)",
        [(slug, name, now_str) for slug, name in default_categories.items()],
    )

    admin_email = os.environ.get("ADMIN_EMAIL", "yacov@drori.org").strip().lower()
    admin_password = os.environ.get("ADMIN_INITIAL_PASSWORD", "").strip() or "Admin123456!"
    if not cursor.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone():
        cursor.execute(
            """INSERT INTO users
               (email, password_hash, full_name, role, is_active, created_at)
               VALUES (?, ?, ?, 'admin', 1, ?)""",
            (admin_email, generate_password_hash(admin_password, method="scrypt"), "מנהל מערכת (יעקב דרורי)", now_str),
        )

    default_users = [
        ("demo@example.com", "User123456!", "משתמש הדגמה (יוצר)", "user"),
        ("backer@example.com", "User123456!", "תומך הדגמה", "user"),
    ]
    for email, password, name, role in default_users:
        if not cursor.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            cursor.execute(
                """INSERT INTO users
                   (email, password_hash, full_name, role, is_active, created_at)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (email, generate_password_hash(password, method="scrypt"), name, role, now_str),
            )

    # Rewards table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rewards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        amount REAL NOT NULL,
        estimated_delivery TEXT NOT NULL,
        quantity_limit INTEGER DEFAULT NULL,
        quantity_claimed INTEGER DEFAULT 0,
        includes_shipping BOOLEAN DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );
    """)

    # Pledges table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pledges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        reward_id INTEGER,
        amount REAL NOT NULL,
        tip_amount REAL DEFAULT 0,
        backer_name TEXT NOT NULL,
        backer_email TEXT NOT NULL,
        backer_phone TEXT,
        is_anonymous BOOLEAN DEFAULT 0,
        greeting_message TEXT,
        shipping_address TEXT,
        payment_status TEXT DEFAULT 'completed',
        payment_method TEXT DEFAULT 'credit_card',
        transaction_id TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY (reward_id) REFERENCES rewards(id) ON DELETE SET NULL
    );
    """)

    # Updates table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        author TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );
    """)

    # Comments table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        author_name TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    conn.close()

def seed_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM projects")
    if cursor.fetchone()[0] > 0:
        conn.close()
        return

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # Seed Project 1: AI Cyber Security / Smart Device
    end_date_1 = (now + timedelta(days=22)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    INSERT INTO projects (
        slug, title, subtitle, category, creator_name, creator_bio, creator_avatar,
        creator_email, creator_phone, cover_image, video_url, story_html,
        goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, edit_pin, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'synapse-guardian-iot',
        'SynApse Guardian: מכשיר הגנה מבוסס AI לרשת הביתית',
        'הגנו על כל מכשירי ה-Smart Home שלכם בעזרת מנוע AI מקומי שמזהה איומים בזמן אמת ללא פגיעה בפרטיות.',
        'technology',
        'יעקב דרורי',
        'מהנדס מערכות ומפתח פתרונות AI עם 15+ שנות ניסיון במערכות משובצות מחשב.',
        'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200&auto=format&fit=crop&q=80',
        'yacov@drori.org',
        '054-9103046',
        'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200&auto=format&fit=crop&q=80',
        'https://www.youtube.com/embed/dQw4w9WgXcQ',
        '<p class="mb-4">הבית החכם המודרני מכיל עשרות מכשירים מחוברים: מצלמות אבטחה, מזגנים חכמים, טלוויזיות ונורות. רובם פגיעים לחדירות ואינם מקבלים עדכוני אבטחה שוטפים.</p><h3 class="text-xl font-bold text-slate-800 mb-2">מה זה SynApse Guardian?</h3><p class="mb-4">קופסה חכמה המתחברת לראוטר הביתי בתוך 30 שניות ומריצה מודל AI מקומי (On-Edge) שמנתח אנומליות בתעבורה מבלי לקרוא את המידע האישי שלכם ומבלי לשלוח שום נתון לענן חיצוני!</p><h3 class="text-xl font-bold text-slate-800 mb-2">למה אנחנו צריכים את התמיכה שלכם?</h3><p>סיימנו בהצלחה את שלב אב הטיפוס ההנדסי. הגיוס מיועד לייצור סדרתי ראשון של 500 היחידות הראשונות והשגת תוי תקן בינלאומיים.</p>',
        120000.0,
        96500.0,
        248,
        45,
        now_str,
        end_date_1,
        'active',
        '202601',
        now_str
    ))
    proj1_id = cursor.lastrowid

    # Rewards for Project 1
    cursor.execute("""
    INSERT INTO rewards (project_id, title, description, amount, estimated_delivery, quantity_limit, quantity_claimed, includes_shipping, created_at)
    VALUES 
    (?, 'תמיכה בקהילה + מדבקות סייברפאנק', 'תודה אישית בדף התומכים הרשמי + סט מדבקות סייברפאנק ייחודיות הנשלחות בדואר.', 50.0, 'נובמבר 2026', NULL, 64, 1, ?),
    (?, 'יחידת Early Bird - SynApse Guardian', 'קבלו את יחידת ה-Guardian הראשונה במחיר השקה בלעדי לתומכים ראשונים (40% הנחה ממחיר השוק).', 390.0, 'דצמבר 2026', 100, 89, 1, ?),
    (?, 'ערכת Pro כפולה (2 מכשירים)', 'זוג מכשירים להגנה על הבית והמשרד + גישת פרימיום לעדכוני קושחה לכל החיים.', 690.0, 'דצמבר 2026', 50, 42, 1, ?),
    (?, 'חבילת מפתחים ואנג''לים + פגישה אישית', 'מכשיר Guardian + ערכת פיתוח SDK מלאה + שיחת ייעוץ אישית 1-על-1 עם המייסד.', 1800.0, 'נובמבר 2026', 10, 8, 1, ?);
    """, (proj1_id, now_str, proj1_id, now_str, proj1_id, now_str, proj1_id, now_str))

    # Updates for Project 1
    cursor.execute("""
    INSERT INTO updates (project_id, title, content, author, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (
        proj1_id,
        'הגענו ל-80% מהיעד! תודה ענקית לכל התומכים',
        'אנחנו נרגשים להודיע שתוך שבועיים בלבד חצינו את רף ה-90 אלף ש"ח. כרטיסי ה-PCB הראשונים כבר בדרך לבדיקות מעבדה.',
        'יעקב דרורי',
        (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    ))

    # Seed Project 2: Art & Culture
    end_date_2 = (now + timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    INSERT INTO projects (
        slug, title, subtitle, category, creator_name, creator_bio, creator_avatar,
        creator_email, creator_phone, cover_image, video_url, story_html,
        goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, edit_pin, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'nahariya-stories-book',
        'ספר האמנות וההיסטוריה: "גליל שלא הכרתם"',
        'ספר אלבומי מרהיב המתעד 100 שנות התיישבות, תרבות וסודות גנוזים לאורך חופי נהריה והגליל המערבי.',
        'art_culture',
        'מיכל שחר',
        'היסטוריונית וצלמת נוף גלילית.',
        'https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=200&auto=format&fit=crop&q=80',
        'michal@galilbooks.co.il',
        '052-8889999',
        'https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?w=1200&auto=format&fit=crop&q=80',
        None,
        '<p class="mb-4">במשך שלוש שנים נסענו בין מושבי הצפון, קיבוצי הגבול וסמטאות נהריה העתיקות כדי לאסוף תצלומים נדירים מיומני חלוצים וארכיונים משפחתיים.</p><p>התוצאה: אלבום כרומו עבה בן 280 עמודים עם סיפורים מרתקים, מפות עתיקות וצילומי רחפן פנורמיים מרהיבים.</p>',
        45000.0,
        52300.0,
        312,
        30,
        now_str,
        end_date_2,
        'successful',
        '202602',
        now_str
    ))
    proj2_id = cursor.lastrowid

    cursor.execute("""
    INSERT INTO rewards (project_id, title, description, amount, estimated_delivery, quantity_limit, quantity_claimed, includes_shipping, created_at)
    VALUES 
    (?, 'ספר דיגיטלי (PDF באיכות הדפסה)', 'קובץ האלבום הדיגיטלי המלא באיכות 4K לטאבלט ולמחשב.', 40.0, 'אוקטובר 2026', NULL, 110, 0, ?),
    (?, 'הספר המודפס בכריכה קשה עם הקדשה', 'עותק מהודר בכריכה קשה עם הטבעת זהב והקדשה אישית בכתב יד מהמחברת.', 130.0, 'נובמבר 2026', 300, 185, 1, ?),
    (?, 'מארז אספנים: 2 ספרים + פוסטר נוף ממוסגר', 'שני עותקים + הדפס אמנותי ממוסגר בגודל 50x70 ס"מ.', 320.0, 'נובמבר 2026', 50, 45, 1, ?);
    """, (proj2_id, now_str, proj2_id, now_str, proj2_id, now_str))

    # Seed Project 3: Music
    end_date_3 = (now + timedelta(days=34)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    INSERT INTO projects (
        slug, title, subtitle, category, creator_name, creator_bio, creator_avatar,
        creator_email, creator_phone, cover_image, video_url, story_html,
        goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, edit_pin, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'music-for-healing',
        'אלבום הבכורה והסיבוב המוזיקלי: "ניגוני תקווה"',
        'הקלטת אלבום אקוסטי מרפא והופעות פתוחות לקהל הרחב במרכזים קהילתיים ברחבי הארץ.',
        'music',
        'יונתן רז',
        'מוזיקאי, מלחין ונגן עוד וגיטרה.',
        'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=200&auto=format&fit=crop&q=80',
        'yonatan@music.org',
        '050-1234567',
        'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=1200&auto=format&fit=crop&q=80',
        None,
        '<p class="mb-4">המוזיקה היא הגשר החזק ביותר ללב האדם. בשנה האחרונה הופעתי בהתנדבות בפני אלפי אנשים וראיתי את כוחה המרפא של המנגינה.</p><p>אני מזמין אתכם להיות שותפים מלאים בהפקת אלבום אולפן מקצועי שיפיץ אור ותקווה.</p>',
        60000.0,
        34200.0,
        185,
        40,
        now_str,
        end_date_3,
        'active',
        '202603',
        now_str
    ))
    proj3_id = cursor.lastrowid

    cursor.execute("""
    INSERT INTO rewards (project_id, title, description, amount, estimated_delivery, quantity_limit, quantity_claimed, includes_shipping, created_at)
    VALUES 
    (?, 'הורדה דיגיטלית מוקדמת', 'קבלת שירי האלבום שבועיים לפני ההפצה הרשמית בספוטיפיי ואפל מיוזיק.', 36.0, 'ספטמבר 2026', NULL, 80, 0, ?),
    (?, 'דיסק פיזי + כרטיס למופע ההשקה', 'עותק דיסק מהודר עם חוברת מילים + כרטיס ישיבה זוגי למופע ההשקה בתל אביב או חיפה.', 180.0, 'אוקטובר 2026', 150, 65, 1, ?);
    """, (proj3_id, now_str, proj3_id, now_str))

    # Seed Project 4: Or Latefila / Kavana Batfila
    end_date_4 = (now + timedelta(days=21)).strftime("%Y-%m-%d %H:%M:%S")
    story_or_latefila = """
<div class="space-y-6 text-slate-800 leading-relaxed">
  <div class="bg-amber-50 border border-amber-200/80 rounded-2xl p-6 mb-6">
    <h3 class="text-xl font-bold text-amber-900 mb-2">להחזיר את הלב אל המילים ואל התפילה</h3>
    <p class="text-amber-800 text-sm">
      כולנו מכירים את הרגעים שבהם התפילה הופכת לקריאה מהירה מהשפתיים ולחוץ, כשהמחשבות נודדות וחסר החיבור העמוק. 
      פרויקט <strong>"אור לתפילה"</strong> נולד מתוך רצון עמוק להחזיר את השקט, המחשבה והכוונה הטהורה אל מילות הסידור והסליחות.
    </p>
  </div>

  <h3 class="text-2xl font-bold text-slate-900">מה כולל הפרויקט?</h3>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 my-4">
    <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
      <div class="text-emerald-600 font-bold text-base mb-1">🎙️ חדר שידור ומפגשים חיים</div>
      <p class="text-xs text-slate-600">וובינרים יומיים פעמיים ביום (10:00 בבוקר ו-20:00 בערב) ללימוד, כוונה משותפת והכנה לימים הנוראים.</p>
    </div>
    <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
      <div class="text-indigo-600 font-bold text-base mb-1">📱 אפליקציית כוונה והתבוננות</div>
      <p class="text-xs text-slate-600">פיתוח אפליקציה מתקדמת עם כוונות מילים, התבוננות באותיות, צלילי רקע מרגיעים ותרגול נשימה בתפילה.</p>
    </div>
  </div>

  <h3 class="text-2xl font-bold text-slate-900">מבט אל מתוך האפליקציה וחדר השידור</h3>
  <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 my-4">
    <img src="/static/images/tefila_screen_1.png" alt="מסך כוונה בתפילה" class="rounded-2xl border border-slate-200 shadow-md w-full object-cover">
    <img src="/static/images/tefila_screen_2.png" alt="מסך התבוננות והעמקה" class="rounded-2xl border border-slate-200 shadow-md w-full object-cover">
  </div>

  <h3 class="text-2xl font-bold text-slate-900">למה התמיכה שלכם קריטית עכשיו?</h3>
  <p>אנחנו נמצאים בימים שלפני חודש אלול והימים הנוראים. הגיוס מאפשר לנו:</p>
  <ul class="list-disc list-inside space-y-2 text-sm text-slate-700">
    <li>להפעיל את שרתי השידור החי בחינם לכלל הציבור פעמיים ביום.</li>
    <li>להשלים את פיתוח אפליקציית המובייל לכוונה בתפילה ולהפיץ אותה ללא עלות לתפוצה רחבה.</li>
    <li>להקליט קטעי שמע והנחיות כוונה איכותיות באולפן מקצועי.</li>
  </ul>
</div>
"""
    cursor.execute("""
    INSERT INTO projects (
        slug, title, subtitle, category, creator_name, creator_bio, creator_avatar,
        creator_email, creator_phone, cover_image, video_url, story_html,
        goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, edit_pin, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'or-latefila',
        'אור לתפילה: להחזיר את הלב והכוונה אל התפילה והסליחות',
        'פרויקט קהילתי וטכנולוגי להעמקת הכוונה, חיבור פנימי, שידורים חיים יומיים ואפליקציית מדיטציה והתבוננות בתפילה.',
        'community',
        'יעקב דרורי וצוות אור לתפילה',
        'יוזמי פרויקט "אור לתפילה" וסטודיו SynApse Zero, מפתחים טכנולוגיות בעלות ערך ומשמעות.',
        '/static/images/tefila_icon.png',
        'yacov@drori.org',
        '054-9103046',
        '/static/images/tefila_feature.png',
        None,
        story_or_latefila,
        50000.0,
        38450.0,
        148,
        30,
        now_str,
        end_date_4,
        'active',
        '770770',
        now_str
    ))
    proj4_id = cursor.lastrowid

    cursor.execute("""
    INSERT INTO rewards (project_id, title, description, amount, estimated_delivery, quantity_limit, quantity_claimed, includes_shipping, created_at)
    VALUES 
    (?, 'ברכת כוונה דיגיטלית + הקדשה בשידור החי', 'רישום שמכם לברכה בלוח השידור החי של הוובינר היומי + גישה מוקדמת להקלטות המפגשים.', 36.0, 'אלול תשפ"ו (ספטמבר 2026)', NULL, 54, 0, ?),
    (?, 'מארז כוונות הסליחות והתפילה (PDF + שמע)', 'מדריך דיגיטלי מעוצב לכוונה במילות התפילה + רצועות שמע להרפיה והתבוננות לפני התפילה.', 90.0, 'אלול תשפ"ו (ספטמבר 2026)', NULL, 42, 0, ?),
    (?, 'גישת פרימיום לכל החיים באפליקציית "כוונה בתפילה"', 'מנוי פרימיום לכל החיים באפליקציה: כל מסלולי הכוונה, צלילי רקע מרגיעים, והתאמה אישית.', 180.0, 'תשרי תשפ"ז (אוקטובר 2026)', 200, 38, 0, ?),
    (?, 'מפגש סדנה אישי בזום + הקדשת שידור בלעדית', 'השתתפות בסדנת עומק אינטימית על סודות הכוונה והתפילה + הקדשת שידור שלם לרפואה/הצלחה/לעילוי נשמת יקירכם.', 360.0, 'אלול תשפ"ו (ספטמבר 2026)', 30, 11, 0, ?),
    (?, 'שותף אור וחסות ראשית בפרויקט', 'חסות רשמית בראש חדר השידור ובאפליקציה, תעודת הוקרה מהודרת ממוסגרת, וכל התשורות הקודמות.', 1000.0, 'אלול תשפ"ו (ספטמבר 2026)', 10, 3, 1, ?);
    """, (proj4_id, now_str, proj4_id, now_str, proj4_id, now_str, proj4_id, now_str, proj4_id, now_str))

    cursor.execute("""
    INSERT INTO updates (project_id, title, content, author, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (
        proj4_id,
        'חצינו את 75% מהיעד! חדר השידור החי כבר באוויר',
        'תודה עצומה לכל 148 התומכים שהצטרפו אלינו. השידורים היומיים (10:00 בבוקר ו-20:00 בערב) זוכים להיענות מרגשת. אנחנו ממשיכים בכל הכוח עד להשגת היעד המלא!',
        'יעקב דרורי',
        now_str
    ))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    seed_db()
    print("Database initialized and seeded successfully!")
