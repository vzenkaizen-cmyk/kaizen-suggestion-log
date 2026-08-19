"""
Vidullanka PLC — Kaizen Suggestion Log
PostgreSQL-backed Streamlit app.
 
Access model
------------
Staff / GEMBA worker : NO account or password required. They must enter
                        their name and department when submitting. They cannot
                        browse, search, or view other suggestions.
 
Approver              : Signs in with a username + password. On an
                        approver's very first sign-in there is no password
                        set yet, so the app asks them to create their own
                        password on the spot (self-service setup). They can
                        change it later from "Manage Approvers".
 
Database
--------
All data is stored in PostgreSQL (via SQLAlchemy + psycopg2). Configure the
connection in `.streamlit/secrets.toml`:
 
    [postgres]
    host     = "your-postgres-host"
    port     = 5432
    user     = "kaizen_user"
    password = "your-password"
    database = "kaizen_db"
    sslmode  = "require"        # most hosted Postgres providers require this
 
The app creates its own tables on first run (CREATE TABLE IF NOT EXISTS), so
you only need an empty database + a user with privileges on it beforehand.
See the step-by-step setup guide further down for exactly how to get that
with a free hosted Postgres provider (Neon is recommended for Streamlit
Community Cloud deployments since it doesn't pause on inactivity).
 
If [postgres] isn't configured, the app falls back to a local SQLite file
(kaizen.db) so you can still run it locally without any setup.
 
Optional integrations (safe to leave unset — the app runs fine without them)
------------------------------------------------------------------------
GROQ_API_KEY                       -> AI-assisted category / quality note on submit
APP_URL                            -> used to build the QR code for phone submissions
"""
 
import hashlib
import io
from datetime import date, datetime
 
import pandas as pd
import plotly.express as px
import qrcode
import sqlalchemy as sa
import streamlit as st
from sqlalchemy import text
 
# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="V-ZEN Kaizen Suggestions", page_icon="💡", layout="wide")
 
# HOF and plant/site choices shown to suggestion submitters.
# Add/remove site names here if Vidullanka has additional sites.
DEPARTMENTS = [
    "HOF", "BBO", "BTO", "BKN", "EME", "MGT",
    "GNT", "HS1", "HS2", "LKM", "MVB", "ORK",
    "RDP", "UDW", "VBL", "WMB",
]
CATEGORIES = ["Productivity", "Quality", "Cost", "Delivery", "Safety", "Morale"]
TECHNIQUES = [
    "None / General Kaizen", "Flow Chart", "Check Sheets", "Cube Kaizen or System Kaizen",
    "Histogram & Scatter Diagram", "Pareto Chart", "Cause & Effect", "5S", "PDCA", "Other",
]
STATUSES = ["Pending", "Approved", "Rejected", "Implemented"]
 
# Reserved approver accounts. They are seeded with NO password — each person
# sets their own password the first time they sign in.
RESERVED_APPROVERS = [
    ("roshan", "Roshan"),
    ("akthar", "Akthar"),
    ("mafas", "Mafas"),
]
 
# One-time migration: existing reserved approvers will be asked to choose
# their own password. This runs only once and is recorded in app_settings.
PREFERRED_PASSWORD_MIGRATION = "preferred-password-v1"
 
BRAND_GREEN = "#c9dfd4"
BRAND_TAN = "#e8e2d4"
 
 
def inject_styles():
    """Application-wide visual styling."""
    st.markdown(
        """
        <style>
        /* Force a readable light interface even when the user's browser/OS is in dark mode. */
        :root { color-scheme: light !important; }
        html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"] {
            background: #ffffff !important;
            color: #1e2e39 !important;
        }
        .stApp { background: linear-gradient(180deg, #f7faf8 0%, #ffffff 42%) !important; }
        [data-testid="stHeader"] { background: rgba(255,255,255,0.96) !important; }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f1f7f4 0%, #ffffff 72%) !important;
            border-right: 1px solid #dce8e2 !important;
        }
        [data-testid="stSidebar"] .block-container { padding-top: 1.5rem; }
        [data-testid="stSidebar"] * { color: #1e2e39 !important; }
        .stMarkdown, .stCaption, .stText, p, label, [data-testid="stWidgetLabel"] { color: #1e2e39 !important; }
        .hero { background: linear-gradient(135deg, #c9dfd4 0%, #e9f3ee 100%); border: 1px solid #bdd5ca; border-radius: 20px; padding: 24px 28px; margin: 0 0 20px 0; box-shadow: 0 8px 30px rgba(35,65,52,0.08); }
        .hero-kicker { color: #53675e !important; font-size: 11px; font-weight: 700; letter-spacing: 2.2px; margin-bottom: 5px; }
        .hero-title { color: #1e2e39 !important; font-size: clamp(28px, 4vw, 42px); line-height: 1.05; font-weight: 800; margin: 0; }
        .hero-subtitle { color: #4d5d58 !important; font-size: 13px; margin: 7px 0 0 0; }
        .hero-badge { display: inline-block; background: #22314a; color: #fff !important; padding: 7px 12px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .7px; }
        div[data-testid="stMetric"] { background: #fff !important; border: 1px solid #e4ece8; padding: 14px 16px; border-radius: 14px; box-shadow: 0 4px 16px rgba(31,52,43,0.04); }
        div[data-testid="stMetric"] * { color: #1e2e39 !important; }
        input, textarea, [data-baseweb="select"] > div, [data-baseweb="input"] > div {
            background-color: #ffffff !important; color: #1e2e39 !important;
            border-color: #cfded7 !important;
        }
        input::placeholder, textarea::placeholder { color: #718078 !important; opacity: 1 !important; }
        [data-baseweb="select"] span, [data-baseweb="select"] div { color: #1e2e39 !important; }
        [data-baseweb="popover"], [data-baseweb="popover"] * { background: #ffffff !important; color: #1e2e39 !important; }
        [role="listbox"], [role="option"] { background: #ffffff !important; color: #1e2e39 !important; }
        [role="option"]:hover { background: #e9f3ee !important; }
        [data-testid="stSidebar"] [role="radiogroup"] label, [data-testid="stSidebar"] [role="radiogroup"] label * { color: #1e2e39 !important; }
        .stButton > button, .stFormSubmitButton > button { border-radius: 10px; min-height: 42px; background: #ffffff !important; color: #1e2e39 !important; border: 1px solid #cfded7 !important; }
        .stButton > button:hover, .stFormSubmitButton > button:hover { border-color: #7da994 !important; }
        button[kind="primary"] { border-radius: 10px !important; font-weight: 700 !important; background: #1f6f50 !important; color: #ffffff !important; border-color: #1f6f50 !important; }
        [data-testid="stExpander"] { background: #ffffff !important; border-color: #dce8e2 !important; }
        [data-testid="stAlert"] { color: #1e2e39 !important; }
        </style>
        """, unsafe_allow_html=True,
    )
 
# --------------------------------------------------------------------------- #
# DB engine (PostgreSQL via SQLAlchemy + psycopg2)
# --------------------------------------------------------------------------- #
@st.cache_resource
def get_engine():
    """
    Create the database engine.
 
    PostgreSQL is used when [postgres] exists in secrets.toml.
    SQLite is used only as a local fallback when [postgres] is not configured.
    This prevents the raw KeyError: st.secrets has no key "postgres".
    """
    pg_cfg = st.secrets.get("postgres")
 
    if pg_cfg:
        required = ["host", "user", "password", "database"]
        missing = [k for k in required if not pg_cfg.get(k)]
        if missing:
            raise RuntimeError(
                "PostgreSQL is configured but these values are missing in "
                "[postgres]: " + ", ".join(missing)
            )
 
        connect_args = {}
        sslmode = pg_cfg.get("sslmode")
        if sslmode:
            connect_args["sslmode"] = sslmode
 
        url = sa.URL.create(
            "postgresql+psycopg2",
            username=pg_cfg["user"],
            password=pg_cfg["password"],
            host=pg_cfg["host"],
            port=int(pg_cfg.get("port", 5432)),
            database=pg_cfg["database"],
        )
        return sa.create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=280,
            pool_size=5,
            max_overflow=5,
            connect_args=connect_args,
        )
 
    # Local development fallback. For deployment, configure [postgres].
    return sa.create_engine(
        "sqlite:///kaizen.db",
        connect_args={"check_same_thread": False},
    )
 
 
def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()
 
 
def init_db():
    engine = get_engine()
    is_sqlite = engine.dialect.name == "sqlite"
 
    if is_sqlite:
        suggestions_ddl = """
            CREATE TABLE IF NOT EXISTS suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_no INTEGER NULL,
                submitted_by VARCHAR(150) NOT NULL,
                employee_type VARCHAR(50),
                department VARCHAR(100) NOT NULL,
                date_submitted DATE,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                category VARCHAR(255),
                technique_used VARCHAR(100),
                status VARCHAR(20) DEFAULT 'Pending',
                tangible_value DECIMAL(14,2) DEFAULT 0,
                reward VARCHAR(255),
                reward_value DECIMAL(14,2) DEFAULT 0,
                approver VARCHAR(150),
                approval_date DATETIME NULL,
                date_implemented DATETIME NULL,
                ai_note VARCHAR(500),
                created_at DATETIME
            )
        """
        approvers_ddl = """
            CREATE TABLE IF NOT EXISTS approvers (
                username VARCHAR(50) PRIMARY KEY,
                display_name VARCHAR(100) NOT NULL,
                password_hash VARCHAR(255) NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """
    else:
        # PostgreSQL
        suggestions_ddl = """
            CREATE TABLE IF NOT EXISTS suggestions (
                id SERIAL PRIMARY KEY,
                legacy_no INTEGER NULL,
                submitted_by VARCHAR(150) NOT NULL,
                employee_type VARCHAR(50),
                department VARCHAR(100) NOT NULL,
                date_submitted DATE,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                category VARCHAR(255),
                technique_used VARCHAR(100),
                status VARCHAR(20) DEFAULT 'Pending',
                tangible_value DECIMAL(14,2) DEFAULT 0,
                reward VARCHAR(255),
                reward_value DECIMAL(14,2) DEFAULT 0,
                approver VARCHAR(150),
                approval_date TIMESTAMP NULL,
                date_implemented DATE NULL,
                ai_note VARCHAR(500),
                created_at TIMESTAMP
            )
        """
        approvers_ddl = """
            CREATE TABLE IF NOT EXISTS approvers (
                username VARCHAR(50) PRIMARY KEY,
                display_name VARCHAR(100) NOT NULL,
                password_hash VARCHAR(255) NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE
            )
        """
 
    app_settings_ddl = """
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key VARCHAR(100) PRIMARY KEY,
            setting_value VARCHAR(255) NOT NULL
        )
    """
 
    with engine.begin() as conn:
        conn.execute(text(suggestions_ddl))
        conn.execute(text(approvers_ddl))
        conn.execute(text(app_settings_ddl))

 
        existing = conn.execute(text("SELECT COUNT(*) FROM approvers")).scalar()
        if existing == 0:
            for username, display_name in RESERVED_APPROVERS:
                conn.execute(
                    text(
                        """INSERT INTO approvers
                           (username, display_name, password_hash, active)
                           VALUES (:u, :d, NULL, :active)"""
                    ),
                    {"u": username, "d": display_name, "active": True},
                )
 
        # One-time conversion of the old "admin-given password" state.
        # Existing reserved accounts are cleared so each approver can choose
        # their own password on the next sign-in. The marker prevents this
        # from happening again on later app starts.
        migration_done = conn.execute(
            text("SELECT setting_value FROM app_settings WHERE setting_key=:k"),
            {"k": PREFERRED_PASSWORD_MIGRATION},
        ).scalar()
        if migration_done != "done":
            for username, _display_name in RESERVED_APPROVERS:
                conn.execute(
                    text("UPDATE approvers SET password_hash=NULL WHERE username=:u"),
                    {"u": username},
                )
            conn.execute(
                text("INSERT INTO app_settings (setting_key, setting_value) VALUES (:k, 'done')"),
                {"k": PREFERRED_PASSWORD_MIGRATION},
            )

    ensure_optional_columns()


# --------------------------------------------------------------------------- #
# Suggestions
# --------------------------------------------------------------------------- #
def df_suggestions() -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(text("SELECT * FROM suggestions ORDER BY id DESC"), engine)


def ensure_optional_columns():
    """Add newer Excel-history fields to an existing database safely."""
    engine=get_engine()
    required={
        "legacy_no": "INTEGER",
        "date_implemented": "DATE" if engine.dialect.name=="postgresql" else "DATETIME",
        "reward_value": "DECIMAL(14,2)",
    }
    existing={c["name"] for c in sa.inspect(engine).get_columns("suggestions")}
    with engine.begin() as conn:
        for name, dtype in required.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE suggestions ADD COLUMN {name} {dtype}"))

def _clean_excel_value(value):
    if pd.isna(value): return None
    if isinstance(value,str):
        value=value.strip()
        return value if value else None
    return value

def _normalize_status(value):
    value=(_clean_excel_value(value) or "Pending").strip().title()
    return value if value in STATUSES else "Pending"

def _normalize_employee_type(value):
    value=(_clean_excel_value(value) or "Staff").strip()
    low=value.lower()
    if "gemba" in low: return "GEMBA Worker"
    if "staff" in low: return "Staff"
    return value

def _read_uploaded_excel(uploaded_file) -> pd.DataFrame:
    """Read an uploaded XLSX workbook reliably from bytes."""
    data = uploaded_file.getvalue()
    if not data:
        raise ValueError("The uploaded Excel file is empty.")
    try:
        xls = pd.ExcelFile(io.BytesIO(data), engine="openpyxl")
    except ImportError as exc:
        raise RuntimeError(
            "Excel import requires openpyxl. Add openpyxl>=3.1 to requirements.txt and redeploy."
        ) from exc
    except Exception as exc:
        raise ValueError(f"The workbook could not be opened: {exc}") from exc
    sheet = "Kaizen Suggestions" if "Kaizen Suggestions" in xls.sheet_names else xls.sheet_names[0]
    return pd.read_excel(io.BytesIO(data), sheet_name=sheet, engine="openpyxl")

def import_legacy_excel(uploaded_file):
    """Import every individual suggestion row from the legacy workbook.

    The workbook's 16 suggestion columns are mapped into the database. The
    Summary sheet is intentionally not imported because the dashboard should
    calculate totals from the individual suggestion records.
    Repeated imports are safe: duplicate rows are skipped.
    """
    raw = _read_uploaded_excel(uploaded_file).copy()

    required = ["No", "Name", "Role", "Date Submitted", "Title"]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise ValueError("Missing required Excel columns: " + ", ".join(missing))

    # Make optional columns available even if an older workbook omitted them.
    optional_columns = [
        "Department", "Description", "Categories (PQCDSM)", "Technique Used",
        "Status", "Decided By", "Decision Date", "Date Implemented",
        "Tangible Value (LKR/yr)", "Reward Given", "Reward Value (LKR)",
    ]
    for col in optional_columns:
        if col not in raw.columns:
            raw[col] = None

    engine = get_engine()
    existing_df = pd.read_sql(
        text("""SELECT legacy_no, submitted_by, date_submitted, title, description
               FROM suggestions"""),
        engine,
    )

    existing_keys = set()
    existing_legacy_nos = set()
    for _, r in existing_df.iterrows():
        legacy_no = pd.to_numeric(r.get("legacy_no"), errors="coerce")
        if pd.notna(legacy_no):
            existing_legacy_nos.add(int(legacy_no))
        d = pd.to_datetime(r.get("date_submitted"), errors="coerce")
        existing_keys.add((
            str(r.get("submitted_by") or "").strip().lower(),
            str(d.date() if pd.notna(d) else ""),
            str(r.get("title") or "").strip().lower(),
            str(r.get("description") or "").strip().lower(),
        ))

    imported = skipped = 0
    errors = []

    # Import ALL rows, not just the first 10 shown in the preview.
    for n, r in raw.iterrows():
        excel_row = n + 2
        try:
            legacy_raw = pd.to_numeric(r.get("No"), errors="coerce")
            legacy_no = int(legacy_raw) if pd.notna(legacy_raw) else None

            submitted_by = _clean_excel_value(r.get("Name")) or "Not specified"
            employee_type = _normalize_employee_type(r.get("Role"))
            department = _clean_excel_value(r.get("Department")) or "Not specified"

            parsed = pd.to_datetime(r.get("Date Submitted"), errors="coerce")
            date_submitted = parsed.date() if pd.notna(parsed) else None

            title = _clean_excel_value(r.get("Title"))
            description = _clean_excel_value(r.get("Description"))
            if not title:
                skipped += 1
                errors.append(f"Excel row {excel_row}: Title is empty.")
                continue

            key = (
                str(submitted_by).strip().lower(),
                str(date_submitted or ""),
                str(title).strip().lower(),
                str(description or "").strip().lower(),
            )

            # Prefer the original Excel No. when available; otherwise use the
            # content key. This makes re-uploading the same 173-row workbook safe.
            if (legacy_no is not None and legacy_no in existing_legacy_nos) or key in existing_keys:
                skipped += 1
                continue

            approval = pd.to_datetime(r.get("Decision Date"), errors="coerce")
            implemented = pd.to_datetime(r.get("Date Implemented"), errors="coerce")
            tangible = pd.to_numeric(r.get("Tangible Value (LKR/yr)"), errors="coerce")
            reward_value = pd.to_numeric(r.get("Reward Value (LKR)"), errors="coerce")

            row = {
                "legacy_no": legacy_no,
                "submitted_by": str(submitted_by),
                "employee_type": employee_type,
                "department": str(department),
                "date_submitted": date_submitted,
                "title": str(title),
                "description": description,
                "category": _clean_excel_value(r.get("Categories (PQCDSM)")),
                "technique_used": _clean_excel_value(r.get("Technique Used")),
                "status": _normalize_status(r.get("Status")),
                "tangible_value": float(tangible) if pd.notna(tangible) else 0.0,
                "reward": _clean_excel_value(r.get("Reward Given")),
                "reward_value": float(reward_value) if pd.notna(reward_value) else 0.0,
                "approver": _clean_excel_value(r.get("Decided By")),
                "approval_date": approval.to_pydatetime() if pd.notna(approval) else None,
                "date_implemented": implemented.date() if pd.notna(implemented) else None,
                "ai_note": "Imported from existing Kaizen Excel workbook",
                "created_at": datetime.combine(date_submitted, datetime.min.time()) if date_submitted else datetime.now(),
            }

            insert_suggestion(row)
            existing_keys.add(key)
            if legacy_no is not None:
                existing_legacy_nos.add(legacy_no)
            imported += 1
        except Exception as exc:
            errors.append(f"Excel row {excel_row}: {exc}")

    return imported, skipped, errors

 
def insert_suggestion(row: dict) -> int:
    engine = get_engine()
    is_postgres = engine.dialect.name == "postgresql"
 
    sql = """INSERT INTO suggestions
             (legacy_no, submitted_by, employee_type, department, date_submitted, title, description,
              category, technique_used, status, tangible_value, reward, reward_value,
              approver, approval_date, date_implemented, ai_note, created_at)
             VALUES (:legacy_no,:submitted_by,:employee_type,:department,:date_submitted,:title,:description,
                     :category,:technique_used,:status,:tangible_value,:reward,:reward_value,
                     :approver,:approval_date,:date_implemented,:ai_note,:created_at)"""
    if is_postgres:
        sql += " RETURNING id"
 
    with engine.begin() as conn:
        result = conn.execute(text(sql), row)
        if is_postgres:
            return result.scalar_one()
        return result.lastrowid
 
 
def update_suggestion_decision(sug_id, status, approver, tangible_value, reward, technique_used):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""UPDATE suggestions
                    SET status=:status, approver=:approver, approval_date=:approval_date,
                        tangible_value=:tangible_value, reward=:reward, technique_used=:technique_used
                    WHERE id=:id"""),
            {
                "status": status, "approver": approver,
                "approval_date": datetime.now(), "tangible_value": tangible_value,
                "reward": reward, "technique_used": technique_used, "id": sug_id,
            },
        )
 
 
# --------------------------------------------------------------------------- #
# Approvers
# --------------------------------------------------------------------------- #
def df_approvers() -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(text("SELECT username, display_name, active, password_hash FROM approvers"), engine)
 
 
def get_approver(username: str):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM approvers WHERE username=:u"), {"u": username}
        ).mappings().first()
    return dict(row) if row else None
 
 
def verify_approver(username: str, password: str):
    row = get_approver(username)
    if row and row["active"] and row["password_hash"] and row["password_hash"] == hash_pw(password):
        return row
    return None
 
 
def set_password(username: str, new_password: str):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE approvers SET password_hash=:p WHERE username=:u"),
            {"p": hash_pw(new_password), "u": username},
        )
 
 
def add_approver(username: str, display_name: str) -> bool:
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO approvers (username, display_name, password_hash, active)
                        VALUES (:u, :d, NULL, :active)"""),
                {"u": username.strip().lower(), "d": display_name.strip(), "active": True},
            )
        return True
    except sa.exc.IntegrityError:
        return False
 
 
def set_approver_active(username: str, active: bool):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE approvers SET active=:a WHERE username=:u"),
            {"a": bool(active), "u": username},
        )
 
 
# --------------------------------------------------------------------------- #
# Optional: Groq AI assist
# --------------------------------------------------------------------------- #
def ai_review(description: str):
    """Returns (suggested_category, short_note) or (None, None) if Groq isn't configured."""
    api_key = st.secrets.get("GROQ_API_KEY", None)
    if not api_key or not description.strip():
        return None, None
    try:
        from groq import Groq
 
        client = Groq(api_key=api_key)
        # NOTE: check https://console.groq.com/docs/models for the current supported
        # model id in your account before deploying — model names change over time.
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You triage Kaizen continuous-improvement suggestions for a hydropower "
                        "operations team. Given the idea description, reply with exactly two lines:\n"
                        f"Category: <one of {', '.join(CATEGORIES)}>\n"
                        "Note: <one short sentence on clarity/impact, max 20 words>"
                    ),
                },
                {"role": "user", "content": description},
            ],
            max_tokens=100,
            temperature=0.2,
        )
        text_out = resp.choices[0].message.content.strip()
        category, note = None, None
        for line in text_out.splitlines():
            if line.lower().startswith("category:"):
                category = line.split(":", 1)[1].strip()
            elif line.lower().startswith("note:"):
                note = line.split(":", 1)[1].strip()
        return category, note
    except Exception as e:  # pragma: no cover
        st.session_state["_ai_error"] = str(e)
        return None, None
 
 
# --------------------------------------------------------------------------- #
# QR code helper
# --------------------------------------------------------------------------- #
def qr_png_bytes(url: str) -> bytes:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
 
 
# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
def init_session():
    defaults = {
        "role": None,                  # None, staff, or approver
        "staff_name": None,
        "staff_department": None,
        "staff_employee_type": None,
        "approver_username": None,
        "approver_display_name": None,
        "pending_first_time_username": None,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)
 
 
def logout():
    for k in [
        "role", "staff_name", "staff_department", "staff_employee_type",
        "approver_username", "approver_display_name", "pending_first_time_username",
    ]:
        st.session_state.pop(k, None)
    init_session()
 
 
def header(badge: str, who: str):
    st.markdown(
        f"""
        <div class="hero">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap;">
                <div>
                    <div class="hero-kicker">VIDULLANKA PLC · OPERATION EXCELLENCE</div>
                    <div class="hero-title">V-ZEN</div>
                    <div class="hero-title">Kaizen Suggestions</div>
                    <div class="hero-subtitle">Capture, review and turn continuous-improvement ideas into action.</div>
                </div>
                <div style="text-align:right;">
                    <span class="hero-badge">{badge}</span>
                    <div class="hero-subtitle" style="margin-top:8px;">{who}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True,
    )
 
 
def qr_expander():
    with st.expander("📱 Get a QR code to log a suggestion from your phone", expanded=False):
        default_url = st.secrets.get("APP_URL", "")
        app_url = st.text_input(
            "App URL (paste your deployed Streamlit URL once, then it's remembered for this session)",
            value=default_url,
            placeholder="https://your-app.streamlit.app",
            key="app_url_input",
        )
        if app_url:
            st.image(qr_png_bytes(app_url), caption="Scan to log a suggestion", width=220)
        else:
            st.info("Enter your app's public URL above (or set APP_URL in secrets) to generate the QR code.")
 
 
# --------------------------------------------------------------------------- #
# Public (anonymous) pages — Staff / GEMBA
# --------------------------------------------------------------------------- #
def staff_access():
    """Collect GEMBA/staff identity and the HOF/plant site."""
    st.subheader("👤 GEMBA / Staff Access")
    st.caption(
        "Enter the name of the person who suggested the idea and select the plant/site. "
        "You can also add a new plant if it is not in the list."
    )

    plant_options = DEPARTMENTS + ["➕ Add New Plant"]
    current_site = st.session_state.get("staff_department")
    default_index = plant_options.index(current_site) if current_site in plant_options else 0

    with st.form("staff_access_form", clear_on_submit=False):
        name = st.text_input(
            "Suggested by *",
            placeholder="Enter the name of the person who suggested the idea",
            value=st.session_state.get("staff_name") or "",
        )
        selected_site = st.selectbox(
            "HOF / Plant Site *",
            plant_options,
            index=default_index,
        )

        new_plant = ""
        if selected_site == "➕ Add New Plant":
            new_plant = st.text_input(
                "New Plant / Site Name *",
                placeholder="e.g. ABC Hydro Power Plant",
                help="Type the plant/site name exactly as you want it to appear in the system.",
            )

        employee_type = st.radio(
            "Employee type *",
            ["GEMBA Worker", "Staff"],
            horizontal=True,
            index=(1 if st.session_state.get("staff_employee_type") == "Staff" else 0),
        )
        continue_clicked = st.form_submit_button(
            "➡️ Continue to Suggestion Form",
            type="primary",
            use_container_width=True,
        )

    if continue_clicked:
        name = name.strip()
        department = (new_plant if selected_site == "➕ Add New Plant" else selected_site).strip()

        if not name:
            st.error("Please enter the name of the person who suggested the idea.")
            return False
        if not department:
            st.error("Please enter a plant/site name.")
            return False
        if len(department) > 100:
            st.error("Plant/site name must be 100 characters or fewer.")
            return False

        with st.spinner("Processing GEMBA access..."):
            st.session_state["role"] = "staff"
            st.session_state["staff_name"] = name
            st.session_state["staff_department"] = department
            st.session_state["staff_employee_type"] = employee_type
            st.session_state["staff_access_granted"] = True
        st.rerun()

    return False


def page_site_implemented():
    """Show implemented Kaizen suggestions for the currently selected plant/site only."""
    site = (st.session_state.get("staff_department") or "").strip()
    st.subheader(f"🟢 Implemented Suggestions — {site}")
    st.caption(
        "See Kaizen suggestions that have been implemented at your plant/site. "
        "This view is limited to your selected site."
    )

    if not site:
        st.warning("Please select your plant/site first.")
        return

    df = df_suggestions()
    if df.empty:
        st.info("No implemented suggestions are available yet.")
        return

    site_series = df["department"].fillna("").astype(str).str.strip()
    view = df[
        (df["status"].fillna("").astype(str).str.strip().str.lower() == "implemented")
        & (site_series.str.casefold() == site.casefold())
    ].copy()

    if view.empty:
        st.info(f"No implemented suggestions have been recorded for **{site}** yet.")
        return

    st.metric("Implemented suggestions at this site", len(view))
    st.divider()

    for _, r in view.sort_values(
        ["date_implemented", "date_submitted"], ascending=False, na_position="last"
    ).iterrows():
        title = r["title"] or "Untitled suggestion"
        with st.expander(f"💡 {title}"):
            st.markdown("**Description**")
            st.write(r["description"] or "No description provided.")
            c1, c2, c3 = st.columns(3)
            c1.caption(f"👤 Suggested by: {r['submitted_by'] or 'Not specified'}")
            c2.caption(f"📅 Submitted: {r['date_submitted'] or '—'}")
            c3.caption(f"✅ Implemented: {r['date_implemented'] or '—'}")

            c4, c5, c6 = st.columns(3)
            c4.caption(f"🏷️ Category: {r['category'] or '—'}")
            c5.caption(f"🛠️ Technique: {r['technique_used'] or '—'}")
            c6.caption(f"💰 Tangible value: Rs {float(r['tangible_value'] or 0):,.0f}")

            if r["reward"]:
                st.info(f"🏆 Recognition / Reward: {r['reward']}")

 
 
def page_log_suggestion():
    """Suggestion form. Staff identity is taken from the authenticated session."""
    st.subheader("💡 New Kaizen Suggestion")
 
    staff_name = st.session_state.get("staff_name")
    staff_department = st.session_state.get("staff_department")
    staff_employee_type = st.session_state.get("staff_employee_type")
 
    if not staff_name or not staff_department or not staff_employee_type:
        st.warning("Please complete GEMBA / Staff Access before logging a suggestion.")
        if st.button("Go to GEMBA / Staff Access", type="primary"):
            st.session_state["role"] = None
            st.rerun()
        return
 
    c1, c2, c3 = st.columns(3)
    c1.metric("Suggested by", staff_name)
    c2.metric("Department", staff_department)
    c3.metric("Employee Type", staff_employee_type)
    st.caption("The suggestion can be entered by any person on behalf of the person named above.")
 
    with st.form("log_suggestion_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            title = st.text_input(
                "Suggestion title *",
                placeholder="Short summary of the idea",
            )
            date_submitted = st.date_input(
                "Date submitted",
                value=date.today(),
            )
        with c2:
            technique_used = st.selectbox(
                "Technique used (optional)",
                TECHNIQUES,
            )
            category = st.multiselect("Category (PQCDSM)", CATEGORIES)
 
        description = st.text_area(
            "Description *",
            placeholder="What is the idea, what problem does it solve, and what improvement is expected?",
            height=140,
        )
 
        submitted = st.form_submit_button(
            "🚀 Submit Suggestion",
            type="primary",
            use_container_width=True,
        )
 
    if submitted:
        if not title.strip():
            st.error("Please enter a suggestion title.")
            return
        if not description.strip():
            st.error("Please enter a description.")
            return
 
        with st.spinner("Processing and saving your suggestion..."):
            ai_category, ai_note = ai_review(description)
            final_category = category if category else ([ai_category] if ai_category else [])
 
            row = {
                "submitted_by": staff_name,
                "employee_type": staff_employee_type,
                "department": staff_department,
                "date_submitted": date_submitted,
                "title": title.strip(),
                "description": description.strip(),
                "category": ", ".join(final_category),
                "technique_used": technique_used,
                "status": "Pending",
                "tangible_value": 0,
                "reward": None,
                "reward_value": 0,
                "approver": None,
                "approval_date": None,
                "date_implemented": None,
                "ai_note": ai_note,
                "created_at": datetime.now(),
            }
 
            try:
                new_id = insert_suggestion(row)
            except Exception as e:
                st.error(f"Could not save the suggestion: {e}")
                return
 
        st.success(f"Suggestion #{new_id} submitted successfully. Thank you!")
        st.info(
            "Your suggestion has been saved and is visible only to authorized approvers. "
            f"Your reference ID is **#{new_id}**."
        )
        if ai_note:
            st.info(f"AI note: {ai_note}")
 
 
def page_track_suggestions():
    st.subheader("Suggestion Tracking")
    st.info(
        "Individual suggestions are not displayed to Staff/GEMBA users. "
        "Authorized approvers manage the suggestion log."
    )
 
 
# --------------------------------------------------------------------------- #
# Approver — sign in (with self-service first-time password setup)
# --------------------------------------------------------------------------- #
def approver_signin():
    st.subheader("🔐 Approver Sign In")
    st.caption(
        "Enter your approved username. On your first sign-in, you will create "
        "your own preferred password."
    )
 
    username = st.text_input(
        "Approver username *",
        placeholder="e.g. roshan",
        key="approver_login_username",
    ).strip().lower()
 
    row = get_approver(username) if username else None
    first_time = bool(row and row["active"] and not row["password_hash"])
 
    if first_time:
        st.info("🆕 First sign-in detected. You choose your own password next — no password is given to you.")
        if st.button("➡️ Continue to create my preferred password", type="primary", use_container_width=True):
            st.session_state["pending_first_time_username"] = username
            st.rerun()
        return
 
    with st.form("approver_signin_form"):
        password = st.text_input(
            "Your preferred password",
            type="password",
            placeholder="Enter the password you created",
        )
        sign_in_clicked = st.form_submit_button(
            "🔐 Sign in",
            type="primary",
            use_container_width=True,
        )
 
    if not sign_in_clicked:
        return
 
    if not username:
        st.error("Please enter your approver username.")
        return
 
    with st.spinner("Processing sign-in..."):
        approver_row = get_approver(username)
        if not approver_row or not approver_row["active"]:
            st.error("Invalid or inactive approver account.")
            return
 
        if not approver_row["password_hash"]:
            st.session_state["pending_first_time_username"] = username
            st.rerun()
 
        if not password:
            st.error("Please enter the password you previously created.")
            return
 
        user = verify_approver(username, password)
 
    if user:
        st.session_state["role"] = "approver"
        st.session_state["approver_username"] = user["username"]
        st.session_state["approver_display_name"] = user["display_name"]
        st.rerun()
    else:
        st.error("Incorrect username or password.")
 
 
def first_time_password_setup():
    username = st.session_state.get("pending_first_time_username")
    if not username:
        return False
 
    row = get_approver(username)
    if not row or not row["active"] or row["password_hash"]:
        st.session_state.pop("pending_first_time_username", None)
        return False
 
    st.subheader("🔑 Create Your Preferred Password")
    st.caption(f"Account: {row['display_name']} ({username})")
    st.success("No password was assigned to you. Create the password you want to use for future sign-ins.")
 
    with st.form("first_time_setup_form"):
        pw1 = st.text_input("Create password", type="password")
        pw2 = st.text_input("Confirm password", type="password")
        create_clicked = st.form_submit_button(
            "Create password & sign in",
            type="primary",
            use_container_width=True,
        )
 
    if create_clicked:
        if not pw1 or pw1 != pw2:
            st.error("Passwords must match and cannot be empty.")
            return True
        if len(pw1) < 6:
            st.error("Please use at least 6 characters.")
            return True
 
        with st.spinner("Processing password setup..."):
            set_password(username, pw1)
 
        st.session_state.pop("pending_first_time_username", None)
        st.session_state["role"] = "approver"
        st.session_state["approver_username"] = username
        st.session_state["approver_display_name"] = row["display_name"]
        st.rerun()
 
    return True
 
 
# --------------------------------------------------------------------------- #
# Approver — Dashboard
# --------------------------------------------------------------------------- #
def kpi_card(label, value, col):
    with col:
        st.markdown(
            f"""<div style="background:#fff;border:1px solid #eee;border-radius:10px;
                    padding:16px 18px;">
                <div style="font-size:26px;font-weight:700;">{value}</div>
                <div style="font-size:12px;color:#777;letter-spacing:1px;">{label.upper()}</div>
                </div>""",
            unsafe_allow_html=True,
        )
 
 
def page_dashboard():
    st.subheader("📊 Approver Dashboard")
    st.caption("Management view of suggestions with plant/site-level performance.")

    df = df_suggestions()
    if df.empty:
        st.info("No suggestions logged yet. New Staff/GEMBA submissions will appear here automatically.")
        return

    # Include predefined plants plus any new plant/site entered by GEMBA users.
    database_sites = [str(x).strip() for x in df["department"].dropna().tolist() if str(x).strip()]
    department_options = list(dict.fromkeys(DEPARTMENTS + database_sites))

    # Dashboard-only plant selector.
    selected_site = st.selectbox(
        "🏭 Select Plant / Site",
        ["All Plants / Sites"] + department_options,
        key="dashboard_site_filter",
    )

    if selected_site == "All Plants / Sites":
        site_df = df.copy()
    else:
        site_df = df[df["department"].fillna("Not specified").astype(str).str.strip() == selected_site].copy()

    # KPIs for selected plant/site.
    total = len(site_df)
    pending = int((site_df["status"] == "Pending").sum())
    approved = int((site_df["status"] == "Approved").sum())
    rejected = int((site_df["status"] == "Rejected").sum())
    implemented = int((site_df["status"] == "Implemented").sum())
    approved_total = approved + implemented
    impl_rate = round(100 * implemented / total, 0) if total else 0
    tangible_total = site_df["tangible_value"].fillna(0).astype(float).sum()

    st.markdown(f"### 📍 {selected_site}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total suggestions", total)
    c2.metric("Pending review", pending)
    c3.metric("Approved", approved)
    c4.metric("Rejected", rejected)

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Implemented", implemented)
    c6.metric("Approved + Implemented", approved_total)
    c7.metric("Implementation rate", f"{impl_rate:.0f}%")
    c8.metric("Tangible value", f"Rs {tangible_total:,.0f}")

    st.divider()

    # Site-by-site summary is visible when All Plants / Sites is selected.
    summary_rows = []
    for site in department_options:
        sdf = df[df["department"].fillna("Not specified").astype(str).str.strip() == site]
        if sdf.empty:
            continue
        approved_site = int((sdf["status"] == "Approved").sum())
        implemented_site = int((sdf["status"] == "Implemented").sum())
        rejected_site = int((sdf["status"] == "Rejected").sum())
        pending_site = int((sdf["status"] == "Pending").sum())
        summary_rows.append({
            "Plant / Site": site,
            "Total Suggestions": len(sdf),
            "Pending": pending_site,
            "Approved": approved_site,
            "Implemented": implemented_site,
            "Approved + Implemented": approved_site + implemented_site,
            "Rejected": rejected_site,
        })

    if selected_site == "All Plants / Sites":
        st.markdown("### 🏭 Site-wise Suggestion Summary")
        st.caption("Choose a plant above to focus the dashboard on that site.")
        if summary_rows:
            summary_df = pd.DataFrame(summary_rows).sort_values("Total Suggestions", ascending=False)
            st.dataframe(summary_df, use_container_width=True, hide_index=True)
        else:
            st.info("No plant/site information is available yet.")

    left, right = st.columns(2)
    with left:
        st.markdown("**Suggestion status**")
        status_df = site_df["status"].value_counts().reindex(STATUSES, fill_value=0).reset_index()
        status_df.columns = ["Status", "Count"]
        fig = px.bar(status_df, x="Count", y="Status", orientation="h", text="Count")
        fig.update_layout(showlegend=False, height=320, margin=dict(l=10,r=10,t=10,b=10), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        if selected_site == "All Plants / Sites" and summary_rows:
            st.markdown("**Plant-wise status comparison**")
            chart_df = pd.DataFrame(summary_rows).set_index("Plant / Site")[["Pending", "Approved", "Implemented", "Rejected"]]
            fig2 = px.bar(chart_df, barmode="group", text_auto=True)
            fig2.update_layout(height=320, margin=dict(l=10,r=10,t=10,b=10), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", legend_title_text="Status")
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.markdown(f"**{selected_site} — Employee Type**")
            type_df = site_df["employee_type"].fillna("Unknown").value_counts().reset_index()
            type_df.columns = ["Employee Type", "Count"]
            fig2 = px.bar(type_df, x="Count", y="Employee Type", orientation="h", text="Count")
            fig2.update_layout(showlegend=False, height=320, margin=dict(l=10,r=10,t=10,b=10), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.markdown("**Recent activity**")
    if site_df.empty:
        st.info("No suggestions for this plant/site.")
    else:
        recent = site_df[["id","date_submitted","submitted_by","department","title","status","approver","approval_date"]].head(10).copy()
        recent.columns = ["ID","Submitted","Submitted By","Department","Suggestion","Status","Decided By","Decision Date"]
        st.dataframe(recent, use_container_width=True, hide_index=True)


def page_approvals():
    st.subheader("Approvals")
    df = df_suggestions()
    pending = df[df["status"] == "Pending"]
 
    if pending.empty:
        st.success("No suggestions waiting for review. 🎉")
        return
 
    for _, r in pending.iterrows():
        with st.expander(f"#{r['id']} — {r['title']}  ·  {r['submitted_by']} ({r['employee_type']}, {r['department']})"):
            st.write(r["description"])
            meta_cols = st.columns(3)
            meta_cols[0].caption(f"Category: {r['category'] or '—'}")
            meta_cols[1].caption(f"Submitted: {r['date_submitted']}")
            meta_cols[2].caption(f"Technique: {r['technique_used'] or '—'}")
            if r["ai_note"]:
                st.caption(f"🤖 AI note: {r['ai_note']}")
 
            with st.form(f"decision_form_{r['id']}"):
                technique = st.selectbox("Technique used", TECHNIQUES,
                                          index=TECHNIQUES.index(r["technique_used"])
                                          if r["technique_used"] in TECHNIQUES else 0,
                                          key=f"tech_{r['id']}")
                mark_implemented = st.checkbox("Mark as already implemented", key=f"impl_{r['id']}")
                tangible_value = st.number_input("Tangible value (LKR)", min_value=0.0, step=1000.0,
                                                  key=f"val_{r['id']}")
                reward = st.text_input("Reward / recognition (optional)", key=f"reward_{r['id']}")
 
                b1, b2 = st.columns(2)
                approve_clicked = b1.form_submit_button("✅ Approve", type="primary")
                reject_clicked = b2.form_submit_button("❌ Reject")
 
            if approve_clicked:
                status = "Implemented" if mark_implemented else "Approved"
                update_suggestion_decision(
                    r["id"], status, st.session_state["approver_display_name"],
                    tangible_value, reward or None, technique,
                )
                st.success(f"Suggestion #{r['id']} marked {status}.")
                st.rerun()
 
            if reject_clicked:
                update_suggestion_decision(
                    r["id"], "Rejected", st.session_state["approver_display_name"],
                    0, None, technique,
                )
                st.warning(f"Suggestion #{r['id']} rejected.")
                st.rerun()
 
 
def page_all_suggestions():
    st.subheader("All Suggestions")
    df = df_suggestions()
    if df.empty:
        st.info("No suggestions logged yet.")
        return

    f1, f2, f3, f4 = st.columns(4)
    status_filter = f1.multiselect("Status", STATUSES, default=STATUSES, key="all_status_filter")

    # Include both the predefined plants and any plants added by GEMBA users.
    database_sites = [
        str(x).strip()
        for x in df["department"].dropna().tolist()
        if str(x).strip()
    ]
    department_options = list(dict.fromkeys(DEPARTMENTS + database_sites))
    dept_filter = f2.multiselect("HOF / Plant Site", department_options, key="all_department_filter")

    type_filter = f3.multiselect("Employee type", ["GEMBA Worker", "Staff"], key="all_type_filter")

    current_year = date.today().year
    available_years = list(range(current_year, 1999, -1))
    year_filter = f4.multiselect("Year", available_years, default=[current_year], key="all_year_filter")

    view = df[df["status"].isin(status_filter)]
    if dept_filter:
        view = view[view["department"].fillna("Not specified").isin(dept_filter)]
    if type_filter:
        view = view[view["employee_type"].isin(type_filter)]
    if year_filter:
        view = view[pd.to_datetime(view["date_submitted"], errors="coerce").dt.year.isin(year_filter)]

    st.caption("Use HOF / Plant Site and Year filters to find suggestions. Years from 2000 to the current year are available.")
    st.caption(f"Showing **{len(view):,}** of **{len(df):,}** suggestions.")

    show = view[["id", "legacy_no", "date_submitted", "submitted_by", "employee_type", "department", "title",
                 "category", "technique_used", "status", "tangible_value", "reward", "reward_value",
                 "approver", "approval_date", "date_implemented"]].rename(columns={
        "id": "Database ID", "legacy_no": "Excel No.", "date_submitted": "Submitted Date", "submitted_by": "Submitted By",
        "employee_type": "Employee Type", "department": "HOF / Plant Site", "title": "Suggestion",
        "category": "Category", "technique_used": "Technique", "status": "Status",
        "tangible_value": "Tangible Value (LKR)", "reward": "Reward / Recognition",
        "reward_value": "Reward Value (LKR)", "approver": "Decided By",
        "approval_date": "Decision Date", "date_implemented": "Date Implemented",
    })
    st.dataframe(show, use_container_width=True, hide_index=True)

    csv = show.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Export filtered suggestions as CSV", csv, "kaizen_suggestions_filtered.csv", "text/csv")

    st.divider()
    st.markdown("**Decision log — approved / rejected / implemented, by whom and when**")
    decided = df[df["status"].isin(["Approved", "Rejected", "Implemented"])]
    if decided.empty:
        st.caption("No decisions recorded yet.")
    else:
        decided_show = decided[["id", "title", "status", "approver", "approval_date", "tangible_value", "reward"]].rename(columns={
            "id": "ID", "title": "Title", "status": "Status", "approver": "Decided By",
            "approval_date": "Decision Date", "tangible_value": "Tangible Value (LKR)", "reward": "Reward",
        }).sort_values("Decision Date", ascending=False)
        st.dataframe(decided_show, use_container_width=True, hide_index=True)



def page_import_excel():
    st.subheader("📥 Import Existing Kaizen Excel")
    st.caption("Upload the historical Kaizen workbook. Valid rows are copied into the connected PostgreSQL database and become available to the Approver Dashboard. Duplicate rows are skipped.")

    uploaded = st.file_uploader(
        "Upload the existing Kaizen Suggestions Excel file", type=["xlsx"],
        key="legacy_kaizen_excel",
        help="Use the Vidullanka Kaizen workbook (.xlsx) containing the 'Kaizen Suggestions' sheet.",
    )
    if uploaded is None:
        st.info("Upload the workbook containing the **Kaizen Suggestions** sheet.")
        return

    try:
        preview = _read_uploaded_excel(uploaded)
    except Exception as exc:
        st.error(f"Could not read the Excel file: {exc}")
        st.info("Make sure **openpyxl>=3.1** is in requirements.txt and redeploy the app.")
        return

    required = ["Name", "Role", "Date Submitted", "Title"]
    missing = [c for c in required if c not in preview.columns]
    if missing:
        st.error("This workbook is missing required columns: " + ", ".join(missing))
        return

    st.success(f"Excel file read successfully — {len(preview):,} individual suggestion rows found.")
    st.caption("The import will process every suggestion row in the **Kaizen Suggestions** sheet, not only the rows shown in this preview. All suggestion fields are copied to PostgreSQL.")
    st.dataframe(preview.head(10), use_container_width=True, hide_index=True)

    missing_dept_count = int(preview["Department"].isna().sum()) if "Department" in preview.columns else len(preview)
    if missing_dept_count:
        st.warning(f"{missing_dept_count:,} historical rows do not contain a Department/HOF/Plant Site. They will be imported as **Not specified**. This preserves the original Excel data instead of guessing a site.")

    st.info("The workbook's **Summary** sheet is not imported as suggestion rows because the dashboard calculates its own totals from the individual records. The original Excel **No** is preserved in the database as **Excel No.**.")

    if st.button("🚀 Import Excel into Database", type="primary", use_container_width=True):
        with st.spinner("Importing historical suggestions into the connected database..."):
            try:
                imported, skipped, errors = import_legacy_excel(uploaded)
                st.success(f"Import completed: **{imported:,}** new suggestions added, **{skipped:,}** duplicate/invalid rows skipped.")
                if errors:
                    st.warning("Some rows could not be imported:")
                    st.code("\n".join(errors[:30]))
                else:
                    st.info("All valid Excel records were imported successfully.")
                st.rerun()
            except Exception as exc:
                st.error(f"Import failed: {exc}")


def page_manage_approvers():
    st.subheader("Manage Approvers")
 
    st.markdown("**Current approvers**")
    approvers = df_approvers()
    for _, a in approvers.iterrows():
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.write(a["display_name"])
        c2.write("🟢 Active" if a["active"] else "⚪ Inactive")
        c3.write("🔑 Password set" if a["password_hash"] else "🆕 Awaiting first sign-in")
        toggle_label = "Deactivate" if a["active"] else "Reactivate"
        if c4.button(toggle_label, key=f"toggle_{a['username']}"):
            set_approver_active(a["username"], not a["active"])
            st.rerun()
 
    st.divider()
    st.markdown("**Add a new approver**")
    st.caption("No password is set here — the new approver creates their own password the first time they sign in.")
    with st.form("add_approver_form", clear_on_submit=True):
        name = st.text_input("Display name")
        username = st.text_input("Username (lowercase, no spaces)")
        add_clicked = st.form_submit_button("Add approver", type="primary")
    if add_clicked:
        if not name.strip() or not username.strip():
            st.error("Both fields are required.")
        elif add_approver(username, name):
            st.success(f"Added approver {name}. They can now sign in and create their own password.")
            st.rerun()
        else:
            st.error("That username already exists.")
 
    st.divider()
    st.markdown("**Change your own password**")
    with st.form("change_pw_form", clear_on_submit=True):
        current_pw = st.text_input("Current password", type="password")
        new_pw1 = st.text_input("New password", type="password")
        new_pw2 = st.text_input("Confirm new password", type="password")
        change_clicked = st.form_submit_button("Update password")
    if change_clicked:
        me = verify_approver(st.session_state["approver_username"], current_pw)
        if not me:
            st.error("Current password is incorrect.")
        elif not new_pw1 or new_pw1 != new_pw2:
            st.error("New passwords must match and can't be empty.")
        elif len(new_pw1) < 6:
            st.error("Please use at least 6 characters.")
        else:
            set_password(st.session_state["approver_username"], new_pw1)
            st.success("Password updated.")
 
 
# --------------------------------------------------------------------------- #
# Shells
# --------------------------------------------------------------------------- #
def public_shell():
    # This screen is the entry point. It deliberately exposes no suggestion data.
    if st.session_state.get("pending_first_time_username"):
        header("APPROVER", "First-time password setup")
        first_time_password_setup()
        return
 
    if st.session_state.get("role") == "staff":
        staff_shell()
        return
 
    header("KAIZEN", "GEMBA worker suggestion entry")
    qr_expander()
 
    with st.sidebar:
        st.markdown("### Access")
        page = st.radio(
            "Choose access",
            ["GEMBA / Staff Access", "Approver Sign In"],
            label_visibility="collapsed",
        )
        st.divider()
        st.caption(
            "Your suggestions are collected here. Share ideas from HOF or any plant site "
            "to support continuous improvement."
        )
 
    if page == "GEMBA / Staff Access":
        staff_access()
    else:
        approver_signin()
 
 
def staff_shell():
    name = st.session_state.get("staff_name", "Staff / GEMBA")
    department = st.session_state.get("staff_department", "")
    employee_type = st.session_state.get("staff_employee_type", "")

    header("GEMBA WORKER", f"{name} · {department}")

    with st.sidebar:
        st.markdown(f"### 👷 {name}")
        st.caption(f"{employee_type} · {department}")
        if st.button("Change staff / Log out", use_container_width=True):
            logout()
            st.rerun()
        st.divider()
        st.markdown("### GEMBA Workspace")
        page = st.radio(
            "Navigation",
            ["💡 New Suggestion", "🟢 Implemented at My Site"],
            label_visibility="collapsed",
        )
        st.divider()
        st.info(
            "You can submit Kaizen ideas and view implemented improvements "
            "recorded for your selected plant/site."
        )

    if page == "💡 New Suggestion":
        page_log_suggestion()
    else:
        page_site_implemented()

 
 
def approver_shell():
    who = st.session_state["approver_display_name"]
    df = df_suggestions()
    counts = {status: int((df["status"] == status).sum()) if not df.empty else 0 for status in STATUSES}
 
    header("APPROVER", f"Signed in as {who}")
    with st.sidebar:
        st.markdown("### 🔐 Approver Workspace")
        st.caption(f"Signed in as **{who}**")
        if st.button("🚪 Sign out", use_container_width=True):
            logout()
            st.rerun()
        st.divider()
        db_type = "PostgreSQL" if get_engine().dialect.name != "sqlite" else "SQLite (local fallback)"
        st.markdown(f"**Database:** {db_type}")
        st.caption("Suggestions are accessed through this app. All suggestion records are stored in the connected database.")
        st.divider()
        st.markdown("**Suggestion status**")
        st.markdown(f"🟠 Pending  **{counts['Pending']}**")
        st.markdown(f"🟢 Approved  **{counts['Approved']}**")
        st.markdown(f"🔴 Rejected  **{counts['Rejected']}**")
        st.markdown(f"🔵 Implemented  **{counts['Implemented']}**")
        st.divider()
        page = st.radio("Navigation", [
            "📊 Dashboard", "📝 Pending Approvals", "🟢 Approved", "🔴 Rejected",
            "🔵 Implemented", "📋 All Suggestions", "📥 Import Excel", "⚙️ Manage Approvers"
        ], label_visibility="collapsed")
 
    if page == "📊 Dashboard":
        page_dashboard()
    elif page == "📝 Pending Approvals":
        page_approvals()
    elif page == "📋 All Suggestions":
        page_all_suggestions()
    elif page == "📥 Import Excel":
        page_import_excel()
    elif page == "⚙️ Manage Approvers":
        page_manage_approvers()
    else:
        page_status_history(page.split(" ",1)[1])
 
 
def page_status_history(status: str):
    df = df_suggestions()
    st.subheader(f"{status} Suggestions")
    st.caption(f"All suggestions currently marked as **{status}**, including decision dates.")
    view = df[df["status"] == status].copy()
    if view.empty:
        st.info(f"No {status.lower()} suggestions yet.")
        return
    show = view[["id","date_submitted","submitted_by","employee_type","department","title","category","technique_used","approver","approval_date","date_implemented","tangible_value","reward","reward_value"]].rename(columns={
        "id":"ID", "date_submitted":"Submitted Date", "submitted_by":"Submitted By",
        "employee_type":"Employee Type", "department":"Department", "title":"Suggestion",
        "category":"Category", "technique_used":"Technique", "approver":"Decided By",
        "approval_date":"Decision Date", "date_implemented":"Date Implemented",
        "tangible_value":"Tangible Value (LKR)", "reward":"Reward / Recognition",
        "reward_value":"Reward Value (LKR)"
    }).sort_values("Decision Date", ascending=False)
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.download_button(f"⬇️ Export {status} suggestions", show.to_csv(index=False).encode("utf-8"), f"kaizen_{status.lower()}_suggestions.csv", "text/csv")
 
 
# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    inject_styles()
    init_session()
 
    try:
        init_db()
    except Exception as e:
        st.error("Database connection/setup failed.")
        st.code(str(e))
        st.info(
            "For PostgreSQL, add a [postgres] section to .streamlit/secrets.toml. "
            "For local testing, the app can use kaizen.db automatically."
        )
        st.stop()
 
    if st.session_state["role"] == "approver":
        approver_shell()
    else:
        public_shell()
 
 
if __name__ == "__main__":
    main()
