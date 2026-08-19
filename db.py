import sqlite3
import os
import json
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "crowdfund.db"))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

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
        created_at TEXT NOT NULL
    );
    """)

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
        goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        goal_amount, current_amount, backers_count, days_total, start_date, end_date, status, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        now_str
    ))
    proj3_id = cursor.lastrowid

    cursor.execute("""
    INSERT INTO rewards (project_id, title, description, amount, estimated_delivery, quantity_limit, quantity_claimed, includes_shipping, created_at)
    VALUES 
    (?, 'הורדה דיגיטלית מוקדמת', 'קבלת שירי האלבום שבועיים לפני ההפצה הרשמית בספוטיפיי ואפל מיוזיק.', 36.0, 'ספטמבר 2026', NULL, 80, 0, ?),
    (?, 'דיסק פיזי + כרטיס למופע ההשקה', 'עותק דיסק מהודר עם חוברת מילים + כרטיס ישיבה זוגי למופע ההשקה בתל אביב או חיפה.', 180.0, 'אוקטובר 2026', 150, 65, 1, ?);
    """, (proj3_id, now_str, proj3_id, now_str))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    seed_db()
    print("Database initialized and seeded successfully!")
