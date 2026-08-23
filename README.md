# 🚀 HeadFund - Israeli Crowdfunding Platform (מימון המונים)

פלטפורמת גיוס המונים Fullstack בעברית מלאה (RTL) בהשראת Headstart, Kickstarter ו-Giveback.  
מאפשרת ליוזמים לפתוח קמפיינים, להגדיר מדרגות תמיכה (תשורות), לפרסם עדכונים, ולתומכים לבצע תמיכות ולסלוק תשלומים בזמן אמת.

![HeadFund Crowdfunding Platform](https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200&auto=format&fit=crop&q=80)

---

## 🌟 פיצ'רים מרכזיים (Features)

1. **דף גילוי ובית (Explore & Home):**
   - סינון מהיר לפי קטגוריות: טכנולוגיה, קהילה, מוזיקה, ספרים ואמנות, ועסקים.
   - סינון לפי סטטוס: הכל, פופולריים, לקראת סיום, ופרויקטים שהושלמו בהצלחה (100%+).
   - כרטיסי פרויקטים עשירים עם תמונת שער, מד התקדמות (Progress Bar) חי, סכום שגויס, יעד, מספר תומכים וימים שנותרו.

2. **דף קמפיין מלא (Headstart-style Campaign Page):**
   - נגן וידאו מובנה (YouTube / Vimeo) או גלריית תמונות שער באיכות גבוהה.
   - טאבים דינמיים: **סיפור הפרויקט (Story)**, **עדכוני יוזם (Updates)**, ו**תגובות ותומכים (Backers & Comments)**.
   - **סרגל צד דביק (Sticky Sidebar)** עם סטטיסטיקות גיוס מרכזיות וכפתור תמיכה חופשית.
   - **מדרגות תמיכה ותשורות (Reward Tiers):**
     - כותרת, תיאור, מחיר בש"ח, כמות שנותרה / אזלה, זמן אספקה משוער, וציון האם כולל משלוח.
     - כפתור אינטראקטיבי לבחירת תשורה הפותח מודאל סליקה ישיר.

3. **זרימת תמיכה וסליקה (Checkout & Pledge Flow):**
   - מודאל אינטראקטיבי לבחירת תשורה / תמיכה חופשית + הוספת טיפ למערכת.
   - מילוי פרטי תומך, כתובת למשלוח, ברכה אישית, ואפשרות לתמיכה בעילום שם (אנונימי).
   - הדמיית סליקה מאובטחת עם יצירת מספר אישור עסקה חד-ערכי (Transaction ID) ועדכון סטטיסטיקות הקמפיין ב-DB.
   - הכנה מובנית לחיבור ספקי סליקה ישראליים (משולם / טרנזילה / מקס / קארדקום / Stripe).

4. **אשף הקמת פרויקט (Campaign Wizard):**
   - יצירת קמפיין חדש ב-4 שלבים אינטואיטיביים.
   - הוספה והסרה דינמית של מדרגות תמיכה ותשורות ללא הגבלה.
   - הוספת קישורי וידאו, תמונת שער, הגדרת סכום יעד וימי גיוס.

5. **לוח בקרה וניהול (Admin & Creator Dashboard):**
   - מדדי ביצועים: סך כל הגיוסים (₪), סך התומכים, אחוז הצלחה ומספר הפרויקטים.
   - טבלת ניהול פרויקטים ומעקב אחר אחוזי הגיוס.
   - יומן עסקאות ותמיכות מלא עם פרטי תומכים, תשורות, וסטטוסי סליקה.

6. **REST API מלא:**
   - `GET /api/projects` - קבלת כל הפרויקטים (תמיכה בסינון לפי קטגוריה וחיפוש).
   - `GET /api/projects/<slug>` - קבלת פרטי פרויקט, תשורות, עדכונים ותגובות.
   - `POST /api/projects` - יצירת פרויקט חדש דרך API.
   - `POST /api/projects/<slug>/pledge` - ביצוע תמיכה וסליקה.
   - `POST /api/projects/<slug>/updates` - פרסום עדכון לתומכים.
   - `GET /api/stats` - סטטיסטיקות מערכת כלליות.

---

## 🛠️ טכנולוגיות וארכיטקטורה

- **Backend:** Python 3.11 + Flask (RESTful API, modular controllers).
- **Database:** SQLite עם foreign keys, טבלאות `projects`, `rewards`, `pledges`, `updates`, `comments`.
- **Frontend:** HTML5 סמנטי, Tailwind CSS (RTL-first), Lucide Icons, Vanilla JS / Alpine-ready.
- **Testing:** Pytest - 100% כיסוי לכל הנתיבים, בדיקות סליקה ועדכון יתרות.

---

## 💻 הרצה מקומית (Local Development)

### 1. שכפול והתקנת תלויות:
```bash
# כניסה לתיקיית הפרויקט
cd /home/yacov/Projects/crowdfund-platform

# הפעלת הסביבה הווירטואלית
source .venv/bin/activate

# התקנת תלויות
pip install -r requirements.txt
```

### 2. אתחול והזנת נתוני דוגמה (Seed Data):
```bash
python db.py
```

### 3. הרצת השרת:
```bash
python app.py
```
האתר יהיה זמין בדפדפן בכתובת: **http://127.0.0.1:5000**

### 🔑 חשבונות מובנים להתחברות (Default Basic Accounts):
המערכת מגיעה מוגדרת מראש עם חשבונות הדגמה זמינים לכניסה מהירה (בשרת המקומי וב-Render):

| תפקיד (Role) | אימייל (Email) | סיסמה (Password) | הרשאות |
| :--- | :--- | :--- | :--- |
| **מנהל (Admin)** | `yacov@drori.org` | `Admin123456!` | גישה מלאה ללוח הניהול (`/dashboard`), אישור וניהול קמפיינים, ניהול קטגוריות |
| **יוצר (Creator)** | `demo@example.com` | `User123456!` | פתיחת קמפיינים חדשים (`/create`), עריכת קמפיין |
| **תומך (Backer)** | `backer@example.com` | `User123456!` | ביצוע תמיכות בקמפיינים וסליקה |

---

## 🧪 הרצת בדיקות אוטומטיות (Tests)
```bash
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

---

## ☁️ פריסה לענן (Cloud Deployment Guide)

ניתן לפרוס את הפרויקט בקלות לכל שירות ענן:

### אופציה 1: Docker (מומלץ לכל ענן או שרת VPS)
הפרויקט כולל תמיכה בהרצה פשוטה כקונטיינר עם Gunicorn.

### אופציה 2: Render / Railway / Fly.io
1. חברו את ה-Repository מ-GitHub לשירות.
2. הגדירו **Build Command:** `pip install -r requirements.txt`
3. הגדירו **Start Command:** `gunicorn -w 4 -b 0.0.0.0:$PORT app:app`

---

## 💳 חיבור עתידי לסליקת אשראי ישראלית (Payment Gateways)
במודול `app.py` בנתיב `/project/<slug>/pledge`, הפונקציה מוכנה לקבלת Webhook / API של חברות סליקה:
- **Meshulam (משולם):** יצירת טופס תשלום ב-iFrame או הפניה, וחזרה ב-Notify URL.
- **Tranzila / Cardcom / Pelecard:** אינטגרציה ישירה באמצעות Rest API.
- **Stripe:** הוספת Stripe Checkout Session.

---

## 📄 רישיון
MIT License © 2026 SynApse Zero / Yacov Drori.
