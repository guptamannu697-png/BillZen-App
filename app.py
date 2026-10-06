from flask import Flask, request, jsonify, session, redirect, url_for, render_template, send_from_directory, Response, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import date, datetime, timedelta
from collections import defaultdict, deque
import sqlite3, os, uuid, secrets, time

app = Flask(__name__)

def load_dotenv(path=None, override=False):
    env_path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(env_path):
        return False
    with open(env_path, 'r', encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = [part.strip() for part in raw_line.split('=', 1)]
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and (override or key not in os.environ):
                os.environ[key] = value
    return True

load_dotenv()

def _get_persistent_secret_key():
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.secret_key')
    if os.path.exists(key_file):
        try:
            with open(key_file, 'r', encoding='utf-8') as f:
                k = f.read().strip()
                if k:
                    return k
        except Exception:
            pass
    gen_key = secrets.token_urlsafe(32)
    try:
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(gen_key)
    except Exception:
        pass
    return gen_key

app.secret_key = _get_persistent_secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'),
                  TEMPLATES_AUTO_RELOAD=True)
app.jinja_env.auto_reload = True

DATABASE = os.environ.get('DATABASE_PATH', 'billzen.db')
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 5
_login_attempts = defaultdict(deque)


# ─────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────
def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db

def rows(sql, args=()):
    db = get_db()
    try:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    finally:
        db.close()

def query(sql, args=(), one=False):
    db = get_db()
    try:
        cur = db.execute(sql, args)
        rv = [dict(r) for r in cur.fetchall()]
        return rv[0] if one and rv else (None if one else rv)
    finally:
        db.close()

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        name                 TEXT NOT NULL,
        username             TEXT UNIQUE NOT NULL,
        password             TEXT NOT NULL,
        plain_password       TEXT DEFAULT '',
        role                 TEXT DEFAULT 'staff',
        permissions          TEXT DEFAULT '',
        admin_id             INTEGER DEFAULT NULL,
        plan_status          TEXT DEFAULT 'trial',
        trial_ends_at        TEXT DEFAULT (datetime('now', '+7 days')),
        subscription_ends_at TEXT DEFAULT '',
        is_master_admin      INTEGER DEFAULT 0,
        created_at           TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS stock_adjustments (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        product_id   INTEGER NOT NULL,
        product_name TEXT DEFAULT '',
        qty          REAL DEFAULT 0,
        type         TEXT DEFAULT 'add',
        reason       TEXT DEFAULT '',
        notes        TEXT DEFAULT '',
        created_at   TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS returns (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id        TEXT UNIQUE,
        user_id          INTEGER NOT NULL,
        original_bill_id TEXT DEFAULT '',
        customer_name    TEXT DEFAULT '',
        customer_phone   TEXT DEFAULT '',
        return_date      TEXT DEFAULT '',
        reason           TEXT DEFAULT '',
        refund_mode      TEXT DEFAULT 'Cash',
        total_return     REAL DEFAULT 0,
        created_at       TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS return_items (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id INTEGER NOT NULL,
        product   TEXT DEFAULT '',
        hsn_code  TEXT DEFAULT '',
        qty       REAL DEFAULT 0,
        price     REAL DEFAULT 0,
        amount    REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS shops (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        name        TEXT NOT NULL,
        address     TEXT DEFAULT '',
        phone       TEXT DEFAULT '',
        email       TEXT DEFAULT '',
        gstin       TEXT DEFAULT '',
        pan         TEXT DEFAULT '',
        state       TEXT DEFAULT '',
        state_code  TEXT DEFAULT '',
        logo        TEXT DEFAULT '',
        upi_id      TEXT DEFAULT '',
        bank_name   TEXT DEFAULT '',
        bank_acc    TEXT DEFAULT '',
        bank_ifsc   TEXT DEFAULT '',
        markup_pct  REAL DEFAULT 30,
        gemini_key  TEXT DEFAULT '',
        openai_key  TEXT DEFAULT '',
        anthropic_key TEXT DEFAULT '',
        ai_provider TEXT DEFAULT 'anthropic',
        created_at  TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS units (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name    TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS categories (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name    TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS products (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        name        TEXT NOT NULL,
        hsn_code    TEXT DEFAULT '',
        category    TEXT DEFAULT 'General',
        unit        TEXT DEFAULT 'Pcs',
        gst_pct     REAL DEFAULT 18,
        buy_price   REAL DEFAULT 0,
        sell_price  REAL DEFAULT 0,
        stock       REAL DEFAULT 0,
        low_stock   REAL DEFAULT 5,
        barcode     TEXT DEFAULT '',
        created_at  TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS customers (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        name       TEXT NOT NULL,
        phone      TEXT DEFAULT '',
        email      TEXT DEFAULT '',
        gstin      TEXT DEFAULT '',
        address    TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS suppliers (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        name       TEXT NOT NULL,
        phone      TEXT DEFAULT '',
        email      TEXT DEFAULT '',
        gstin      TEXT DEFAULT '',
        address    TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS sales (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL,
        bill_id        TEXT UNIQUE NOT NULL,
        bill_type      TEXT DEFAULT 'retail',
        customer_name  TEXT NOT NULL,
        customer_phone TEXT DEFAULT '',
        customer_gstin TEXT DEFAULT '',
        subtotal       REAL DEFAULT 0,
        cgst_amount    REAL DEFAULT 0,
        sgst_amount    REAL DEFAULT 0,
        igst_amount    REAL DEFAULT 0,
        discount       REAL DEFAULT 0,
        total_amount   REAL NOT NULL,
        paid_amount    REAL DEFAULT 0,
        payment_status TEXT DEFAULT 'Pending',
        payment_mode   TEXT DEFAULT 'Cash',
        sale_date      TEXT DEFAULT (date('now')),
        notes          TEXT DEFAULT '',
        bill_category  TEXT DEFAULT 'pakka',
        split_payment  TEXT DEFAULT '',
        converted_from TEXT DEFAULT '',
        created_by     TEXT DEFAULT '',
        khata_pen_no   TEXT DEFAULT '',
        cancelled      INTEGER DEFAULT 0,
        created_at     TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS sale_items (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id    INTEGER NOT NULL,
        product    TEXT NOT NULL,
        hsn_code   TEXT DEFAULT '',
        unit       TEXT DEFAULT 'Pcs',
        qty        REAL NOT NULL,
        price      REAL NOT NULL,
        gst_pct    REAL DEFAULT 0,
        cgst_pct   REAL DEFAULT 0,
        sgst_pct   REAL DEFAULT 0,
        igst_pct   REAL DEFAULT 0,
        amount     REAL NOT NULL,
        FOREIGN KEY(sale_id) REFERENCES sales(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS purchases (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL,
        purchase_id    TEXT UNIQUE NOT NULL,
        supplier_name  TEXT DEFAULT '',
        supplier_gstin TEXT DEFAULT '',
        invoice_no     TEXT DEFAULT '',
        irn_no         TEXT DEFAULT '',
        eway_bill_no   TEXT DEFAULT '',
        buyer_gstin    TEXT DEFAULT '',
        subtotal       REAL DEFAULT 0,
        cgst_amount    REAL DEFAULT 0,
        sgst_amount    REAL DEFAULT 0,
        igst_amount    REAL DEFAULT 0,
        total_amount   REAL DEFAULT 0,
        bill_date      TEXT DEFAULT (date('now')),
        notes          TEXT DEFAULT '',
        bill_image     TEXT DEFAULT '',
        created_at     TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS purchase_items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_id INTEGER NOT NULL,
        product     TEXT NOT NULL,
        hsn_code    TEXT DEFAULT '',
        batch_no    TEXT DEFAULT '',
        unit        TEXT DEFAULT 'Pcs',
        qty         REAL NOT NULL,
        buy_price   REAL NOT NULL,
        gst_pct     REAL DEFAULT 0,
        cgst_pct    REAL DEFAULT 0,
        sgst_pct    REAL DEFAULT 0,
        igst_pct    REAL DEFAULT 0,
        taxable_val REAL DEFAULT 0,
        cgst_amt    REAL DEFAULT 0,
        sgst_amt    REAL DEFAULT 0,
        igst_amt    REAL DEFAULT 0,
        amount      REAL NOT NULL,
        FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS customer_payments (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        customer_id INTEGER,
        bill_id     TEXT NOT NULL,
        amount      REAL NOT NULL,
        mode        TEXT DEFAULT 'Cash',
        paid_at     TEXT DEFAULT (datetime('now')),
        note        TEXT DEFAULT '',
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS quotations (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           INTEGER NOT NULL,
        quote_id          TEXT UNIQUE NOT NULL,
        customer_name     TEXT NOT NULL,
        customer_phone    TEXT DEFAULT '',
        subtotal          REAL DEFAULT 0,
        tax_amount        REAL DEFAULT 0,
        discount          REAL DEFAULT 0,
        total_amount      REAL NOT NULL,
        quote_date        TEXT DEFAULT (date('now')),
        valid_until       TEXT DEFAULT '',
        notes             TEXT DEFAULT '',
        status            TEXT DEFAULT 'Pending',
        converted_bill_id TEXT DEFAULT '',
        created_at        TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS quotation_items (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id   INTEGER NOT NULL,
        product    TEXT NOT NULL,
        hsn_code   TEXT DEFAULT '',
        unit       TEXT DEFAULT 'Pcs',
        qty        REAL NOT NULL,
        price      REAL NOT NULL,
        gst_pct    REAL DEFAULT 0,
        amount     REAL NOT NULL,
        FOREIGN KEY(quote_id) REFERENCES quotations(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS delivery_challans (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL,
        challan_id     TEXT UNIQUE NOT NULL,
        consignee_name TEXT NOT NULL,
        address        TEXT DEFAULT '',
        phone          TEXT DEFAULT '',
        challan_date   TEXT DEFAULT (date('now')),
        vehicle_no     TEXT DEFAULT '',
        driver_name    TEXT DEFAULT '',
        notes          TEXT DEFAULT '',
        status         TEXT DEFAULT 'Delivered',
        created_at     TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS delivery_challan_items (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        challan_id INTEGER NOT NULL,
        product    TEXT NOT NULL,
        hsn_code   TEXT DEFAULT '',
        unit       TEXT DEFAULT 'Pcs',
        qty        REAL NOT NULL,
        notes      TEXT DEFAULT '',
        FOREIGN KEY(challan_id) REFERENCES delivery_challans(id) ON DELETE CASCADE
    );
    """)

    db.commit()
    db.close()
    migrate_db()
    print("Database ready!")

def gen_id(prefix='B'):
    return prefix + datetime.now().strftime('%y%m%d') + str(uuid.uuid4())[:4].upper()

# ─────────────────────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"success": False, "error": "Not authenticated"}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

@app.before_request
def protect_csrf():
    if (request.path.startswith('/api/') and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
            and request.path not in ('/api/login', '/api/register', '/api/logout') and 'user_id' in session):
        token = request.headers.get('X-CSRF-Token', '')
        if not token or not secrets.compare_digest(token, session.get('csrf_token', '')):
            return jsonify({'success': False, 'error': 'Invalid CSRF token'}), 403

def get_uid():
    return session.get('user_id')

@app.route('/')
def home():
    if 'user_id' not in session:
        return render_template('billzen_landing.html')
    return redirect(url_for('dashboard'))


@app.route('/landing')
@app.route('/home-page')
def landing_page():
    return render_template('billzen_landing.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('index.html')

@app.after_request
def add_no_cache_headers(response):
    if response.mimetype == 'text/html':
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# ── DB Migration: add gemini_key column if missing ──────────────
def migrate_db():
    db = get_db()
    try:
        db.execute("ALTER TABLE shops ADD COLUMN gemini_key TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass
    # Add columns for staff management
    for col, col_def in [
        ('admin_id', 'INTEGER DEFAULT NULL'),
        ('plain_password', "TEXT DEFAULT ''"),
        ('permissions', "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
            db.commit()
        except Exception:
            pass
    try:
        db.execute("ALTER TABLE shops ADD COLUMN openai_key TEXT DEFAULT ''")
        db.execute("ALTER TABLE shops ADD COLUMN anthropic_key TEXT DEFAULT ''")
        db.execute("ALTER TABLE shops ADD COLUMN ai_provider TEXT DEFAULT 'anthropic'")
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE sales ADD COLUMN bill_category TEXT DEFAULT 'pakka'")
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE sales ADD COLUMN split_payment TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE sales ADD COLUMN converted_from TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE sales ADD COLUMN created_by TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass
    for tbl, col, col_def in [
        ('sales', 'vehicle_no', "TEXT DEFAULT ''"),
        ('sales', 'transporter_id', "TEXT DEFAULT ''"),
        ('sales', 'state_code', "TEXT DEFAULT ''"),
        ('sales', 'state_name', "TEXT DEFAULT ''"),
        ('sales', 'pan_no', "TEXT DEFAULT ''"),
        ('sales', 'khata_pen_no', "TEXT DEFAULT ''"),
        ('sales', 'billing_address', "TEXT DEFAULT ''"),
        ('sales', 'shipping_address', "TEXT DEFAULT ''"),
        ('sales', 'is_interstate', "INTEGER DEFAULT 0"),
        ('customers', 'state_code', "TEXT DEFAULT ''"),
        ('customers', 'state_name', "TEXT DEFAULT ''"),
        ('customers', 'pan_no', "TEXT DEFAULT ''"),
        ('purchases', 'is_interstate', "INTEGER DEFAULT 0"),
        ('purchases', 'state_code', "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_def}")
            db.commit()
        except Exception:
            pass
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS quotations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL,
            quote_id          TEXT UNIQUE NOT NULL,
            customer_name     TEXT NOT NULL,
            customer_phone    TEXT DEFAULT '',
            subtotal          REAL DEFAULT 0,
            tax_amount        REAL DEFAULT 0,
            discount          REAL DEFAULT 0,
            total_amount      REAL NOT NULL,
            quote_date        TEXT DEFAULT (date('now')),
            valid_until       TEXT DEFAULT '',
            notes             TEXT DEFAULT '',
            status            TEXT DEFAULT 'Pending',
            converted_bill_id TEXT DEFAULT '',
            created_at        TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS quotation_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id   INTEGER NOT NULL,
            product    TEXT NOT NULL,
            hsn_code   TEXT DEFAULT '',
            unit       TEXT DEFAULT 'Pcs',
            qty        REAL NOT NULL,
            price      REAL NOT NULL,
            gst_pct    REAL DEFAULT 0,
            amount     REAL NOT NULL,
            FOREIGN KEY(quote_id) REFERENCES quotations(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS delivery_challans (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            challan_id     TEXT UNIQUE NOT NULL,
            consignee_name TEXT NOT NULL,
            address        TEXT DEFAULT '',
            phone          TEXT DEFAULT '',
            challan_date   TEXT DEFAULT (date('now')),
            vehicle_no     TEXT DEFAULT '',
            driver_name    TEXT DEFAULT '',
            notes          TEXT DEFAULT '',
            status         TEXT DEFAULT 'Delivered',
            created_at     TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS delivery_challan_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            challan_id INTEGER NOT NULL,
            product    TEXT NOT NULL,
            hsn_code   TEXT DEFAULT '',
            unit       TEXT DEFAULT 'Pcs',
            qty        REAL NOT NULL,
            notes      TEXT DEFAULT '',
            FOREIGN KEY(challan_id) REFERENCES delivery_challans(id) ON DELETE CASCADE
        );
        """)
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN plan_status TEXT DEFAULT 'trial'")
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN trial_ends_at TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN subscription_ends_at TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN is_master_admin INTEGER DEFAULT 0")
        db.commit()
    except Exception:
        pass
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS tax_rules (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            rate           REAL NOT NULL,
            cgst_rate      REAL NOT NULL,
            sgst_rate      REAL NOT NULL,
            igst_rate      REAL NOT NULL,
            effective_from TEXT DEFAULT '2017-07-01',
            effective_to   TEXT DEFAULT '2099-12-31',
            description    TEXT DEFAULT '',
            is_active      INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sales_returns (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            credit_note_no     TEXT UNIQUE NOT NULL,
            user_id            INTEGER NOT NULL,
            original_sale_id   INTEGER NOT NULL,
            original_bill_id   TEXT NOT NULL,
            original_sale_date TEXT DEFAULT '',
            customer_name      TEXT DEFAULT '',
            customer_phone     TEXT DEFAULT '',
            customer_gstin     TEXT DEFAULT '',
            place_of_supply    TEXT DEFAULT '07',
            is_interstate      INTEGER DEFAULT 0,
            return_date        TEXT NOT NULL,
            reason             TEXT DEFAULT '',
            subtotal           REAL DEFAULT 0,
            cgst_amount        REAL DEFAULT 0,
            sgst_amount        REAL DEFAULT 0,
            igst_amount        REAL DEFAULT 0,
            total_amount       REAL DEFAULT 0,
            refund_mode        TEXT DEFAULT 'Credit Note',
            notes              TEXT DEFAULT '',
            created_at         TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sales_return_items (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id          INTEGER NOT NULL,
            original_item_id   INTEGER DEFAULT 0,
            product            TEXT DEFAULT '',
            hsn_code           TEXT DEFAULT '',
            qty                REAL DEFAULT 0,
            unit               TEXT DEFAULT 'Pcs',
            price              REAL DEFAULT 0,
            taxable_amount     REAL DEFAULT 0,
            gst_pct            REAL DEFAULT 18,
            cgst_amount        REAL DEFAULT 0,
            sgst_amount        REAL DEFAULT 0,
            igst_amount        REAL DEFAULT 0,
            total_amount       REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS purchase_returns (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            debit_note_no        TEXT UNIQUE NOT NULL,
            user_id              INTEGER NOT NULL,
            original_purchase_id INTEGER NOT NULL,
            original_invoice_no  TEXT NOT NULL,
            original_bill_date   TEXT DEFAULT '',
            supplier_name        TEXT DEFAULT '',
            supplier_phone       TEXT DEFAULT '',
            supplier_gstin       TEXT DEFAULT '',
            place_of_supply      TEXT DEFAULT '07',
            is_interstate        INTEGER DEFAULT 0,
            return_date          TEXT NOT NULL,
            reason               TEXT DEFAULT '',
            subtotal             REAL DEFAULT 0,
            cgst_amount          REAL DEFAULT 0,
            sgst_amount          REAL DEFAULT 0,
            igst_amount          REAL DEFAULT 0,
            total_amount         REAL DEFAULT 0,
            refund_mode          TEXT DEFAULT 'Debit Note',
            notes                TEXT DEFAULT '',
            created_at           TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS purchase_return_items (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id          INTEGER NOT NULL,
            original_item_id   INTEGER DEFAULT 0,
            product            TEXT DEFAULT '',
            hsn_code           TEXT DEFAULT '',
            qty                REAL DEFAULT 0,
            unit               TEXT DEFAULT 'Pcs',
            price              REAL DEFAULT 0,
            taxable_amount     REAL DEFAULT 0,
            gst_pct            REAL DEFAULT 18,
            cgst_amount        REAL DEFAULT 0,
            sgst_amount        REAL DEFAULT 0,
            igst_amount        REAL DEFAULT 0,
            total_amount       REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS shop_ledger_mappings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            key_name    TEXT NOT NULL,
            ledger_name TEXT NOT NULL,
            updated_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, key_name)
        );
        """)
        db.commit()
    except Exception:
        pass
    try:
        cur = db.execute("SELECT COUNT(*) FROM tax_rules")
        if cur.fetchone()[0] == 0:
            for tr in [
                (18.0, 9.0, 9.0, 18.0, '2017-07-01', '2099-12-31', 'Standard Rate 18%', 1),
                (5.0, 2.5, 2.5, 5.0, '2017-07-01', '2099-12-31', 'Essential Rate 5%', 1),
                (0.0, 0.0, 0.0, 0.0, '2017-07-01', '2099-12-31', 'Exempt / Nil 0%', 1),
                (12.0, 6.0, 6.0, 12.0, '2017-07-01', '2099-12-31', 'Historical 12%', 0),
                (28.0, 14.0, 14.0, 28.0, '2017-07-01', '2099-12-31', 'Luxury 28%', 0),
            ]:
                db.execute("INSERT INTO tax_rules (rate, cgst_rate, sgst_rate, igst_rate, effective_from, effective_to, description, is_active) VALUES (?,?,?,?,?,?,?,?)", tr)
            db.commit()
    except Exception:
        pass
    master_uname = os.environ.get('BILLZEN_BOOTSTRAP_ADMIN_USERNAME', 'admin@billzen')
    try:
        db.execute("UPDATE users SET is_master_admin = 1, plan_status = 'lifetime' WHERE username = ?", (master_uname,))
        db.commit()
    except Exception:
        pass

    # Wipe out any legacy cleartext passwords stored in users
    try:
        db.execute("UPDATE users SET plain_password = '' WHERE plain_password != ''")
        db.commit()
    except Exception:
        pass

    # Add Desktop Connector columns to shops
    for col, col_def in [
        ('tally_connector_token', "TEXT DEFAULT ''"),
        ('connector_machine', "TEXT DEFAULT ''"),
        ('connector_last_seen', "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f"ALTER TABLE shops ADD COLUMN {col} {col_def}")
            db.commit()
        except Exception:
            pass

    # Add high-performance secondary database indexes for tenant-scoped & relational queries
    try:
        db.executescript("""
        CREATE INDEX IF NOT EXISTS idx_sales_uid_date ON sales(user_id, sale_date);
        CREATE INDEX IF NOT EXISTS idx_sales_uid_bill ON sales(user_id, bill_id);
        CREATE INDEX IF NOT EXISTS idx_sales_uid_cancelled ON sales(user_id, cancelled);
        CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
        CREATE INDEX IF NOT EXISTS idx_purchases_uid_date ON purchases(user_id, bill_date);
        CREATE INDEX IF NOT EXISTS idx_purchases_uid_pid ON purchases(user_id, purchase_id);
        CREATE INDEX IF NOT EXISTS idx_purchase_items_pur ON purchase_items(purchase_id);
        CREATE INDEX IF NOT EXISTS idx_sales_returns_uid_orig ON sales_returns(user_id, original_sale_id);
        CREATE INDEX IF NOT EXISTS idx_sales_returns_bill ON sales_returns(user_id, original_bill_id);
        CREATE INDEX IF NOT EXISTS idx_sales_return_items_ret ON sales_return_items(return_id);
        CREATE INDEX IF NOT EXISTS idx_purchase_returns_uid_orig ON purchase_returns(user_id, original_purchase_id);
        CREATE INDEX IF NOT EXISTS idx_purchase_returns_inv ON purchase_returns(user_id, original_invoice_no);
        CREATE INDEX IF NOT EXISTS idx_purchase_return_items_ret ON purchase_return_items(return_id);
        CREATE INDEX IF NOT EXISTS idx_products_uid_name ON products(user_id, name);
        CREATE INDEX IF NOT EXISTS idx_customers_uid_name ON customers(user_id, name);
        CREATE INDEX IF NOT EXISTS idx_suppliers_uid_name ON suppliers(user_id, name);
        CREATE INDEX IF NOT EXISTS idx_users_admin ON users(admin_id);
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        """)
        db.commit()
    except Exception:
        pass
    finally:
        db.close()

@app.route('/api/setup-status', methods=['GET'])
def setup_status():
    user_count = query("SELECT COUNT(*) AS count FROM users", one=True)
    return jsonify({"success": True, "has_users": bool(user_count and user_count['count'] > 0)})

@app.route('/api/register', methods=['POST'])
def api_register():
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    username = (d.get('username') or '').strip()
    password = (d.get('password') or '').strip()
    shop_name = (d.get('shop_name') or name + ' Enterprises').strip()
    phone = (d.get('phone') or '').strip()
    gstin = (d.get('gstin') or '').strip()
    state = (d.get('state') or 'DELHI').strip()

    if not name or not username or not password:
        return jsonify({"success": False, "error": "Name, username, and password are required"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        return jsonify({"success": False, "error": "Username already exists"}), 409

    trial_expiry = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    cur = db.execute("INSERT INTO users (name, username, password, role, plan_status, trial_ends_at, is_master_admin) VALUES (?, ?, ?, 'admin', 'trial', ?, 0)",
                     (name, username, generate_password_hash(password), trial_expiry))
    user_id = cur.lastrowid

    db.execute("INSERT INTO shops (user_id, name, phone, gstin, state, state_code) VALUES (?, ?, ?, ?, ?, ?)",
               (user_id, shop_name, phone, gstin, state, state[:2]))

    for u in ['Pcs', 'Kg', 'Ltr', 'Box', 'Meter', 'Pack']:
        db.execute("INSERT INTO units (user_id, name) VALUES (?, ?)", (user_id, u))
    for c in ['General', 'Grocery', 'Electronics', 'Clothing', 'Services']:
        db.execute("INSERT INTO categories (user_id, name) VALUES (?, ?)", (user_id, c))

    db.commit()

    session.clear()
    session['user_id'] = user_id
    session['login_id'] = user_id
    session['username'] = username
    session['name'] = name
    session['role'] = 'admin'
    session['csrf_token'] = secrets.token_urlsafe(32)

    return jsonify({
        "success": True,
        "username": username,
        "name": name,
        "role": 'admin',
        "csrf_token": session['csrf_token']
    }), 201

@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.get_json(silent=True) or {}
    username = d.get('username', '').strip()
    key = (request.remote_addr or 'unknown', username)
    now = time.monotonic()
    attempts = _login_attempts[key]
    while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        return jsonify({"success": False, "error": "Too many login attempts. Try again later."}), 429
    user = query("SELECT * FROM users WHERE username=?", (username,), one=True)
    if not user or not check_password_hash(user['password'], d.get('password','')):
        attempts.append(now)
        return jsonify({"success": False, "error": "Invalid credentials"}), 401
    _login_attempts.pop(key, None)
    session.clear()
    # Staff work on their shop owner's data; login_id keeps who actually signed in.
    session['user_id']  = user.get('admin_id') or user['id']
    session['login_id'] = user['id']
    session['username'] = user['username']
    session['name']     = user['name']
    session['role']     = user['role']
    session['csrf_token'] = secrets.token_urlsafe(32)
    return jsonify({"success": True, "username": user['username'], "name": user['name'], "role": user['role'], "csrf_token": session['csrf_token']})

@app.route('/api/logout', methods=['POST'])
@login_required
def api_logout():
    session.clear()
    return jsonify({"success": True})

@app.route('/logout')
def logout_page():
    session.clear()
    return redirect(url_for('login_page'))

def get_user_plan_info(uid):
    u = query("SELECT id, name, username, role, admin_id, plan_status, trial_ends_at, subscription_ends_at, is_master_admin FROM users WHERE id=?", (uid,), one=True)
    if not u:
        return {"status": "expired", "days_left": 0, "is_master": False, "label": "⚠️ Expired"}
    if u.get('admin_id'):
        return get_user_plan_info(u['admin_id'])
    if u.get('is_master_admin'):
        return {"status": "lifetime", "days_left": 9999, "is_master": True, "label": "👑 Master Admin"}
    now = datetime.now()
    sub_end = (u.get('subscription_ends_at') or '').strip()
    if sub_end:
        try:
            sub_dt = datetime.strptime(sub_end, '%Y-%m-%d %H:%M:%S') if ' ' in sub_end else datetime.fromisoformat(sub_end)
            if sub_dt > now:
                if sub_dt.year >= 2099 or u.get('plan_status') == 'lifetime':
                    return {"status": "lifetime", "days_left": 9999, "expires_at": "Lifetime", "is_master": False, "label": "👑 Lifetime Pro"}
                days_left = max(1, (sub_dt - now).days)
                return {"status": "active", "days_left": days_left, "expires_at": sub_end, "is_master": False, "label": f"💎 Pro ({days_left}d left)"}
        except Exception:
            pass
    trial_end = (u.get('trial_ends_at') or '').strip()
    if trial_end:
        try:
            tr_dt = datetime.strptime(trial_end, '%Y-%m-%d %H:%M:%S') if ' ' in trial_end else datetime.fromisoformat(trial_end)
            if tr_dt > now:
                days_left = max(1, (tr_dt - now).days)
                return {"status": "trial", "days_left": days_left, "expires_at": trial_end, "is_master": False, "label": f"⏳ Free Trial ({days_left}d left)"}
        except Exception:
            pass
    return {"status": "expired", "days_left": 0, "is_master": False, "label": "⚠️ Trial Expired"}

@app.route('/api/me')
def api_me():
    if 'user_id' not in session:
        return jsonify({"loggedIn": False})
    actual_uid = session.get('login_id') or session['user_id']
    u = query("SELECT is_master_admin, permissions FROM users WHERE id=?", (actual_uid,), one=True) or {}
    is_master = bool(u.get('is_master_admin', 0))
    perms = u.get('permissions', '')
    plan = get_user_plan_info(session['user_id'])
    return jsonify({
        "loggedIn": True,
        "username": session['username'],
        "name": session['name'],
        "role": session['role'],
        "permissions": perms,
        "csrf_token": session.get('csrf_token'),
        "plan": plan,
        "is_master_admin": is_master
    })

@app.route('/api/csrf')
@login_required
def csrf_token():
    return jsonify({'success': True, 'csrf_token': session.get('csrf_token')})

# ─────────────────────────────────────────────────────────────
#  SHOP SETTINGS
# ─────────────────────────────────────────────────────────────
@app.route('/api/shop', methods=['GET'])
@login_required
def get_shop():
    shop = query("SELECT * FROM shops WHERE user_id=?", (get_uid(),), one=True)
    if shop:
        for key in ('gemini_key', 'openai_key', 'anthropic_key'):
            shop[key + '_configured'] = bool(shop.get(key))
            shop.pop(key, None)
    return jsonify({"success": True, "shop": shop})

@app.route('/api/shop', methods=['POST'])
@login_required
def save_shop():
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    d = request.get_json(silent=True) or {}
    db = get_db()
    existing = query("SELECT id FROM shops WHERE user_id=?", (get_uid(),), one=True)
    fields = ['name','address','phone','email','gstin','pan','state','state_code','upi_id','bank_name','bank_acc','bank_ifsc','markup_pct','ai_provider']
    supplied_keys = [key for key in ('gemini_key', 'openai_key', 'anthropic_key') if d.get(key)]
    if existing:
        sets = ', '.join(f"{f}=?" for f in fields + supplied_keys)
        vals = [d.get(f,'') for f in fields] + [d[key] for key in supplied_keys] + [get_uid()]
        db.execute(f"UPDATE shops SET {sets} WHERE user_id=?", vals)
    else:
        cols = ', '.join(['user_id'] + fields + supplied_keys)
        qs   = ', '.join(['?'] * (len(fields) + len(supplied_keys) + 1))
        vals = [get_uid()] + [d.get(f,'') for f in fields] + [d[key] for key in supplied_keys]
        db.execute(f"INSERT INTO shops ({cols}) VALUES ({qs})", vals)
    db.commit()
    return jsonify({"success": True})



# ─────────────────────────────────────────────────────────────
#  STOCK ADJUSTMENT
# ─────────────────────────────────────────────────────────────
@app.route('/api/stock/adjust', methods=['POST'])
@login_required
def stock_adjust():
    uid = get_uid()
    d   = request.get_json(silent=True) or {}
    prod_id = d.get('product_id')
    qty     = float(d.get('qty', 0))
    typ     = d.get('type', 'add')
    reason  = d.get('reason', '')
    notes   = d.get('notes', '')
    if not prod_id or qty <= 0:
        return jsonify({"success": False, "error": "Invalid data"}), 400
    prod = query("SELECT * FROM products WHERE id=? AND user_id=?", (prod_id, uid), one=True)
    if not prod:
        return jsonify({"success": False, "error": "Product not found"}), 404
    db = get_db()
    new_stock = prod['stock'] + qty if typ == 'add' else max(0, prod['stock'] - qty)
    db.execute("UPDATE products SET stock=? WHERE id=? AND user_id=?", (new_stock, prod_id, uid))
    db.execute("""INSERT INTO stock_adjustments (user_id,product_id,product_name,qty,type,reason,notes,created_at)
        VALUES (?,?,?,?,?,?,?,datetime('now'))""",
        (uid, prod_id, prod['name'], qty, typ, reason, notes))
    db.commit()
    return jsonify({"success": True, "new_stock": new_stock})

@app.route('/api/stock/adjustments', methods=['GET'])
@login_required
def get_stock_adjustments():
    uid = get_uid()
    data = rows("SELECT * FROM stock_adjustments WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (uid,))
    return jsonify({"success": True, "adjustments": data})

# ─────────────────────────────────────────────────────────────
#  RETURNS & CREDIT NOTES
# ─────────────────────────────────────────────────────────────
@app.route('/api/returns', methods=['GET'])
@login_required
def get_returns():
    uid = get_uid()
    data = rows("SELECT * FROM returns WHERE user_id=? ORDER BY created_at DESC", (uid,))
    return jsonify({"success": True, "returns": data})

@app.route('/api/returns', methods=['POST'])
@login_required
def create_return():
    uid = get_uid()
    d   = request.get_json(silent=True) or {}
    items = d.get('items', [])
    if not items:
        return jsonify({"success": False, "error": "No items"}), 400
    db = get_db()
    original_bill_id = d.get('original_bill_id', '').strip()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=? AND cancelled=0", (original_bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "A valid active original bill is required"}), 400
    sold = {item['product']: item['qty'] for item in rows("SELECT product, SUM(qty) AS qty FROM sale_items WHERE sale_id=? GROUP BY product", (sale['id'],))}
    already_returned = {item['product']: item['qty'] for item in rows("""SELECT ri.product, SUM(ri.qty) AS qty
        FROM return_items ri JOIN returns r ON r.id=ri.return_id
        WHERE r.user_id=? AND r.original_bill_id=? GROUP BY ri.product""", (uid, original_bill_id))}
    for item in items:
        product, qty = (item.get('product') or '').strip(), float(item.get('qty', 0))
        if not product or qty <= 0 or product not in sold or qty > sold[product] - already_returned.get(product, 0):
            return jsonify({"success": False, "error": "Return quantity exceeds the original sale"}), 400
    import random, string
    return_id = 'RET' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    db.execute("""INSERT INTO returns
        (return_id,user_id,original_bill_id,customer_name,customer_phone,return_date,reason,refund_mode,total_return,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (return_id, uid, d.get('original_bill_id',''), d.get('customer_name',''),
         d.get('customer_phone',''), d.get('return_date', str(date.today())),
         d.get('reason',''), d.get('refund_mode','Cash'),
         float(d.get('total_return', 0))))
    ret_row = db.execute("SELECT last_insert_rowid() as id").fetchone()
    ret_id  = ret_row['id']
    # Add return items + restore stock
    for item in items:
        db.execute("""INSERT INTO return_items (return_id,product,hsn_code,qty,price,amount)
            VALUES (?,?,?,?,?,?)""",
            (ret_id, item.get('product',''), item.get('hsn_code',''),
             float(item.get('qty',0)), float(item.get('price',0)),
             float(item.get('qty',0))*float(item.get('price',0))))
        # Restore stock
        prod = query("SELECT id FROM products WHERE user_id=? AND name=?", (uid, item.get('product','')), one=True)
        if prod:
            db.execute("UPDATE products SET stock=stock+? WHERE id=? AND user_id=?",
                (float(item.get('qty',0)), prod['id'], uid))
    db.commit()
    return jsonify({"success": True, "return_id": return_id}), 201


# ─────────────────────────────────────────────────────────────
#  SALES RETURNS (CREDIT NOTES) & PURCHASE RETURNS (DEBIT NOTES)
# ─────────────────────────────────────────────────────────────

@app.route('/api/sales/<bill_id>/return-context', methods=['GET'])
@login_required
def get_sale_return_context(bill_id):
    """
    Returns original sale transaction with line items and remaining returnable quantities.
    Enables seamless 1-click 'Create Sales Return' with pre-filled context.
    """
    uid = get_uid()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=? AND cancelled=0", (bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "Active sales invoice not found"}), 404

    orig_items = rows("SELECT * FROM sale_items WHERE sale_id=?", (sale['id'],))

    returned_sums = rows("""
        SELECT sri.original_item_id, sri.product, SUM(sri.qty) as returned_qty
        FROM sales_return_items sri
        JOIN sales_returns sr ON sr.id = sri.return_id
        WHERE sr.user_id=? AND sr.original_sale_id=?
        GROUP BY sri.original_item_id, sri.product
    """, (uid, sale['id']))

    ret_map = {}
    for r in returned_sums:
        if r['original_item_id']:
            ret_map[r['original_item_id']] = float(r['returned_qty'])
        else:
            ret_map[r['product']] = float(r['returned_qty'])

    items_context = []
    for it in orig_items:
        sold_qty = float(it.get('qty', 0))
        already_ret = ret_map.get(it['id'], ret_map.get(it['product'], 0.0))
        returnable = max(0.0, round(sold_qty - already_ret, 2))
        items_context.append({
            "original_item_id": it['id'],
            "product": it['product'],
            "hsn_code": it.get('hsn_code', ''),
            "unit": it.get('unit', 'Pcs'),
            "sold_qty": sold_qty,
            "original_qty": sold_qty,
            "already_returned_qty": already_ret,
            "returnable_qty": returnable,
            "price": float(it.get('price', 0)),
            "gst_pct": float(it.get('gst_pct', 18))
        })

    return jsonify({
        "success": True,
        "sale": dict(sale),
        "items": items_context
    })


@app.route('/api/sales/returns', methods=['POST'])
@login_required
def create_sales_return():
    """
    Creates a formal GST Credit Note for a Sales Return.
    Enforces maximum returnable quantity, computes taxes, and restores inventory.
    """
    uid = get_uid()
    d = request.get_json(silent=True) or {}
    orig_bill_id = (d.get('original_bill_id') or '').strip()
    return_items_req = d.get('items', [])
    if not orig_bill_id or not return_items_req:
        return jsonify({"success": False, "error": "Original invoice and return items required"}), 400

    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=? AND cancelled=0", (orig_bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "Original invoice not found or cancelled"}), 404

    orig_items = {it['id']: it for it in rows("SELECT * FROM sale_items WHERE sale_id=?", (sale['id'],))}

    returned_sums = rows("""
        SELECT sri.original_item_id, sri.product, SUM(sri.qty) as returned_qty
        FROM sales_return_items sri
        JOIN sales_returns sr ON sr.id = sri.return_id
        WHERE sr.user_id=? AND sr.original_sale_id=?
        GROUP BY sri.original_item_id, sri.product
    """, (uid, sale['id']))
    ret_map = {r['original_item_id']: float(r['returned_qty']) for r in returned_sums if r['original_item_id']}

    from tax_engine import calculate_line_item_tax
    is_inter = bool(sale.get('is_interstate', 0) or sale.get('igst_amount', 0) > 0)
    tx_date = d.get('return_date') or str(date.today())

    valid_return_items = []
    tot_taxable = tot_cgst = tot_sgst = tot_igst = tot_amount = 0.0

    for req_it in return_items_req:
        qty = float(req_it.get('qty') if req_it.get('qty') is not None else (req_it.get('return_qty') or 0))
        if qty <= 0:
            continue
        orig_id = req_it.get('original_item_id')
        orig_it = orig_items.get(orig_id)
        if not orig_it:
            orig_it = next((v for v in orig_items.values() if v['product'] == req_it.get('product')), None)
            if not orig_it:
                return jsonify({"success": False, "error": f"Item '{req_it.get('product')}' not found in original sale"}), 400

        sold_q = float(orig_it.get('qty', 0))
        already_q = ret_map.get(orig_it['id'], 0.0)
        max_returnable = max(0.0, round(sold_q - already_q, 2))
        if qty > max_returnable + 0.001:
            return jsonify({
                "success": False,
                "error": f"Return quantity for '{orig_it['product']}' ({qty}) exceeds available returnable quantity ({max_returnable})."
            }), 400

        price = float(orig_it.get('price', 0))
        gst_pct = float(orig_it.get('gst_pct', 18))
        line_calc = calculate_line_item_tax(qty, price, gst_pct, is_inter, tx_date)

        valid_return_items.append({
            "original_item_id": orig_it['id'],
            "product": orig_it['product'],
            "hsn_code": orig_it.get('hsn_code', ''),
            "unit": orig_it.get('unit', 'Pcs'),
            "qty": qty,
            "price": price,
            "taxable_amount": line_calc['taxable'],
            "gst_pct": line_calc['gst_pct'],
            "cgst_amount": line_calc['cgst'],
            "sgst_amount": line_calc['sgst'],
            "igst_amount": line_calc['igst'],
            "total_amount": line_calc['total']
        })

        tot_taxable += line_calc['taxable']
        tot_cgst += line_calc['cgst']
        tot_sgst += line_calc['sgst']
        tot_igst += line_calc['igst']
        tot_amount += line_calc['total']

    if not valid_return_items:
        return jsonify({"success": False, "error": "No valid items selected for return"}), 400

    year = str(date.today().year)
    last_cn = query("SELECT credit_note_no FROM sales_returns WHERE user_id=? AND credit_note_no LIKE ? ORDER BY id DESC LIMIT 1",
                    (uid, f"CN-{year}-%"), one=True)
    if last_cn:
        try:
            num = int(last_cn['credit_note_no'].split('-')[-1])
            cn_no = f"CN-{year}-{(num + 1):04d}"
        except Exception:
            cn_no = f"CN-{year}-0001"
    else:
        cn_no = f"CN-{year}-0001"

    db = get_db()
    db.execute("""
        INSERT INTO sales_returns (
            credit_note_no, user_id, original_sale_id, original_bill_id, original_sale_date,
            customer_name, customer_phone, customer_gstin, place_of_supply, is_interstate,
            return_date, reason, subtotal, cgst_amount, sgst_amount, igst_amount, total_amount,
            refund_mode, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cn_no, uid, sale['id'], sale['bill_id'], sale.get('sale_date', ''),
        sale.get('customer_name', ''), sale.get('customer_phone', ''), sale.get('customer_gstin', ''),
        sale.get('state_code', '07'), int(is_inter),
        tx_date, d.get('reason', 'Goods Return'),
        round(tot_taxable, 2), round(tot_cgst, 2), round(tot_sgst, 2), round(tot_igst, 2), round(tot_amount, 2),
        d.get('refund_mode', 'Credit Note'), d.get('notes', '')
    ))
    cn_id = db.execute("SELECT last_insert_rowid() as id").fetchone()['id']

    for it in valid_return_items:
        db.execute("""
            INSERT INTO sales_return_items (
                return_id, original_item_id, product, hsn_code, qty, unit, price,
                taxable_amount, gst_pct, cgst_amount, sgst_amount, igst_amount, total_amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cn_id, it['original_item_id'], it['product'], it['hsn_code'], it['qty'], it['unit'], it['price'],
            it['taxable_amount'], it['gst_pct'], it['cgst_amount'], it['sgst_amount'], it['igst_amount'], it['total_amount']
        ))
        db.execute("UPDATE products SET stock = stock + ? WHERE user_id=? AND name=?", (it['qty'], uid, it['product']))
        prod = query("SELECT id FROM products WHERE user_id=? AND name=?", (uid, it['product']), one=True)
        if prod:
            db.execute("""
                INSERT INTO stock_adjustments (user_id, product_id, product_name, qty, type, reason, notes)
                VALUES (?, ?, ?, ?, 'add', 'Sales Return', ?)
            """, (uid, prod['id'], it['product'], it['qty'], f"Credit Note {cn_no} against {sale['bill_id']}"))

    db.commit()
    return jsonify({
        "success": True,
        "credit_note_no": cn_no,
        "id": cn_id,
        "total_taxable": round(tot_taxable, 2),
        "total_cgst": round(tot_cgst, 2),
        "total_sgst": round(tot_sgst, 2),
        "total_igst": round(tot_igst, 2),
        "total_amount": round(tot_amount, 2),
        "message": f"Credit Note {cn_no} created successfully and stock restored!"
    }), 201


@app.route('/api/sales/returns', methods=['GET'])
@login_required
def get_sales_returns():
    uid = get_uid()
    month = request.args.get('month')
    sql = "SELECT * FROM sales_returns WHERE user_id=?"
    params = [uid]
    if month:
        sql += " AND return_date LIKE ?"
        params.append(month + '%')
    sql += " ORDER BY return_date DESC, id DESC"
    returns = rows(sql, params)
    for r in returns:
        r['items'] = rows("SELECT * FROM sales_return_items WHERE return_id=?", (r['id'],))
    return jsonify({"success": True, "sales_returns": returns})


@app.route('/api/sales/returns/<credit_note_no>', methods=['GET'])
@login_required
def get_single_sales_return(credit_note_no):
    uid = get_uid()
    cn = query("SELECT * FROM sales_returns WHERE credit_note_no=? AND user_id=?", (credit_note_no, uid), one=True)
    if not cn:
        return jsonify({"success": False, "error": "Credit Note not found"}), 404
    items = rows("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    return jsonify({"success": True, "credit_note": dict(cn), "items": items, "shop": dict(shop)})


# ── PURCHASE RETURNS (DEBIT NOTES) ──

@app.route('/api/purchases/<purchase_ref>/return-context', methods=['GET'])
@login_required
def get_purchase_return_context(purchase_ref):
    """
    Returns original purchase invoice with line items and remaining returnable quantities.
    Enables seamless 1-click 'Create Purchase Return' with pre-filled context.
    """
    uid = get_uid()
    pur = query("""
        SELECT * FROM purchases 
        WHERE user_id=? AND (id=? OR purchase_id=? OR invoice_no=?)
    """, (uid, purchase_ref, purchase_ref, purchase_ref), one=True)
    if not pur:
        return jsonify({"success": False, "error": "Purchase invoice not found"}), 404

    orig_items = rows("SELECT * FROM purchase_items WHERE purchase_id=?", (pur['id'],))

    returned_sums = rows("""
        SELECT pri.original_item_id, pri.product, SUM(pri.qty) as returned_qty
        FROM purchase_return_items pri
        JOIN purchase_returns pr ON pr.id = pri.return_id
        WHERE pr.user_id=? AND pr.original_purchase_id=?
        GROUP BY pri.original_item_id, pri.product
    """, (uid, pur['id']))

    ret_map = {}
    for r in returned_sums:
        if r['original_item_id']:
            ret_map[r['original_item_id']] = float(r['returned_qty'])
        else:
            ret_map[r['product']] = float(r['returned_qty'])

    items_context = []
    for it in orig_items:
        purchased_qty = float(it.get('qty', 0))
        already_ret = ret_map.get(it['id'], ret_map.get(it['product'], 0.0))
        returnable = max(0.0, round(purchased_qty - already_ret, 2))
        price = float(it.get('buy_price', it.get('price', 0)))
        items_context.append({
            "original_item_id": it['id'],
            "product": it['product'],
            "hsn_code": it.get('hsn_code', ''),
            "unit": it.get('unit', 'Pcs'),
            "purchased_qty": purchased_qty,
            "original_qty": purchased_qty,
            "already_returned_qty": already_ret,
            "returnable_qty": returnable,
            "price": price,
            "gst_pct": float(it.get('gst_pct', 18))
        })

    return jsonify({
        "success": True,
        "purchase": dict(pur),
        "items": items_context
    })


@app.route('/api/purchases/returns', methods=['POST'])
@login_required
def create_purchase_return():
    """
    Creates a formal GST Debit Note for a Purchase Return.
    Enforces maximum returnable quantity, computes taxes, and deducts inventory.
    """
    uid = get_uid()
    d = request.get_json(silent=True) or {}
    pur_ref = str(d.get('original_purchase_id') or d.get('original_invoice_no') or d.get('original_purchase_ref') or '').strip()
    return_items_req = d.get('items', [])
    if not pur_ref or not return_items_req:
        return jsonify({"success": False, "error": "Original purchase invoice and return items required"}), 400

    pur = query("""
        SELECT * FROM purchases 
        WHERE user_id=? AND (id=? OR purchase_id=? OR invoice_no=?)
    """, (uid, pur_ref, pur_ref, pur_ref), one=True)
    if not pur:
        return jsonify({"success": False, "error": "Original purchase invoice not found"}), 404

    orig_items = {it['id']: it for it in rows("SELECT * FROM purchase_items WHERE purchase_id=?", (pur['id'],))}

    returned_sums = rows("""
        SELECT pri.original_item_id, pri.product, SUM(pri.qty) as returned_qty
        FROM purchase_return_items pri
        JOIN purchase_returns pr ON pr.id = pri.return_id
        WHERE pr.user_id=? AND pr.original_purchase_id=?
        GROUP BY pri.original_item_id, pri.product
    """, (uid, pur['id']))
    ret_map = {r['original_item_id']: float(r['returned_qty']) for r in returned_sums if r['original_item_id']}

    from tax_engine import calculate_line_item_tax, is_interstate_supply
    shop_rec = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    shop_code = (shop_rec.get('gstin') or '')[:2] or shop_rec.get('state_code') or '07'
    party_code = (pur.get('supplier_gstin') or '')[:2] or pur.get('state_code') or ''
    is_inter = is_interstate_supply(shop_code, party_code) if (party_code and party_code != '00') else bool(pur.get('is_interstate', 0) or pur.get('igst_amount', 0) > 0)
    tx_date = d.get('return_date') or str(date.today())

    valid_return_items = []
    tot_taxable = tot_cgst = tot_sgst = tot_igst = tot_amount = 0.0

    for req_it in return_items_req:
        qty = float(req_it.get('qty') if req_it.get('qty') is not None else (req_it.get('return_qty') or 0))
        if qty <= 0:
            continue
        orig_id = req_it.get('original_item_id')
        orig_it = orig_items.get(orig_id)
        if not orig_it:
            orig_it = next((v for v in orig_items.values() if v['product'] == req_it.get('product')), None)
            if not orig_it:
                return jsonify({"success": False, "error": f"Item '{req_it.get('product')}' not found in original purchase"}), 400

        purchased_q = float(orig_it.get('qty', 0))
        already_q = ret_map.get(orig_it['id'], 0.0)
        max_returnable = max(0.0, round(purchased_q - already_q, 2))
        if qty > max_returnable + 0.001:
            return jsonify({
                "success": False,
                "error": f"Return quantity for '{orig_it['product']}' ({qty}) exceeds available returnable quantity ({max_returnable})."
            }), 400

        price = float(orig_it.get('buy_price', orig_it.get('price', 0)))
        gst_pct = float(orig_it.get('gst_pct', 18))
        line_calc = calculate_line_item_tax(qty, price, gst_pct, is_inter, tx_date)

        valid_return_items.append({
            "original_item_id": orig_it['id'],
            "product": orig_it['product'],
            "hsn_code": orig_it.get('hsn_code', ''),
            "unit": orig_it.get('unit', 'Pcs'),
            "qty": qty,
            "price": price,
            "taxable_amount": line_calc['taxable'],
            "gst_pct": line_calc['gst_pct'],
            "cgst_amount": line_calc['cgst'],
            "sgst_amount": line_calc['sgst'],
            "igst_amount": line_calc['igst'],
            "total_amount": line_calc['total']
        })

        tot_taxable += line_calc['taxable']
        tot_cgst += line_calc['cgst']
        tot_sgst += line_calc['sgst']
        tot_igst += line_calc['igst']
        tot_amount += line_calc['total']

    if not valid_return_items:
        return jsonify({"success": False, "error": "No valid items selected for purchase return"}), 400

    year = str(date.today().year)
    last_dn = query("SELECT debit_note_no FROM purchase_returns WHERE user_id=? AND debit_note_no LIKE ? ORDER BY id DESC LIMIT 1",
                    (uid, f"DN-{year}-%"), one=True)
    if last_dn:
        try:
            num = int(last_dn['debit_note_no'].split('-')[-1])
            dn_no = f"DN-{year}-{(num + 1):04d}"
        except Exception:
            dn_no = f"DN-{year}-0001"
    else:
        dn_no = f"DN-{year}-0001"

    inv_display = pur.get('invoice_no') or pur.get('purchase_id') or f"PUR-{pur['id']}"
    db = get_db()
    db.execute("""
        INSERT INTO purchase_returns (
            debit_note_no, user_id, original_purchase_id, original_invoice_no, original_bill_date,
            supplier_name, supplier_phone, supplier_gstin, place_of_supply, is_interstate,
            return_date, reason, subtotal, cgst_amount, sgst_amount, igst_amount, total_amount,
            refund_mode, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        dn_no, uid, pur['id'], inv_display, pur.get('bill_date', ''),
        pur.get('supplier_name', ''), pur.get('supplier_phone', ''), pur.get('supplier_gstin', ''),
        pur.get('state_code', '07'), int(is_inter),
        tx_date, d.get('reason', 'Defective Goods Return'),
        round(tot_taxable, 2), round(tot_cgst, 2), round(tot_sgst, 2), round(tot_igst, 2), round(tot_amount, 2),
        d.get('refund_mode', 'Debit Note'), d.get('notes', '')
    ))
    dn_id = db.execute("SELECT last_insert_rowid() as id").fetchone()['id']

    for it in valid_return_items:
        db.execute("""
            INSERT INTO purchase_return_items (
                return_id, original_item_id, product, hsn_code, qty, unit, price,
                taxable_amount, gst_pct, cgst_amount, sgst_amount, igst_amount, total_amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dn_id, it['original_item_id'], it['product'], it['hsn_code'], it['qty'], it['unit'], it['price'],
            it['taxable_amount'], it['gst_pct'], it['cgst_amount'], it['sgst_amount'], it['igst_amount'], it['total_amount']
        ))
        db.execute("UPDATE products SET stock = MAX(0, stock - ?) WHERE user_id=? AND name=?", (it['qty'], uid, it['product']))
        prod = query("SELECT id FROM products WHERE user_id=? AND name=?", (uid, it['product']), one=True)
        if prod:
            db.execute("""
                INSERT INTO stock_adjustments (user_id, product_id, product_name, qty, type, reason, notes)
                VALUES (?, ?, ?, ?, 'remove', 'Purchase Return', ?)
            """, (uid, prod['id'], it['product'], it['qty'], f"Debit Note {dn_no} against {inv_display}"))

    db.commit()
    return jsonify({
        "success": True,
        "debit_note_no": dn_no,
        "id": dn_id,
        "total_taxable": round(tot_taxable, 2),
        "total_cgst": round(tot_cgst, 2),
        "total_sgst": round(tot_sgst, 2),
        "total_igst": round(tot_igst, 2),
        "total_amount": round(tot_amount, 2),
        "message": f"Debit Note {dn_no} created successfully and stock adjusted!"
    }), 201


@app.route('/api/purchases/returns', methods=['GET'])
@login_required
def get_purchase_returns():
    uid = get_uid()
    month = request.args.get('month')
    sql = "SELECT * FROM purchase_returns WHERE user_id=?"
    params = [uid]
    if month:
        sql += " AND return_date LIKE ?"
        params.append(month + '%')
    sql += " ORDER BY return_date DESC, id DESC"
    returns = rows(sql, params)
    for r in returns:
        r['items'] = rows("SELECT * FROM purchase_return_items WHERE return_id=?", (r['id'],))
    return jsonify({"success": True, "purchase_returns": returns})


@app.route('/api/purchases/returns/<debit_note_no>', methods=['GET'])
@login_required
def get_single_purchase_return(debit_note_no):
    uid = get_uid()
    dn = query("SELECT * FROM purchase_returns WHERE debit_note_no=? AND user_id=?", (debit_note_no, uid), one=True)
    if not dn:
        return jsonify({"success": False, "error": "Debit Note not found"}), 404
    items = rows("SELECT * FROM purchase_return_items WHERE return_id=?", (dn['id'],))
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    return jsonify({"success": True, "debit_note": dict(dn), "items": items, "shop": dict(shop)})


# ── GST RETURN CENTER (GSTR-1, GSTR-3B & AUDIT RECONCILIATION) ──

@app.route('/api/gst/return-center', methods=['GET'])
@login_required
def get_gst_return_center():
    """
    Computes GSTR-1, GSTR-3B, and Book vs Return Audit Reconciliation for a month.
    """
    uid = get_uid()
    month = request.args.get('month', str(date.today())[:7])
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}

    raw_sales = rows("SELECT * FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0 AND (bill_category != 'kacha' OR bill_category IS NULL) ORDER BY sale_date ASC", (uid, month + '%'))
    sales = []
    for s in raw_sales:
        s_dict = dict(s)
        s_dict['items'] = rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))
        sales.append(s_dict)

    raw_cn = rows("SELECT * FROM sales_returns WHERE user_id=? AND return_date LIKE ? ORDER BY return_date ASC", (uid, month + '%'))
    sales_returns = []
    for cn in raw_cn:
        cn_dict = dict(cn)
        cn_dict['items'] = rows("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))
        sales_returns.append(cn_dict)

    raw_pur = rows("SELECT * FROM purchases WHERE user_id=? AND bill_date LIKE ? ORDER BY bill_date ASC", (uid, month + '%'))
    purchases = []
    for p in raw_pur:
        p_dict = dict(p)
        p_dict['items'] = rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))
        purchases.append(p_dict)

    raw_dn = rows("SELECT * FROM purchase_returns WHERE user_id=? AND return_date LIKE ? ORDER BY return_date ASC", (uid, month + '%'))
    purchase_returns = []
    for dn in raw_dn:
        dn_dict = dict(dn)
        dn_dict['items'] = rows("SELECT * FROM purchase_return_items WHERE return_id=?", (dn['id'],))
        purchase_returns.append(dn_dict)

    from gst_return_engine import prepare_gstr1_payload, prepare_gstr3b_payload, perform_book_reconciliation
    gstr1 = prepare_gstr1_payload(shop, sales, sales_returns, month)
    gstr3b = prepare_gstr3b_payload(shop, sales, sales_returns, purchases, purchase_returns, month)
    reconciliation = perform_book_reconciliation(sales, sales_returns, purchases, purchase_returns, gstr1, gstr3b)
    tax_rules = [dict(r) for r in rows("SELECT * FROM tax_rules WHERE is_active=1 ORDER BY rate ASC")]

    return jsonify({
        "success": True,
        "month": month,
        "shop": dict(shop),
        "gstr1": gstr1,
        "gstr3b": gstr3b,
        "reconciliation": reconciliation,
        "tax_rules": tax_rules
    })


@app.route('/api/gst/return-center/export/gstr1-json', methods=['GET'])
@login_required
def export_gstr1_json():
    uid = get_uid()
    month = request.args.get('month', str(date.today())[:7])
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}

    raw_sales = rows("SELECT * FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0 AND (bill_category != 'kacha' OR bill_category IS NULL) ORDER BY sale_date ASC", (uid, month + '%'))
    sales = []
    for s in raw_sales:
        s_dict = dict(s)
        s_dict['items'] = rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))
        sales.append(s_dict)

    raw_cn = rows("SELECT * FROM sales_returns WHERE user_id=? AND return_date LIKE ? ORDER BY return_date ASC", (uid, month + '%'))
    sales_returns = []
    for cn in raw_cn:
        cn_dict = dict(cn)
        cn_dict['items'] = rows("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))
        sales_returns.append(cn_dict)

    from gst_return_engine import prepare_gstr1_payload
    payload = prepare_gstr1_payload(shop, sales, sales_returns, month)
    import json
    json_bytes = json.dumps(payload, indent=2).encode('utf-8')

    filename = f"GSTR1_{shop.get('gstin', 'BILLZEN')}_{month.replace('-', '')}.json"
    return Response(
        json_bytes,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route('/api/gst/return-center/export/gstr3b-csv', methods=['GET'])
@login_required
def export_gstr3b_csv():
    import io, csv
    uid = get_uid()
    month = request.args.get('month', str(date.today())[:7])
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}

    raw_sales = rows("SELECT * FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0 AND (bill_category != 'kacha' OR bill_category IS NULL)", (uid, month + '%'))
    raw_cn = rows("SELECT * FROM sales_returns WHERE user_id=? AND return_date LIKE ?", (uid, month + '%'))
    raw_pur = rows("SELECT * FROM purchases WHERE user_id=? AND bill_date LIKE ?", (uid, month + '%'))
    raw_dn = rows("SELECT * FROM purchase_returns WHERE user_id=? AND return_date LIKE ?", (uid, month + '%'))

    from gst_return_engine import prepare_gstr3b_payload
    gstr3b = prepare_gstr3b_payload(shop, raw_sales, raw_cn, raw_pur, raw_dn, month)

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["GSTR-3B MONTHLY SUMMARY", f"Period: {month}", f"GSTIN: {shop.get('gstin', '')}"])
    writer.writerow([])
    writer.writerow(["Table", "Description", "Taxable Value (Rs)", "IGST (Rs)", "CGST (Rs)", "SGST (Rs)", "Total Tax (Rs)"])

    t3 = gstr3b['table_3_1']['outward_taxable_supplies']
    tot_out = t3['iamt'] + t3['camt'] + t3['samt']
    writer.writerow(["3.1(a)", "Outward taxable supplies (other than zero rated/nil/exempted)", t3['txval'], t3['iamt'], t3['camt'], t3['samt'], tot_out])
    writer.writerow(["3.1(b)", "Outward taxable supplies (zero rated)", 0.0, 0.0, 0.0, 0.0, 0.0])
    writer.writerow(["3.1(c)", "Other outward supplies (Nil rated, exempted)", 0.0, "-", "-", "-", 0.0])

    itc = gstr3b['table_4_itc']['itc_net']
    writer.writerow(["4(A)(5)", "Eligible ITC - All other ITC (Purchases less Debit Notes)", "-", itc['iamt'], itc['camt'], itc['samt'], itc['total_itc']])

    pay = gstr3b['table_6_1']['tax_payable']
    writer.writerow(["6.1", "Net Tax Payable in Cash (Outward Tax less Eligible ITC)", "-", pay['iamt'], pay['camt'], pay['samt'], pay['total_payable']])

    return Response(
        out.getvalue().encode('utf-8-sig'),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=GSTR3B_{month}.csv"}
    )


# ── TAX RULES ARCHITECTURE (DYNAMIC CONFIGURATION) ──

@app.route('/api/tax-rules', methods=['GET'])
@login_required
def get_tax_rules():
    db_rules = rows("SELECT * FROM tax_rules ORDER BY rate ASC")
    return jsonify({"success": True, "tax_rules": db_rules})


@app.route('/api/tax-rules', methods=['POST'])
@login_required
def save_tax_rule():
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    d = request.get_json(silent=True) or {}
    rate = float(d.get('rate', 0))
    cgst = float(d.get('cgst_rate', rate / 2.0))
    sgst = float(d.get('sgst_rate', rate / 2.0))
    igst = float(d.get('igst_rate', rate))
    eff_from = d.get('effective_from', '2017-07-01')
    eff_to = d.get('effective_to', '2099-12-31')
    desc = d.get('description', '')
    is_active = int(d.get('is_active', 1))

    db = get_db()
    existing = query("SELECT id FROM tax_rules WHERE rate=?", (rate,), one=True)
    if existing:
        db.execute("""
            UPDATE tax_rules SET cgst_rate=?, sgst_rate=?, igst_rate=?, effective_from=?, effective_to=?, description=?, is_active=?
            WHERE id=?
        """, (cgst, sgst, igst, eff_from, eff_to, desc, is_active, existing['id']))
    else:
        db.execute("""
            INSERT INTO tax_rules (rate, cgst_rate, sgst_rate, igst_rate, effective_from, effective_to, description, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (rate, cgst, sgst, igst, eff_from, eff_to, desc, is_active))
    db.commit()
    return jsonify({"success": True, "message": f"Tax rule for {rate}% saved successfully!"})


# ── TALLY LEDGER MAPPINGS (PERSISTENT CHART OF ACCOUNTS) ──

@app.route('/api/tally/ledger-mappings', methods=['GET'])
@login_required
def get_tally_ledger_mappings():
    uid = get_uid()
    from tally_engine import DEFAULT_LEDGER_MAP
    custom = {r['key_name']: r['ledger_name'] for r in rows("SELECT key_name, ledger_name FROM shop_ledger_mappings WHERE user_id=?", (uid,))}
    merged = dict(DEFAULT_LEDGER_MAP)
    merged.update(custom)
    return jsonify({"success": True, "ledger_mappings": merged, "mappings": merged})


@app.route('/api/tally/ledger-mappings', methods=['POST'])
@login_required
def save_tally_ledger_mappings():
    uid = get_uid()
    d = request.get_json(silent=True) or {}
    mappings = d.get('ledger_map') or d
    db = get_db()
    for k, v in mappings.items():
        if isinstance(v, str) and v.strip():
            db.execute("""
                INSERT INTO shop_ledger_mappings (user_id, key_name, ledger_name, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(user_id, key_name) DO UPDATE SET ledger_name=excluded.ledger_name, updated_at=excluded.updated_at
            """, (uid, k.strip(), v.strip()))
    db.commit()
    return jsonify({"success": True, "message": "Tally Ledger Mappings saved successfully!"})


# ─────────────────────────────────────────────────────────────
#  STAFF MANAGEMENT
# ─────────────────────────────────────────────────────────────
@app.route('/api/staff', methods=['GET'])
@login_required
def get_staff():
    # Only admin (shop owner) can see staff
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    uid = get_uid()
    # Get all staff members created under this shop owner (NEVER return passwords)
    data = rows("SELECT id, name, username, role, permissions, created_at FROM users WHERE admin_id=?", (uid,))
    return jsonify({"success": True, "staff": data})

@app.route('/api/staff', methods=['POST'])
@login_required
def add_staff():
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    d        = request.get_json(silent=True) or {}
    name     = d.get('name','').strip()
    username = d.get('username','').strip()
    password = d.get('password','').strip()
    role     = d.get('role', 'staff').strip()
    perms    = d.get('permissions', '').strip()
    if role not in ('cashier', 'staff', 'manager', 'admin'):
        role = 'staff'
    if not name or not username or not password:
        return jsonify({"success": False, "error": "Name, username, and password required"}), 400
    existing = query("SELECT id FROM users WHERE username=?", (username,), one=True)
    if existing:
        return jsonify({"success": False, "error": f"Username '{username}' already exists. Please choose a different username."}), 400
    from werkzeug.security import generate_password_hash
    db = get_db()
    # Never persist plaintext password in database
    db.execute("INSERT INTO users (name, username, password, plain_password, role, permissions, admin_id) VALUES (?,?,?,?,?,?,?)",
        (name, username, generate_password_hash(password), '', role, perms, get_uid()))
    db.commit()
    return jsonify({"success": True}), 201

@app.route('/api/staff/<int:sid>', methods=['DELETE'])
@login_required
def delete_staff(sid):
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    uid = get_uid()
    db  = get_db()
    db.execute("DELETE FROM users WHERE id=? AND admin_id=?", (sid, uid))
    db.commit()
    return jsonify({"success": True})

@app.route('/api/staff/<int:sid>/password', methods=['PUT'])
@login_required
def change_staff_password(sid):
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    d   = request.get_json(silent=True) or {}
    pwd = d.get('password','').strip()
    if not pwd:
        return jsonify({"success": False, "error": "Password required"}), 400
    from werkzeug.security import generate_password_hash
    db = get_db()
    db.execute("UPDATE users SET password=?, plain_password='' WHERE id=? AND admin_id=?",
        (generate_password_hash(pwd), sid, get_uid()))
    db.commit()
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────
#  AI BILL SCANNER — Backend proxy
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
#  AI PROVIDERS (Anthropic / OpenAI / Gemini) — keys never leave the server
# ─────────────────────────────────────────────────────────────
AI_PROVIDERS    = ('anthropic', 'openai', 'gemini')
ANTHROPIC_MODEL = 'claude-opus-5-5'
OPENAI_CHAT_MODEL, OPENAI_VISION_MODEL = 'gpt-4o-mini', 'gpt-4o-mini'
GEMINI_MODEL    = 'gemini-2.5-flash'


class AIError(Exception):
    pass


def pick_ai_provider(shop=None):
    """Use custom shop key if configured; otherwise fall back to platform cloud AI keys."""
    shop = shop or {}
    preferred = shop.get('ai_provider') or os.environ.get('AI_PROVIDER') or 'openai'
    for provider in [preferred] + [p for p in AI_PROVIDERS if p != preferred]:
        if shop.get(provider + '_key'):
            return provider, shop[provider + '_key']
    for provider in [preferred] + [p for p in AI_PROVIDERS if p != preferred]:
        env_key = os.environ.get(f'{provider.upper()}_API_KEY')
        if env_key:
            return provider, env_key
    return None, None


def _post_json(url, body, headers, timeout=90):
    import json as jsonlib, urllib.request, urllib.error
    req = urllib.request.Request(url, data=jsonlib.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return jsonlib.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = jsonlib.loads(e.read()).get('error', {})
            msg = err.get('message') if isinstance(err, dict) else str(err)
        except Exception:
            msg = None
        raise AIError(msg or f'AI provider returned HTTP {e.code}')
    except (urllib.error.URLError, TimeoutError):
        raise AIError('Could not reach the AI provider. Check your internet connection.')


def process_pdf_document(pdf_b64):
    """
    Given a base64-encoded PDF:
    1. Extracts clean text from all pages using PyMuPDF.
    2. Renders pages (up to 2 pages stitched) to high-resolution JPEG image.
    Returns: (extracted_text, rendered_jpeg_b64)
    """
    import base64, io
    import fitz
    from PIL import Image

    if ',' in pdf_b64:
        pdf_b64 = pdf_b64.split(',', 1)[1]
    pdf_bytes = base64.b64decode(pdf_b64)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pages_text = []
    for i, page in enumerate(doc):
        txt = page.get_text().strip()
        if txt:
            pages_text.append(f"--- INVOICE PAGE {i+1} TEXT ---\n{txt}")

    extracted_text = "\n\n".join(pages_text)

    rendered_jpeg_b64 = None
    page_count = len(doc)
    if page_count == 1:
        pix = doc[0].get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        rendered_jpeg_b64 = base64.b64encode(img_bytes).decode("utf-8")
    elif page_count > 1:
        imgs = []
        for i in range(min(page_count, 2)):
            pix = doc[i].get_pixmap(dpi=150)
            imgs.append(Image.open(io.BytesIO(pix.tobytes("jpeg"))))
        total_h = sum(im.height for im in imgs)
        max_w = max(im.width for im in imgs)
        stitched = Image.new('RGB', (max_w, total_h), (255, 255, 255))
        y_off = 0
        for im in imgs:
            stitched.paste(im, (0, y_off))
            y_off += im.height
        buf = io.BytesIO()
        stitched.save(buf, format="JPEG", quality=85)
        rendered_jpeg_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return extracted_text, rendered_jpeg_b64


def ai_complete(provider, key, system, messages, attachment=None, json_schema=None, effort='medium'):
    """Run one completion. messages: [{'role': 'user'|'assistant', 'content': str}].
    attachment: (mime_type, base64_data) added to the last user message."""
    mime, b64 = attachment or (None, None)
    is_pdf = mime == 'application/pdf'

    if provider == 'anthropic':
        import anthropic
        msgs = [dict(m) for m in messages]
        if attachment:
            source = {'type': 'base64', 'media_type': mime, 'data': b64}
            block = {'type': 'document' if is_pdf else 'image', 'source': source}
            msgs[-1] = {'role': 'user', 'content': [block, {'type': 'text', 'text': msgs[-1]['content']}]}
        output_config = {'effort': effort}
        if json_schema:
            output_config['format'] = {'type': 'json_schema', 'schema': json_schema}
        kwargs = dict(model=ANTHROPIC_MODEL, max_tokens=16000, messages=msgs, output_config=output_config,
                      betas=['server-side-fallback-2026-07-01'], fallbacks='default')
        if system:
            kwargs['system'] = system
        try:
            resp = anthropic.Anthropic(api_key=key, timeout=120).beta.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise AIError('Anthropic API key is invalid. Update it in Settings.')
        except anthropic.RateLimitError:
            raise AIError('Anthropic rate limit reached. Please try again in a minute.')
        except anthropic.APIStatusError as e:
            raise AIError(f'Anthropic error: {e.message}')
        except anthropic.APIConnectionError:
            raise AIError('Could not reach Anthropic. Check your internet connection.')
        if resp.stop_reason == 'refusal':
            raise AIError('The AI declined this request.')
        return next((b.text for b in resp.content if b.type == 'text'), '')

    if provider == 'openai':
        if is_pdf:
            try:
                _, rendered_jpeg = process_pdf_document(b64)
                if rendered_jpeg:
                    mime = 'image/jpeg'
                    b64 = rendered_jpeg
                    is_pdf = False
                else:
                    raise AIError('Could not render PDF. Upload an image or switch provider.')
            except AIError:
                raise
            except Exception as e:
                raise AIError(f'PDF conversion error for OpenAI: {e}')

        msgs = ([{'role': 'system', 'content': system}] if system else []) + [dict(m) for m in messages]
        if attachment:
            msgs[-1] = {'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}},
                {'type': 'text', 'text': msgs[-1]['content']}]}
        body = {'model': OPENAI_VISION_MODEL if attachment else OPENAI_CHAT_MODEL,
                'messages': msgs, 'max_tokens': 4000 if json_schema else 600}
        if json_schema:
            body['response_format'] = {'type': 'json_object'}
        resp = _post_json('https://api.openai.com/v1/chat/completions', body, {'Authorization': f'Bearer {key}'})
        return (resp.get('choices') or [{}])[0].get('message', {}).get('content', '') or ''

    if provider == 'gemini':
        contents = [{'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': [{'text': m['content']}]}
                    for m in messages]
        if attachment:
            contents[-1]['parts'].insert(0, {'inline_data': {'mime_type': mime, 'data': b64}})
        body = {'contents': contents}
        if system:
            body['system_instruction'] = {'parts': [{'text': system}]}
        if json_schema:
            body['generationConfig'] = {'responseMimeType': 'application/json'}
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'
        resp = _post_json(url, body, {'x-goog-api-key': key})
        parts = ((resp.get('candidates') or [{}])[0].get('content') or {}).get('parts') or [{}]
        return ''.join(p.get('text', '') for p in parts)

    raise AIError(f'Unknown AI provider: {provider}')


def _str(desc=''):
    return {'type': 'string', 'description': desc} if desc else {'type': 'string'}


BILL_SCHEMA = {
    'type': 'object',
    'properties': {
        'supplier_name': _str(), 'supplier_gstin': _str(), 'supplier_address': _str('Full seller address'),
        'supplier_phone': _str(), 'supplier_email': _str(), 'invoice_no': _str(), 'irn_no': _str(),
        'eway_bill_no': _str(), 'buyer_gstin': _str(), 'bill_date': _str('YYYY-MM-DD'),
        'items': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'product': _str(), 'hsn_code': _str('Numeric HSN/SAC code'), 'batch_no': _str(),
                'unit': _str(), 'qty': {'type': 'number'},
                'buy_price': {'type': 'number', 'description': 'Rate per unit excluding GST'},
                'gst_pct': {'type': 'number'},
            },
            'required': ['product', 'hsn_code', 'batch_no', 'unit', 'qty', 'buy_price', 'gst_pct'],
            'additionalProperties': False,
        }},
        'cgst_total': {'type': 'number'}, 'sgst_total': {'type': 'number'},
        'igst_total': {'type': 'number'}, 'grand_total': {'type': 'number'},
    },
    'required': ['supplier_name', 'supplier_gstin', 'supplier_address', 'supplier_phone', 'supplier_email',
                 'invoice_no', 'irn_no', 'eway_bill_no', 'buyer_gstin', 'bill_date', 'items',
                 'cgst_total', 'sgst_total', 'igst_total', 'grand_total'],
    'additionalProperties': False,
}

BILL_PROMPT = (
    "Extract every field from this Indian purchase / tax invoice.\n"
    "- List every line item separately.\n"
    "- hsn_code is the numeric code from the HSN/SAC column.\n"
    "- buy_price is the rate per unit excluding GST.\n"
    "- If GST is split as CGST + SGST, gst_pct is the combined rate (CGST % x 2).\n"
    "- bill_date uses YYYY-MM-DD. Use an empty string or 0 for anything not printed on the bill.\n"
    "Return only JSON with exactly these keys: supplier_name, supplier_gstin, supplier_address, "
    "supplier_phone, supplier_email, invoice_no, irn_no, eway_bill_no, buyer_gstin, bill_date, "
    "items (product, hsn_code, batch_no, unit, qty, buy_price, gst_pct), cgst_total, sgst_total, "
    "igst_total, grand_total."
)


def _parse_json_reply(text):
    import json as jsonlib
    clean = (text or '').strip()
    start, end = clean.find('{'), clean.rfind('}')
    if start == -1 or end == -1:
        raise AIError('Could not read the bill. Try a clearer photo.')
    try:
        return jsonlib.loads(clean[start:end + 1])
    except ValueError:
        raise AIError('Could not read the bill. Try a clearer photo.')


@app.route('/api/ai/status', methods=['GET'])
@login_required
def ai_status():
    shop = query("SELECT * FROM shops WHERE user_id=?", (get_uid(),), one=True) or {}
    provider, _ = pick_ai_provider(shop)
    return jsonify({'success': True, 'provider': provider, 'preferred': shop.get('ai_provider') or 'anthropic',
                    'configured': [p for p in AI_PROVIDERS if shop.get(p + '_key')]})


@app.route('/api/scan-bill', methods=['POST'])
@login_required
def scan_bill():
    data      = request.get_json(silent=True) or {}
    image_b64 = data.get('image_b64', '')
    mime_type = data.get('mime_type') or 'image/jpeg'
    filename  = data.get('filename') or ''
    if not image_b64:
        return jsonify({"success": False, "error": "No bill file or photo provided"}), 400

    if ',' in image_b64:
        image_b64 = image_b64.split(',', 1)[1]

    # Detect if PDF
    is_pdf = (mime_type == 'application/pdf' or
              filename.lower().endswith('.pdf') or
              image_b64.startswith('JVBERi'))

    extracted_text = ""
    attachment = None

    if is_pdf:
        try:
            extracted_text, rendered_jpeg = process_pdf_document(image_b64)
            if rendered_jpeg:
                attachment = ('image/jpeg', rendered_jpeg)
            else:
                attachment = ('application/pdf', image_b64)
        except Exception as e:
            app.logger.warning(f"PDF pre-processing fallback: {e}")
            attachment = ('application/pdf', image_b64)
    else:
        if mime_type not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
            return jsonify({"success": False, "error": "Upload a JPG, PNG, WEBP or PDF bill"}), 400
        attachment = (mime_type, image_b64)

    shop = query("SELECT * FROM shops WHERE user_id=?", (get_uid(),), one=True) or {}
    primary_p, primary_k = pick_ai_provider(shop)

    # Collect candidate providers in priority order
    candidates = []
    if primary_p and primary_k:
        candidates.append((primary_p, primary_k))
    for p in ('openai', 'gemini'):
        if p != primary_p:
            k = shop.get(f'{p}_key') or os.environ.get(f'{p.upper()}_API_KEY')
            if k:
                candidates.append((p, k))

    if not candidates:
        return jsonify({"success": False, "error": "AI Scanner is not configured. Please contact administrator."}), 400

    prompt = BILL_PROMPT
    if extracted_text:
        prompt += f"\n\n--- INVOICE TEXT EXTRACTED DIRECTLY FROM PDF ---\n{extracted_text[:10000]}"

    last_error = None
    for provider, key in candidates:
        try:
            reply = ai_complete(provider, key, None, [{'role': 'user', 'content': prompt}],
                                attachment=attachment, json_schema=BILL_SCHEMA)
            return jsonify({"success": True, "data": _parse_json_reply(reply), "provider": provider, "was_pdf": is_pdf})
        except Exception as e:
            last_error = e
            app.logger.warning(f"AI Provider {provider} scan failed: {e}. Trying fallback if available.")
            continue

    return jsonify({"success": False, "error": str(last_error) if last_error else "Bill scan failed. Please try again."}), 502


@app.route('/api/ai/query', methods=['POST'])
@login_required
def ai_bi_query():
    """
    Direct deterministic business intelligence query endpoint over real database records.
    Works 100% reliably even without an external LLM API key!
    """
    uid = get_uid()
    data = request.get_json(silent=True) or {}
    q = (data.get('query') or data.get('message') or '').strip()
    if not q:
        return jsonify({'success': False, 'error': 'Query cannot be empty'}), 400

    from bi_agent import get_bi_grounded_response
    db = get_db()
    try:
        reply = get_bi_grounded_response(db, uid, q)
        return jsonify({'success': True, 'reply': reply, 'answer': reply, 'source': 'BillZen Business Intelligence Engine'})
    finally:
        db.close()


@app.route('/api/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    """
    Data-Grounded Business Intelligence Assistant.
    Never hallucinates figures. Uses actual BillZen database records.
    """
    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])
    if not isinstance(messages, list) or not messages or len(messages) > 22:
        return jsonify({'success': False, 'error': 'Invalid chat request'}), 400
    if any(not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant')
           or not isinstance(m.get('content'), str) or len(m['content']) > 12000 for m in messages):
        return jsonify({'success': False, 'error': 'Invalid chat message'}), 400

    user_query = messages[-1]['content'].strip()
    uid = get_uid()
    db = get_db()
    from bi_agent import get_bi_grounded_response
    try:
        grounded_data = get_bi_grounded_response(db, uid, user_query)
    finally:
        db.close()

    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    provider, key = pick_ai_provider(shop)
    
    # If no external provider configured, respond directly with verified ground truth!
    if not provider:
        return jsonify({'success': True, 'reply': grounded_data, 'provider': 'BillZen BI Engine'})

    system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
    system += f"\n\n[VERIFIED BILLZEN REAL DATABASE RECORDS - MANDATORY GROUND TRUTH - NEVER INVENT NUMBERS]:\n{grounded_data}\n\nPresent the verified figures politely, professionally, and accurately as BillZen AI Executive Assistant."
    convo = [{'role': m['role'], 'content': m['content']} for m in messages if m['role'] != 'system']

    try:
        reply = ai_complete(provider, key, system, convo, effort='low')
        if not reply:
            reply = grounded_data
    except Exception:
        # Fallback cleanly to verified ground-truth response
        reply = grounded_data

    return jsonify({'success': True, 'reply': reply, 'provider': provider})

# ─────────────────────────────────────────────────────────────
#  UNITS
# ─────────────────────────────────────────────────────────────
@app.route('/api/units', methods=['GET'])
@login_required
def get_units():
    data = rows("SELECT * FROM units WHERE user_id=? ORDER BY name", (get_uid(),))
    return jsonify({"success": True, "units": data})

@app.route('/api/units', methods=['POST'])
@login_required
def add_unit():
    d = request.get_json(silent=True) or {}
    name = d.get('name','').strip()
    if not name:
        return jsonify({"success": False, "error": "Name required"}), 400
    db = get_db()
    db.execute("INSERT INTO units (user_id,name) VALUES (?,?)", (get_uid(), name))
    db.commit()
    return jsonify({"success": True})

@app.route('/api/units/<int:uid>', methods=['DELETE'])
@login_required
def delete_unit(uid):
    db = get_db()
    db.execute("DELETE FROM units WHERE id=? AND user_id=?", (uid, get_uid()))
    db.commit()
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────
#  PRODUCTS
# ─────────────────────────────────────────────────────────────
@app.route('/api/products', methods=['GET'])
@login_required
def get_products():
    data = rows("SELECT * FROM products WHERE user_id=? ORDER BY name", (get_uid(),))
    return jsonify({"success": True, "products": data})

@app.route('/api/products', methods=['POST'])
@login_required
def add_product():
    d = request.get_json(silent=True) or {}
    name = d.get('name','').strip()
    if not name:
        return jsonify({"success": False, "error": "Name required"}), 400
    db = get_db()
    db.execute("""INSERT INTO products (user_id,name,hsn_code,category,unit,gst_pct,buy_price,sell_price,stock,low_stock,barcode)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (get_uid(), name, d.get('hsn_code',''), d.get('category','General'),
                d.get('unit','Pcs'), float(d.get('gst_pct',18)),
                float(d.get('buy_price',0)), float(d.get('sell_price',0)),
                float(d.get('stock',0)), float(d.get('low_stock',5)), d.get('barcode','')))
    db.commit()
    return jsonify({"success": True}), 201

@app.route('/api/products/<int:pid>', methods=['PUT'])
@login_required
def update_product(pid):
    d = request.get_json(silent=True) or {}
    db = get_db()
    db.execute("""UPDATE products SET name=?,hsn_code=?,category=?,unit=?,gst_pct=?,
                  buy_price=?,sell_price=?,stock=?,low_stock=?,barcode=? WHERE id=? AND user_id=?""",
               (d.get('name'), d.get('hsn_code',''), d.get('category','General'),
                d.get('unit','Pcs'), float(d.get('gst_pct',18)),
                float(d.get('buy_price',0)), float(d.get('sell_price',0)),
                float(d.get('stock',0)), float(d.get('low_stock',5)),
                d.get('barcode',''), pid, get_uid()))
    db.commit()
    return jsonify({"success": True})

@app.route('/api/products/<int:pid>', methods=['DELETE'])
@login_required
def delete_product(pid):
    db = get_db()
    db.execute("DELETE FROM products WHERE id=? AND user_id=?", (pid, get_uid()))
    db.commit()
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────
#  CUSTOMERS
# ─────────────────────────────────────────────────────────────
@app.route('/api/customers', methods=['GET'])
@login_required
def get_customers():
    data = rows("SELECT * FROM customers WHERE user_id=? ORDER BY name", (get_uid(),))
    return jsonify({"success": True, "customers": data})

@app.route('/api/customers', methods=['POST'])
@login_required
def add_customer():
    d = request.get_json(silent=True) or {}
    db = get_db()
    db.execute("INSERT INTO customers (user_id,name,phone,email,gstin,address) VALUES (?,?,?,?,?,?)",
               (get_uid(), d.get('name',''), d.get('phone',''), d.get('email',''), d.get('gstin',''), d.get('address','')))
    db.commit()
    return jsonify({"success": True}), 201

@app.route('/api/customers/<int:cid>', methods=['PUT'])
@login_required
def update_customer(cid):
    uid = get_uid()
    d = request.get_json(silent=True) or {}
    cust = query("SELECT id FROM customers WHERE id=? AND user_id=?", (cid, uid), one=True)
    if not cust:
        return jsonify({"success": False, "error": "Customer not found"}), 404
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({"success": False, "error": "Customer name is required"}), 400
    db = get_db()
    gstin = (d.get('gstin') or '').strip().upper()
    state_code = (d.get('state_code') or '').strip()
    state_name = (d.get('state_name') or '').strip()
    pan_no = (d.get('pan_no') or '').strip().upper()
    if not pan_no and len(gstin) == 15:
        pan_no = gstin[2:12]
    if not state_code and len(gstin) >= 2:
        state_code = gstin[:2]
    db.execute("""UPDATE customers SET name=?, phone=?, email=?, gstin=?, address=?,
                  state_code=?, state_name=?, pan_no=? WHERE id=? AND user_id=?""",
               (name, d.get('phone',''), d.get('email',''), gstin, d.get('address',''),
                state_code, state_name, pan_no, cid, uid))
    db.commit()
    return jsonify({"success": True})

@app.route('/api/customers/<int:cid>', methods=['DELETE'])
@login_required
def delete_customer(cid):
    uid = get_uid()
    db  = get_db()
    cust = query("SELECT id FROM customers WHERE id=? AND user_id=?", (cid, uid), one=True)
    if not cust:
        return jsonify({"success": False, "error": "Not found"}), 404
    db.execute("DELETE FROM customers WHERE id=? AND user_id=?", (cid, uid))
    db.commit()
    return jsonify({"success": True})

@app.route('/api/customers/<int:cid>/ledger')
@login_required
def customer_ledger(cid):
    uid  = get_uid()
    cust = query("SELECT * FROM customers WHERE id=? AND user_id=?", (cid, uid), one=True)
    if not cust:
        return jsonify({"success": False, "error": "Customer not found"}), 404
    sale_list = rows(
        "SELECT * FROM sales WHERE user_id=? AND customer_name=? AND cancelled=0 ORDER BY sale_date DESC",
        (uid, cust['name'])
    )
    for s in sale_list:
        s['items'] = rows("SELECT product,qty,price FROM sale_items WHERE sale_id=?", (s['id'],))
    total_billed = sum(s['total_amount'] for s in sale_list)
    total_paid   = sum(s['paid_amount']  for s in sale_list)
    return jsonify({
        "success":      True,
        "customer":     dict(cust),
        "sales":        sale_list,
        "total_billed": round(total_billed, 2),
        "total_paid":   round(total_paid,   2),
        "balance":      round(total_billed - total_paid, 2)
    })

# ─────────────────────────────────────────────────────────────
#  PURCHASE - Single purchase detail
# ─────────────────────────────────────────────────────────────
@app.route('/api/purchases/<int:pid>', methods=['GET'])
@login_required
def get_purchase_detail(pid):
    uid = get_uid()
    p = query("SELECT * FROM purchases WHERE id=? AND user_id=?", (pid, uid), one=True)
    if not p:
        return jsonify({"success": False, "error": "Not found"}), 404
    p['items'] = rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))
    return jsonify({"success": True, "purchase": p})

# ─────────────────────────────────────────────────────────────
#  SALES - Mark single bill as paid
# ─────────────────────────────────────────────────────────────
@app.route('/api/sales/<bill_id>/mark-paid', methods=['POST'])
@login_required
def mark_sale_paid(bill_id):
    uid = get_uid()
    db  = get_db()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=?", (bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "Bill not found"}), 404
    db.execute("UPDATE sales SET paid_amount=total_amount, payment_status='Paid' WHERE bill_id=? AND user_id=?", (bill_id, uid))
    db.commit()
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────
#  CUSTOMERS - Record partial payment
# ─────────────────────────────────────────────────────────────
@app.route('/api/customers/<int:cid>/payment', methods=['POST'])
@login_required
def record_customer_payment(cid):
    uid  = get_uid()
    d    = request.get_json(silent=True) or {}
    amt  = float(d.get('amount', 0))
    if amt <= 0:
        return jsonify({"success": False, "error": "Invalid amount"}), 400
    cust = query("SELECT * FROM customers WHERE id=? AND user_id=?", (cid, uid), one=True)
    if not cust:
        return jsonify({"success": False, "error": "Customer not found"}), 404
    db   = get_db()
    # Distribute payment across pending bills oldest first
    pending_bills = rows(
        "SELECT * FROM sales WHERE user_id=? AND customer_name=? AND cancelled=0 AND payment_status!='Paid' ORDER BY sale_date ASC",
        (uid, cust['name'])
    )
    remaining = amt
    for bill in pending_bills:
        if remaining <= 0:
            break
        due = round(bill['total_amount'] - bill['paid_amount'], 2)
        pay = min(remaining, due)
        new_paid = round(bill['paid_amount'] + pay, 2)
        status = 'Paid' if new_paid >= bill['total_amount'] else 'Partial'
        db.execute("UPDATE sales SET paid_amount=?, payment_status=? WHERE id=?", (new_paid, status, bill['id']))
        db.execute("""INSERT INTO customer_payments (user_id,customer_id,bill_id,amount,mode,note)
                      VALUES (?,?,?,?,?,?)""",
                   (uid, cid, bill['bill_id'], pay, d.get('mode', 'Cash'), d.get('note', '')))
        remaining = round(remaining - pay, 2)
    db.commit()
    return jsonify({"success": True, "applied": round(amt - remaining, 2)})

# ─────────────────────────────────────────────────────────────
#  SUPPLIERS - Get all purchases from a supplier
# ─────────────────────────────────────────────────────────────
@app.route('/api/suppliers/<int:sid>/purchases', methods=['GET'])
@login_required
def supplier_purchases(sid):
    uid = get_uid()
    sup = query("SELECT * FROM suppliers WHERE id=? AND user_id=?", (sid, uid), one=True)
    if not sup:
        return jsonify({"success": False, "error": "Supplier not found"}), 404
    purch = rows(
        "SELECT * FROM purchases WHERE user_id=? AND supplier_name=? ORDER BY created_at DESC",
        (uid, sup['name'])
    )
    total = sum(p['total_amount'] for p in purch)
    return jsonify({
        "success": True,
        "supplier": dict(sup),
        "purchases": purch,
        "bill_count": len(purch),
        "total_amount": round(total, 2)
    })

# ─────────────────────────────────────────────────────────────
#  SUPPLIERS
# ─────────────────────────────────────────────────────────────
@app.route('/api/suppliers/<int:sid>', methods=['DELETE'])
@login_required
def delete_supplier(sid):
    uid = get_uid()
    db  = get_db()
    sup = query("SELECT id FROM suppliers WHERE id=? AND user_id=?", (sid, uid), one=True)
    if not sup:
        return jsonify({"success": False, "error": "Not found"}), 404
    db.execute("DELETE FROM suppliers WHERE id=? AND user_id=?", (sid, uid))
    db.commit()
    return jsonify({"success": True})

@app.route('/api/suppliers', methods=['GET'])
@login_required
def get_suppliers():
    data = rows("SELECT * FROM suppliers WHERE user_id=? ORDER BY name", (get_uid(),))
    return jsonify({"success": True, "suppliers": data})

@app.route('/api/suppliers/<int:sid>', methods=['PUT'])
@login_required
def update_supplier(sid):
    uid = get_uid()
    d   = request.get_json(silent=True) or {}
    sup = query("SELECT id FROM suppliers WHERE id=? AND user_id=?", (sid, uid), one=True)
    if not sup:
        return jsonify({"success": False, "error": "Not found"}), 404
    db = get_db()
    db.execute(
        "UPDATE suppliers SET name=?,phone=?,email=?,gstin=?,address=? WHERE id=? AND user_id=?",
        (d.get('name',''), d.get('phone',''), d.get('email',''),
         d.get('gstin',''), d.get('address',''), sid, uid)
    )
    db.commit()
    return jsonify({"success": True})

@app.route('/api/suppliers', methods=['POST'])
@login_required
def add_supplier():
    d = request.get_json(silent=True) or {}
    db = get_db()
    db.execute("INSERT INTO suppliers (user_id,name,phone,email,gstin,address) VALUES (?,?,?,?,?,?)",
               (get_uid(), d.get('name',''), d.get('phone',''), d.get('email',''), d.get('gstin',''), d.get('address','')))
    db.commit()
    return jsonify({"success": True}), 201

# ─────────────────────────────────────────────────────────────
#  SALES
# ─────────────────────────────────────────────────────────────
@app.route('/api/sales', methods=['GET'])
@login_required
def get_sales():
    cat = request.args.get('category', '').lower().strip()
    uid = get_uid()
    if cat in ('pakka', 'kacha'):
        data = rows("SELECT * FROM sales WHERE user_id=? AND bill_category=? ORDER BY created_at DESC", (uid, cat))
    else:
        data = rows("SELECT * FROM sales WHERE user_id=? ORDER BY created_at DESC", (uid,))
    for s in data:
        s['items'] = rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))
    return jsonify({"success": True, "sales": data})

@app.route('/api/sales', methods=['POST'])
@login_required
def add_sale():
    uid = get_uid()
    plan_info = get_user_plan_info(uid)
    if plan_info.get('status') == 'expired' and not plan_info.get('is_master'):
        return jsonify({
            "success": False,
            "error": "Aapka 7-Day Free Trial poora ho gaya hai. Agla bill generate karne ke liye subscription renew karein.",
            "trial_expired": True
        }), 403

    d = request.get_json(silent=True) or {}
    if not d.get('customer_name'):
        return jsonify({"success": False, "error": "Customer name required"}), 400
    items = d.get('items', [])
    if not items:
        return jsonify({"success": False, "error": "No items"}), 400

    db = get_db()
    uid = session.get('user_id', 1)

    bill_cat = (d.get('bill_category') or 'pakka').lower().strip()
    if bill_cat not in ('pakka', 'kacha'):
        bill_cat = 'pakka'

    split_payment = d.get('split_payment', '')
    if isinstance(split_payment, (dict, list)):
        import json as jsonlib
        split_payment = jsonlib.dumps(split_payment)

    subtotal = cgst_total = sgst_total = igst_total = 0
    line_items = []

    is_interstate = int(d.get('is_interstate', 0))
    state_code = (d.get('state_code') or '').strip()
    state_name = (d.get('state_name') or d.get('customer_state') or '').strip()
    cgstin = (d.get('customer_gstin') or '').strip().upper()
    pan_no = (d.get('pan_no') or d.get('customer_pan') or '').strip().upper()
    khata_pen_no = (d.get('khata_pen_no') or d.get('khata_pen_number') or '').strip()
    if not pan_no and len(cgstin) == 15:
        pan_no = cgstin[2:12]
    if not state_code and len(cgstin) >= 2:
        state_code = cgstin[:2]
    vehicle_no = (d.get('vehicle_no') or '').strip()
    transporter_id = (d.get('transporter_id') or '').strip()
    billing_address = (d.get('billing_address') or d.get('address') or '').strip()
    shipping_address = (d.get('shipping_address') or billing_address).strip()

    for item in items:
        name  = (item.get('product') or '').strip()
        qty   = float(item.get('qty', 0))
        price = float(item.get('price', 0))
        gst   = float(item.get('gst_pct', 0))
        if is_interstate:
            igst = float(item.get('igst_pct', gst))
            cgst = sgst = 0
        else:
            igst = 0
            cgst = float(item.get('cgst_pct', gst/2))
            sgst = float(item.get('sgst_pct', gst/2))
        if not name or qty <= 0: continue
        amt = round(qty * price, 2)
        subtotal += amt
        cgst_total += round(amt * cgst / 100, 2)
        sgst_total += round(amt * sgst / 100, 2)
        igst_total += round(amt * igst / 100, 2)
        line_items.append({"product": name, "hsn_code": item.get('hsn_code',''),
                           "unit": item.get('unit','Pcs'), "qty": qty, "price": price,
                           "gst_pct": gst, "cgst_pct": cgst, "sgst_pct": sgst,
                           "igst_pct": igst, "amount": amt})
        # Update stock
        prod = db.execute("SELECT id FROM products WHERE name=? AND user_id=?", (name, uid)).fetchone()
        if prod:
            db.execute("UPDATE products SET stock=MAX(0,stock-?) WHERE id=?", (qty, prod['id']))
        else:
            db.execute("INSERT INTO products (user_id,name,stock) VALUES (?,?,0)", (uid, name))

    discount = float(d.get('discount', 0))
    total    = round(subtotal + cgst_total + sgst_total + igst_total - discount, 2)
    paid     = min(float(d.get('paid_amount', 0)), total)
    status   = 'Paid' if paid >= total else ('Partial' if paid > 0 else 'Pending')
    bill_id  = gen_id('EST' if bill_cat == 'kacha' else 'INV')
    operator = session.get('name') or session.get('username') or 'Owner'

    cur = db.execute("""INSERT INTO sales (user_id,bill_id,bill_type,customer_name,customer_phone,customer_gstin,
                        subtotal,cgst_amount,sgst_amount,igst_amount,discount,total_amount,
                        paid_amount,payment_status,payment_mode,sale_date,notes,bill_category,split_payment,created_by,
                        is_interstate,state_code,state_name,pan_no,khata_pen_no,vehicle_no,transporter_id,billing_address,shipping_address)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (uid, bill_id, d.get('bill_type','retail'), d['customer_name'],
                      d.get('customer_phone',''), cgstin,
                      subtotal, cgst_total, sgst_total, igst_total, discount, total,
                      paid, status, d.get('payment_mode','Cash'),
                      d.get('sale_date', str(date.today())), d.get('notes',''),
                      bill_cat, str(split_payment), operator,
                      is_interstate, state_code, state_name, pan_no, khata_pen_no, vehicle_no, transporter_id,
                      billing_address, shipping_address))
    sale_id = cur.lastrowid

    for li in line_items:
        db.execute("""INSERT INTO sale_items (sale_id,product,hsn_code,unit,qty,price,gst_pct,cgst_pct,sgst_pct,igst_pct,amount)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (sale_id, li['product'], li['hsn_code'], li['unit'], li['qty'],
                    li['price'], li['gst_pct'], li['cgst_pct'], li['sgst_pct'], li['igst_pct'], li['amount']))

    # Auto-save customer if not already saved
    cname  = d['customer_name'].strip()
    cphone = d.get('customer_phone','').strip()
    if cname:
        existing_cust = db.execute(
            "SELECT id FROM customers WHERE user_id=? AND (name=? OR (phone!='' AND phone=?))",
            (uid, cname, cphone if cphone else '__NO_MATCH__')
        ).fetchone()
        if not existing_cust:
            db.execute(
                "INSERT INTO customers (user_id,name,phone,email,gstin,address,state_code,state_name,pan_no) VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, cname, cphone, '', cgstin, billing_address, state_code, state_name, pan_no)
            )
        else:
            db.execute(
                "UPDATE customers SET gstin=CASE WHEN gstin='' THEN ? ELSE gstin END, state_code=CASE WHEN state_code='' THEN ? ELSE state_code END, state_name=CASE WHEN state_name='' THEN ? ELSE state_name END, pan_no=CASE WHEN pan_no='' THEN ? ELSE pan_no END, address=CASE WHEN address='' THEN ? ELSE address END WHERE id=?",
                (cgstin, state_code, state_name, pan_no, billing_address, existing_cust['id'])
            )

    db.commit()
    return jsonify({"success": True, "bill_id": bill_id, "bill_category": bill_cat, "total_amount": total, "total": total, "status": status}), 201

@app.route('/api/sales/<bill_id>/convert-to-pakka', methods=['POST'])
@login_required
def convert_sale_to_pakka(bill_id):
    uid = get_uid()
    db = get_db()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=?", (bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "Bill not found"}), 404
    if sale.get('bill_category') == 'pakka':
        return jsonify({"success": False, "error": "Bill is already a Pakka (Tax) Invoice"}), 400
    if sale.get('cancelled'):
        return jsonify({"success": False, "error": "Cancelled bills cannot be converted"}), 400

    new_bill_id = gen_id('INV')
    old_id = sale['id']

    items = rows("SELECT * FROM sale_items WHERE sale_id=?", (old_id,))
    subtotal = cgst_total = sgst_total = igst_total = 0
    for it in items:
        amt = it['amount']
        subtotal += amt
        gst = it.get('gst_pct') or 18
        cgst_pct = it.get('cgst_pct') or (gst / 2)
        sgst_pct = it.get('sgst_pct') or (gst / 2)
        cgst_total += round(amt * cgst_pct / 100, 2)
        sgst_total += round(amt * sgst_pct / 100, 2)
        db.execute("UPDATE sale_items SET gst_pct=?, cgst_pct=?, sgst_pct=? WHERE id=?",
                   (gst, cgst_pct, sgst_pct, it['id']))

    subtotal = round(subtotal, 2)
    cgst_total = round(cgst_total, 2)
    sgst_total = round(sgst_total, 2)
    discount = float(sale.get('discount') or 0)
    total = round(subtotal + cgst_total + sgst_total - discount, 2)

    db.execute("""UPDATE sales SET bill_id=?, bill_category='pakka', subtotal=?, cgst_amount=?, sgst_amount=?,
                  total_amount=?, converted_from=? WHERE id=?""",
               (new_bill_id, subtotal, cgst_total, sgst_total, total, bill_id, old_id))
    db.commit()
    return jsonify({
        "success": True,
        "old_bill_id": bill_id,
        "new_bill_id": new_bill_id,
        "total_amount": total,
        "message": f"Successfully converted Estimate {bill_id} to Tax Invoice {new_bill_id}"
    })

# ─────────────────────────────────────────────────────────────
#  PURCHASES
# ─────────────────────────────────────────────────────────────
@app.route('/api/purchases', methods=['GET'])
@login_required
def get_purchases():
    data = rows("SELECT * FROM purchases WHERE user_id=? ORDER BY created_at DESC", (get_uid(),))
    for p in data:
        p['items'] = rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))
    return jsonify({"success": True, "purchases": data})

@app.route('/api/purchases', methods=['POST'])
@login_required
def add_purchase():
    d = request.get_json(silent=True) or {}
    items = d.get('items', [])
    if not items:
        return jsonify({"success": False, "error": "No items"}), 400

    db  = get_db()
    uid = get_uid()

    subtotal = cgst_total = sgst_total = igst_total = 0
    purchase_id = gen_id('P')

    cur = db.execute("""INSERT INTO purchases (user_id,purchase_id,supplier_name,supplier_gstin,
                        invoice_no,irn_no,eway_bill_no,buyer_gstin,bill_date,notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (uid, purchase_id, d.get('supplier_name',''), d.get('supplier_gstin',''),
                      d.get('invoice_no',''), d.get('irn_no',''), d.get('eway_bill_no',''),
                      d.get('buyer_gstin',''), d.get('bill_date', str(date.today())), d.get('notes','')))
    pid = cur.lastrowid

    shop = query("SELECT markup_pct FROM shops WHERE user_id=?", (uid,), one=True)
    try:
        markup = float(shop['markup_pct']) if (shop and shop.get('markup_pct') not in (None, '')) else 30.0
    except (ValueError, TypeError):
        markup = 30.0

    shop_rec = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    shop_code = (shop_rec.get('gstin') or '')[:2] or '07'
    party_code = (d.get('supplier_gstin') or '')[:2]
    from tax_engine import is_interstate_supply
    is_inter = is_interstate_supply(shop_code, party_code) if party_code else bool(d.get('is_interstate', 0))

    for item in items:
        name      = (item.get('product') or '').strip()
        qty       = float(item.get('qty', 0))
        buy_price = float(item.get('buy_price', 0))
        if not name or qty <= 0 or buy_price < 0:
            db.rollback()
            return jsonify({"success": False, "error": "Each purchase item needs a product, positive quantity, and non-negative price"}), 400
        gst       = float(item.get('gst_pct', 0))
        if is_inter:
            igst = float(item.get('igst_pct', gst))
            cgst = 0.0
            sgst = 0.0
        else:
            igst = float(item.get('igst_pct', 0))
            cgst = float(item.get('cgst_pct', gst/2))
            sgst = float(item.get('sgst_pct', gst/2))
        taxable   = round(qty * buy_price, 2)
        cgst_amt  = round(taxable * cgst / 100, 2)
        sgst_amt  = round(taxable * sgst / 100, 2)
        igst_amt  = round(taxable * igst / 100, 2)
        total_amt = round(taxable + cgst_amt + sgst_amt + igst_amt, 2)

        subtotal   += taxable
        cgst_total += cgst_amt
        sgst_total += sgst_amt
        igst_total += igst_amt

        db.execute("""INSERT INTO purchase_items (purchase_id,product,hsn_code,batch_no,unit,qty,buy_price,
                      gst_pct,cgst_pct,sgst_pct,igst_pct,taxable_val,cgst_amt,sgst_amt,igst_amt,amount)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (pid, name, item.get('hsn_code',''), item.get('batch_no',''),
                    item.get('unit','Pcs'), qty, buy_price, gst, cgst, sgst, igst,
                    taxable, cgst_amt, sgst_amt, igst_amt, total_amt))

        # Update inventory
        prod = db.execute("SELECT id FROM products WHERE name=? AND user_id=?", (name, uid)).fetchone()
        sell_price = round(buy_price * (1 + markup/100), 2)
        if prod:
            db.execute("UPDATE products SET stock=stock+?, buy_price=?, sell_price=?, hsn_code=CASE WHEN hsn_code='' THEN ? ELSE hsn_code END WHERE id=?",
                       (qty, buy_price, sell_price, item.get('hsn_code',''), prod['id']))
        else:
            db.execute("""INSERT INTO products (user_id,name,hsn_code,unit,gst_pct,buy_price,sell_price,stock)
                          VALUES (?,?,?,?,?,?,?,?)""",
                       (uid, name, item.get('hsn_code',''), item.get('unit','Pcs'),
                        gst, buy_price, sell_price, qty))

    total = round(subtotal + cgst_total + sgst_total + igst_total, 2)
    party_code = (d.get('supplier_gstin') or '')[:2] or d.get('state_code', '')
    db.execute("UPDATE purchases SET is_interstate=?,state_code=?,subtotal=?,cgst_amount=?,sgst_amount=?,igst_amount=?,total_amount=? WHERE id=?",
               (1 if is_inter else 0, party_code, subtotal, cgst_total, sgst_total, igst_total, total, pid))

    # Auto-save supplier if not already saved
    sname  = d.get('supplier_name','').strip()
    sgstin = d.get('supplier_gstin','').strip()
    if sname:
        existing_sup = db.execute(
            "SELECT id FROM suppliers WHERE user_id=? AND (name=? OR (gstin!='' AND gstin=?))",
            (uid, sname, sgstin if sgstin else '__NO_MATCH__')
        ).fetchone()
        if not existing_sup:
            db.execute(
                "INSERT INTO suppliers (user_id,name,phone,email,gstin,address) VALUES (?,?,?,?,?,?)",
                (uid, sname, '', '', sgstin, '')
            )

    db.commit()
    return jsonify({"success": True, "purchase_id": purchase_id, "id": pid, "total": total}), 201

@app.route('/api/purchases/<int:pid>', methods=['PUT'])
@login_required
def update_purchase(pid):
    uid = get_uid()
    db = get_db()
    pur = query("SELECT * FROM purchases WHERE id=? AND user_id=?", (pid, uid), one=True)
    if not pur:
        return jsonify({"success": False, "error": "Purchase not found"}), 404

    d = request.get_json(silent=True) or {}
    items = d.get('items', [])
    if not items:
        return jsonify({"success": False, "error": "No items provided"}), 400

    is_interstate = int(d.get('is_interstate', pur['is_interstate'] if 'is_interstate' in pur.keys() else 0))
    party_code = (d.get('supplier_gstin') or '')[:2] or d.get('state_code', pur.get('state_code', ''))
    subtotal = cgst_total = sgst_total = igst_total = 0
    line_items = []

    for item in items:
        name = (item.get('product') or '').strip()
        qty = float(item.get('qty', 0))
        buy_price = float(item.get('buy_price', item.get('price', 0)))
        if not name or qty <= 0 or buy_price < 0:
            return jsonify({"success": False, "error": "Invalid product or quantity"}), 400
        gst = float(item.get('gst_pct', 0))
        if is_interstate:
            igst = gst
            cgst = sgst = 0
        else:
            igst = 0
            cgst = gst / 2
            sgst = gst / 2
        taxable = round(qty * buy_price, 2)
        cgst_amt = round(taxable * cgst / 100, 2)
        sgst_amt = round(taxable * sgst / 100, 2)
        igst_amt = round(taxable * igst / 100, 2)
        total_amt = round(taxable + cgst_amt + sgst_amt + igst_amt, 2)

        subtotal += taxable
        cgst_total += cgst_amt
        sgst_total += sgst_amt
        igst_total += igst_amt
        line_items.append({
            "product": name, "hsn_code": item.get('hsn_code', ''), "batch_no": item.get('batch_no', ''),
            "unit": item.get('unit', 'Pcs'), "qty": qty, "buy_price": buy_price,
            "gst_pct": gst, "cgst_pct": cgst, "sgst_pct": sgst, "igst_pct": igst,
            "taxable_val": taxable, "cgst_amt": cgst_amt, "sgst_amt": sgst_amt, "igst_amt": igst_amt,
            "amount": total_amt
        })

    # Restore old stock
    old_items = rows("SELECT product, qty FROM purchase_items WHERE purchase_id=?", (pid,))
    for oi in old_items:
        db.execute("UPDATE products SET stock=MAX(0, stock-?) WHERE user_id=? AND name=?", (oi['qty'], uid, oi['product']))

    db.execute("DELETE FROM purchase_items WHERE purchase_id=?", (pid,))

    for li in line_items:
        db.execute("""INSERT INTO purchase_items (purchase_id,product,hsn_code,batch_no,unit,qty,buy_price,
                      gst_pct,cgst_pct,sgst_pct,igst_pct,taxable_val,cgst_amt,sgst_amt,igst_amt,amount)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (pid, li['product'], li['hsn_code'], li['batch_no'], li['unit'], li['qty'], li['buy_price'],
                    li['gst_pct'], li['cgst_pct'], li['sgst_pct'], li['igst_pct'],
                    li['taxable_val'], li['cgst_amt'], li['sgst_amt'], li['igst_amt'], li['amount']))
        prod = db.execute("SELECT id FROM products WHERE name=? AND user_id=?", (li['product'], uid)).fetchone()
        if prod:
            db.execute("UPDATE products SET stock=stock+?, buy_price=? WHERE id=?", (li['qty'], li['buy_price'], prod['id']))
        else:
            db.execute("INSERT INTO products (user_id,name,hsn_code,unit,gst_pct,buy_price,sell_price,stock) VALUES (?,?,?,?,?,?,?,?)",
                       (uid, li['product'], li['hsn_code'], li['unit'], li['gst_pct'], li['buy_price'], round(li['buy_price']*1.3, 2), li['qty']))

    total = round(subtotal + cgst_total + sgst_total + igst_total, 2)
    db.execute("""UPDATE purchases SET supplier_name=?, supplier_gstin=?, invoice_no=?,
                  bill_date=?, notes=?, is_interstate=?, state_code=?, subtotal=?, cgst_amount=?, sgst_amount=?,
                  igst_amount=?, total_amount=? WHERE id=? AND user_id=?""",
               (d.get('supplier_name', pur['supplier_name']), d.get('supplier_gstin', pur['supplier_gstin']),
                d.get('invoice_no', pur['invoice_no']), d.get('bill_date', pur['bill_date']),
                d.get('notes', pur['notes']), is_interstate, party_code, subtotal, cgst_total, sgst_total, igst_total, total,
                pid, uid))
    db.commit()
    return jsonify({"success": True, "purchase_id": pur['purchase_id'], "total": total})

@app.route('/api/purchases/<int:pid>', methods=['DELETE'])
@login_required
def delete_purchase(pid):
    uid = get_uid()
    db = get_db()
    pur = query("SELECT * FROM purchases WHERE id=? AND user_id=?", (pid, uid), one=True)
    if not pur:
        return jsonify({"success": False, "error": "Purchase not found"}), 404

    # Prevent stock mismatch & orphaned debit notes:
    # Reject deletion if active Debit Notes exist for this purchase
    active_dn = query("""
        SELECT COUNT(*) as cnt FROM purchase_returns 
        WHERE user_id=? AND (original_purchase_id=? OR original_invoice_no=?)
    """, (uid, pid, pur.get('invoice_no') or pur.get('purchase_id')), one=True)
    if active_dn and active_dn['cnt'] > 0:
        return jsonify({
            "success": False,
            "error": "Cannot delete purchase with active Debit Notes / Purchase Returns. Please manage return items or adjust debit notes first."
        }), 400

    old_items = rows("SELECT product, qty FROM purchase_items WHERE purchase_id=?", (pid,))
    for oi in old_items:
        db.execute("UPDATE products SET stock=MAX(0, stock-?) WHERE user_id=? AND name=?", (oi['qty'], uid, oi['product']))
    db.execute("DELETE FROM purchase_items WHERE purchase_id=?", (pid,))
    db.execute("DELETE FROM purchases WHERE id=? AND user_id=?", (pid, uid))
    db.commit()
    return jsonify({"success": True, "message": "Purchase deleted and stock updated successfully."})

@app.route('/api/ai/suggest-hsn')
@login_required
def suggest_hsn():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({"success": False, "error": "Query required"}), 400
    try:
        from hsn_detector import detect_hsn_and_gst
        uid = get_uid()
        shop = query("SELECT gemini_key, openai_key, ai_provider FROM shops WHERE user_id=?", (uid,), one=True) or {}
        hsn, gst, source = detect_hsn_and_gst(q, shop.get('openai_key') or shop.get('gemini_key'), shop.get('ai_provider'))
        return jsonify({"success": True, "hsn": hsn, "gst": gst, "source": source, "query": q})
    except Exception as e:
        return jsonify({"success": False, "hsn": "", "gst": 18, "error": str(e)})

@app.route('/api/tally/export/v2', methods=['POST'])
@login_required
def tally_export_v2():
    uid = get_uid()
    d = request.get_json(silent=True) or {}
    vch_type = d.get('voucher_type', 'both')
    category = d.get('category', 'all')
    from_date = d.get('from_date') or ''
    to_date = d.get('to_date') or ''
    selected_sale_ids = d.get('selected_sales', [])
    selected_pur_ids = d.get('selected_purchases', [])
    ledger_map = d.get('ledger_map', None)

    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    company_name = shop.get('name') or 'My Shop'

    sales_list = []
    if vch_type in ('sales', 'both'):
        sql = "SELECT * FROM sales WHERE user_id=? AND cancelled=0"
        params = [uid]
        if from_date:
            sql += " AND sale_date >= ?"
            params.append(from_date)
        if to_date:
            sql += " AND sale_date <= ?"
            params.append(to_date)
        if not d.get('include_kacha'):
            sql += " AND bill_category != 'kacha'"
        sql += " ORDER BY sale_date ASC"
        raw_sales = rows(sql, params)
        for s in raw_sales:
            if selected_sale_ids and s['bill_id'] not in selected_sale_ids:
                continue
            s_dict = dict(s)
            s_dict['items'] = [dict(it) for it in rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))]
            sales_list.append(s_dict)

    purchases_list = []
    if vch_type in ('purchases', 'both'):
        sql = "SELECT * FROM purchases WHERE user_id=?"
        params = [uid]
        if from_date:
            sql += " AND bill_date >= ?"
            params.append(from_date)
        if to_date:
            sql += " AND bill_date <= ?"
            params.append(to_date)
        sql += " ORDER BY bill_date ASC"
        raw_pur = rows(sql, params)
        for p in raw_pur:
            if selected_pur_ids and (p['purchase_id'] not in selected_pur_ids and p['id'] not in selected_pur_ids):
                continue
            p_dict = dict(p)
            p_dict['items'] = [dict(it) for it in rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))]
            purchases_list.append(p_dict)

    cn_list = []
    raw_cn = rows("SELECT * FROM sales_returns WHERE user_id=? ORDER BY return_date ASC", (uid,))
    for cn in raw_cn:
        cn_dict = dict(cn)
        cn_dict['items'] = [dict(it) for it in rows("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))]
        cn_list.append(cn_dict)

    dn_list = []
    raw_dn = rows("SELECT * FROM purchase_returns WHERE user_id=? ORDER BY return_date ASC", (uid,))
    for dn in raw_dn:
        dn_dict = dict(dn)
        dn_dict['items'] = [dict(it) for it in rows("SELECT * FROM purchase_return_items WHERE return_id=?", (dn['id'],))]
        dn_list.append(dn_dict)

    try:
        from tally_engine import generate_tally_v2_xml
        res = generate_tally_v2_xml(company_name, sales_list, purchases_list, ledger_map, category, credit_notes_list=cn_list, debit_notes_list=dn_list)
        xml_data, s_cnt, p_cnt = res[0], res[1], res[2]
        
        if d.get('summary_only'):
            taxable = sum(float(s.get('subtotal', 0)) for s in sales_list) + sum(float(p.get('subtotal', 0)) for p in purchases_list)
            cgst = sum(float(s.get('cgst_amount', 0)) for s in sales_list) + sum(float(p.get('cgst_amount', 0)) for p in purchases_list)
            sgst = sum(float(s.get('sgst_amount', 0)) for s in sales_list) + sum(float(p.get('sgst_amount', 0)) for p in purchases_list)
            igst = sum(float(s.get('igst_amount', 0)) for s in sales_list) + sum(float(p.get('igst_amount', 0)) for p in purchases_list)
            total = sum(float(s.get('total_amount', 0)) for s in sales_list) + sum(float(p.get('total_amount', 0)) for p in purchases_list)
            return jsonify({
                "success": True,
                "sales_count": s_cnt,
                "purchases_count": p_cnt,
                "taxable_value": round(taxable, 2),
                "cgst": round(cgst, 2),
                "sgst": round(sgst, 2),
                "igst": round(igst, 2),
                "total": round(total, 2)
            })

        response = make_response(xml_data)
        filename = f"Tally_Export_{category}_{date.today().strftime('%Y%m%d')}.xml"
        response.headers['Content-Type'] = 'application/xml; charset=utf-8'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/tally/sync/live', methods=['POST'])
@login_required
def tally_sync_live():
    """
    OPTION 2: Live Tally Sync
    Pushes vouchers directly into TallyPrime via its native HTTP XML server (Port 9000).
    CRITICAL SECURITY: BillZen Cloud server NEVER makes arbitrary HTTP requests
    to user-supplied remote network addresses (SSRF prevention).
    """
    uid = get_uid()
    d = request.get_json(silent=True) or {}
    tally_host = (d.get('tally_host') or '127.0.0.1').strip()
    tally_port = int(d.get('tally_port') or 9000)

    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    company_name = shop.get('name') or 'My Shop'

    # STRICT SSRF GUARD: Prohibit arbitrary remote network calls
    if tally_host not in ('127.0.0.1', 'localhost'):
        return jsonify({
            "success": False,
            "error": "Direct server-to-remote connections are prohibited to prevent SSRF vulnerabilities. Use the BillZen Windows Desktop Connector (tally_connector.py) to sync with Tally on your PC.",
            "security_policy": "SSRF_PROTECTED",
            "connector_token": shop.get('tally_connector_token') or ""
        }), 400

    if tally_port != 9000:
        return jsonify({
            "success": False,
            "error": "Only standard TallyPrime port 9000 is permitted."
        }), 400

    tally_url = f"http://{tally_host}:{tally_port}"
    vch_type = d.get('voucher_type', 'both')
    category = d.get('category', 'all')
    from_date = d.get('from_date') or ''
    to_date = d.get('to_date') or ''
    selected_sale_ids = d.get('selected_sales', [])
    selected_pur_ids = d.get('selected_purchases', [])
    ledger_map = d.get('ledger_map', None)

    sales_list = []
    if vch_type in ('sales', 'both'):
        sql = "SELECT * FROM sales WHERE user_id=? AND cancelled=0"
        params = [uid]
        if from_date:
            sql += " AND sale_date >= ?"
            params.append(from_date)
        if to_date:
            sql += " AND sale_date <= ?"
            params.append(to_date)
        if not d.get('include_kacha'):
            sql += " AND bill_category != 'kacha'"
        sql += " ORDER BY sale_date ASC"
        raw_sales = rows(sql, params)
        for s in raw_sales:
            if selected_sale_ids and s['bill_id'] not in selected_sale_ids:
                continue
            s_dict = dict(s)
            s_dict['items'] = [dict(it) for it in rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))]
            sales_list.append(s_dict)

    purchases_list = []
    if vch_type in ('purchases', 'both'):
        sql = "SELECT * FROM purchases WHERE user_id=?"
        params = [uid]
        if from_date:
            sql += " AND bill_date >= ?"
            params.append(from_date)
        if to_date:
            sql += " AND bill_date <= ?"
            params.append(to_date)
        sql += " ORDER BY bill_date ASC"
        raw_pur = rows(sql, params)
        for p in raw_pur:
            if selected_pur_ids and (p['purchase_id'] not in selected_pur_ids and p['id'] not in selected_pur_ids):
                continue
            p_dict = dict(p)
            p_dict['items'] = [dict(it) for it in rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))]
            purchases_list.append(p_dict)

    cn_list = []
    raw_cn = rows("SELECT * FROM sales_returns WHERE user_id=? ORDER BY return_date ASC", (uid,))
    for cn in raw_cn:
        cn_dict = dict(cn)
        cn_dict['items'] = [dict(it) for it in rows("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))]
        cn_list.append(cn_dict)

    dn_list = []
    raw_dn = rows("SELECT * FROM purchase_returns WHERE user_id=? ORDER BY return_date ASC", (uid,))
    for dn in raw_dn:
        dn_dict = dict(dn)
        dn_dict['items'] = [dict(it) for it in rows("SELECT * FROM purchase_return_items WHERE return_id=?", (dn['id'],))]
        dn_list.append(dn_dict)

    try:
        from tally_engine import generate_tally_v2_xml
        res = generate_tally_v2_xml(company_name, sales_list, purchases_list, ledger_map, category, credit_notes_list=cn_list, debit_notes_list=dn_list)
        xml_data, s_cnt, p_cnt = res[0], res[1], res[2]
        
        import urllib.request, urllib.error, re
        req = urllib.request.Request(
            tally_url,
            data=xml_data.encode('utf-8'),
            headers={'Content-Type': 'application/xml; charset=utf-8'},
            method='POST'
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                tally_resp = resp.read().decode('utf-8', errors='ignore')
                created = re.findall(r'<CREATED>(\d+)</CREATED>', tally_resp)
                errors = re.findall(r'<ERRORS>(\d+)</ERRORS>', tally_resp)
                cr_count = sum(int(c) for c in created) if created else (s_cnt + p_cnt)
                err_count = sum(int(e) for e in errors) if errors else 0
                return jsonify({
                    "success": True,
                    "created": cr_count,
                    "errors": err_count,
                    "sales_count": s_cnt,
                    "purchases_count": p_cnt,
                    "message": f"Successfully synced {cr_count} vouchers directly into TallyPrime!",
                    "tally_response": tally_resp[:500]
                })
        except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError):
            return jsonify({
                "success": False,
                "offline": True,
                "tally_url": tally_url,
                "error": f"Tally server unreachable on local port 9000. Please ensure TallyPrime is open and port 9000 is enabled, or run the BillZen Desktop Connector.",
                "hint": "Option 1 (Download Tally XML) is also available and 100% works without any port setup!"
            }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── SECURE BILLZEN TALLY CONNECTOR API (Cloud <-> Windows Desktop Daemon) ──

@app.route('/api/tally/connector/token', methods=['GET', 'POST'])
@login_required
def tally_connector_token():
    """Generates or retrieves the business's secure desktop pairing token."""
    uid = get_uid()
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True)
    if not shop:
        db = get_db()
        db.execute("INSERT INTO shops (user_id, name) VALUES (?, ?)", (uid, "My Shop"))
        db.commit()
        shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    token = shop.get('tally_connector_token') or ''
    if not token or request.method == 'POST':
        token = 'bz_conn_' + secrets.token_hex(20)
        db = get_db()
        db.execute("UPDATE shops SET tally_connector_token=? WHERE user_id=?", (token, uid))
        db.commit()
    return jsonify({
        "success": True,
        "connector_token": token,
        "machine": shop.get('connector_machine') or "Not Paired",
        "last_seen": shop.get('connector_last_seen') or "Never"
    })

@app.route('/api/tally/connector/vouchers', methods=['GET'])
def tally_connector_vouchers():
    """
    Secure endpoint for the Windows Desktop Connector.
    Authenticates via X-Connector-Token header and returns vouchers strictly scoped to tenant.
    Zero cross-customer data leakage.
    """
    token = request.headers.get('X-Connector-Token', '').strip()
    if not token:
        return jsonify({"success": False, "error": "X-Connector-Token required"}), 401
    shop = query("SELECT * FROM shops WHERE tally_connector_token=?", (token,), one=True)
    if not shop:
        return jsonify({"success": False, "error": "Invalid or expired connector token"}), 401

    uid = shop['user_id']
    company_name = shop.get('name') or 'My Shop'

    sales_list = []
    raw_sales = rows("SELECT * FROM sales WHERE user_id=? AND cancelled=0 AND (bill_category != 'kacha' OR bill_category IS NULL) ORDER BY sale_date ASC", (uid,))
    for s in raw_sales:
        s_dict = dict(s)
        s_dict['items'] = [dict(it) for it in rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))]
        sales_list.append(s_dict)

    purchases_list = []
    raw_pur = rows("SELECT * FROM purchases WHERE user_id=? ORDER BY bill_date ASC", (uid,))
    for p in raw_pur:
        p_dict = dict(p)
        p_dict['items'] = [dict(it) for it in rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))]
        purchases_list.append(p_dict)

    cn_list = []
    raw_cn = rows("SELECT * FROM sales_returns WHERE user_id=? ORDER BY return_date ASC", (uid,))
    for cn in raw_cn:
        cn_dict = dict(cn)
        cn_dict['items'] = [dict(it) for it in rows("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))]
        cn_list.append(cn_dict)

    dn_list = []
    raw_dn = rows("SELECT * FROM purchase_returns WHERE user_id=? ORDER BY return_date ASC", (uid,))
    for dn in raw_dn:
        dn_dict = dict(dn)
        dn_dict['items'] = [dict(it) for it in rows("SELECT * FROM purchase_return_items WHERE return_id=?", (dn['id'],))]
        dn_list.append(dn_dict)

    ledger_map = {r['key_name']: r['ledger_name'] for r in rows("SELECT key_name, ledger_name FROM shop_ledger_mappings WHERE user_id=?", (uid,))}

    from tally_engine import generate_tally_v2_xml
    res = generate_tally_v2_xml(company_name, sales_list, purchases_list, ledger_map, 'all', credit_notes_list=cn_list, debit_notes_list=dn_list)
    xml_data, s_cnt, p_cnt = res[0], res[1], res[2]

    return jsonify({
        "success": True,
        "company_name": company_name,
        "sales_count": s_cnt,
        "purchases_count": p_cnt,
        "credit_notes_count": len(cn_list),
        "debit_notes_count": len(dn_list),
        "xml_data": xml_data
    })

@app.route('/api/tally/connector/report', methods=['POST'])
def tally_connector_report():
    """Connector reports back sync execution and heartbeat."""
    token = request.headers.get('X-Connector-Token', '').strip()
    if not token:
        return jsonify({"success": False, "error": "X-Connector-Token required"}), 401
    shop = query("SELECT * FROM shops WHERE tally_connector_token=?", (token,), one=True)
    if not shop:
        return jsonify({"success": False, "error": "Invalid connector token"}), 401

    d = request.get_json(silent=True) or {}
    machine = (d.get('machine_name') or 'Windows Client').strip()
    db = get_db()
    db.execute("UPDATE shops SET connector_machine=?, connector_last_seen=datetime('now') WHERE id=?", (machine, shop['id']))
    db.commit()
    return jsonify({"success": True, "status": "acknowledged"})

@app.route('/api/tally/connector/status', methods=['GET'])
@login_required
def tally_connector_status():
    """Returns desktop connector health for UI display."""
    uid = get_uid()
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    token = shop.get('tally_connector_token') or ""
    last_seen = shop.get('connector_last_seen') or ""
    is_active = False
    if last_seen:
        try:
            dt = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
            is_active = (datetime.now() - dt).total_seconds() < 300
        except Exception:
            pass
    return jsonify({
        "success": True,
        "token": token,
        "machine": shop.get('connector_machine') or "",
        "last_seen": last_seen,
        "is_active": is_active
    })


@app.route('/api/gst/drilldown', methods=['GET'])
@login_required
def gst_drilldown():
    """
    GST RECONCILIATION & HIERARCHICAL DRILL-DOWN (October 5 Notes Section 4 & 10)
    GST TOTAL -> INVOICES -> PARTY -> ITEMS -> QTY -> RATE -> TAXABLE -> GST -> TOTAL
    """
    uid = get_uid()
    month = request.args.get('month', str(date.today())[:7])
    
    sales = rows("SELECT * FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0 ORDER BY sale_date ASC", (uid, month + '%'))
    purchases = rows("SELECT * FROM purchases WHERE user_id=? AND bill_date LIKE ? ORDER BY bill_date ASC", (uid, month + '%'))
    
    rates_data = {
        "18": {"rate_label": "18% GST (9% CGST + 9% SGST / 18% IGST)", "sales_taxable": 0.0, "purchases_taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "total_tax": 0.0, "total_value": 0.0, "invoices": []},
        "5":  {"rate_label": "5% GST (2.5% CGST + 2.5% SGST / 5% IGST)", "sales_taxable": 0.0, "purchases_taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "total_tax": 0.0, "total_value": 0.0, "invoices": []},
        "0":  {"rate_label": "0% Exempted / Nil Rated", "sales_taxable": 0.0, "purchases_taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "total_tax": 0.0, "total_value": 0.0, "invoices": []}
    }
    
    for s in sales:
        s_items = rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))
        is_inter = bool(s.get('is_interstate', 0) or s.get('igst_amount', 0) > 0)
        by_rate = {}
        for it in s_items:
            pct = float(it.get('gst_pct', 0))
            bucket = "18" if pct >= 12 else ("5" if pct > 0 else "0")
            by_rate.setdefault(bucket, []).append(it)
            
        for bucket, it_list in by_rate.items():
            b_taxable = sum(round(float(x.get('qty', 1)) * float(x.get('price', 0)), 2) for x in it_list)
            b_gst_pct = 18.0 if bucket == "18" else (5.0 if bucket == "5" else 0.0)
            if is_inter:
                b_igst = round(b_taxable * b_gst_pct / 100, 2)
                b_cgst = b_sgst = 0.0
            else:
                b_cgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_sgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_igst = 0.0
            b_tax = b_cgst + b_sgst + b_igst
            b_total = b_taxable + b_tax
            
            rates_data[bucket]["sales_taxable"] = round(rates_data[bucket]["sales_taxable"] + b_taxable, 2)
            rates_data[bucket]["cgst"] = round(rates_data[bucket]["cgst"] + b_cgst, 2)
            rates_data[bucket]["sgst"] = round(rates_data[bucket]["sgst"] + b_sgst, 2)
            rates_data[bucket]["igst"] = round(rates_data[bucket]["igst"] + b_igst, 2)
            rates_data[bucket]["total_tax"] = round(rates_data[bucket]["total_tax"] + b_tax, 2)
            rates_data[bucket]["total_value"] = round(rates_data[bucket]["total_value"] + b_total, 2)
            
            rates_data[bucket]["invoices"].append({
                "bill_id": s['bill_id'],
                "type": "Sale",
                "date": s['sale_date'],
                "party": s['customer_name'] or "Walk-in",
                "gstin": s.get('customer_gstin') or "",
                "is_interstate": is_inter,
                "taxable": b_taxable,
                "cgst": b_cgst,
                "sgst": b_sgst,
                "igst": b_igst,
                "total": b_total,
                "items": [{
                    "product": x.get('product'),
                    "hsn": x.get('hsn_code', ''),
                    "qty": float(x.get('qty', 1)),
                    "unit": x.get('unit', 'Pc'),
                    "rate": float(x.get('price', 0)),
                    "amount": round(float(x.get('qty', 1)) * float(x.get('price', 0)), 2),
                    "gst_pct": float(x.get('gst_pct', b_gst_pct))
                } for x in it_list]
            })

    for p in purchases:
        p_items = rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))
        is_inter = bool(p.get('is_interstate', 0) or p.get('igst_amount', 0) > 0)
        by_rate = {}
        for it in p_items:
            pct = float(it.get('gst_pct', 0))
            bucket = "18" if pct >= 12 else ("5" if pct > 0 else "0")
            by_rate.setdefault(bucket, []).append(it)
            
        for bucket, it_list in by_rate.items():
            b_taxable = sum(round(float(x.get('qty', 1)) * float(x.get('buy_price', x.get('price', 0))), 2) for x in it_list)
            b_gst_pct = 18.0 if bucket == "18" else (5.0 if bucket == "5" else 0.0)
            if is_inter:
                b_igst = round(b_taxable * b_gst_pct / 100, 2)
                b_cgst = b_sgst = 0.0
            else:
                b_cgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_sgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_igst = 0.0
            b_tax = b_cgst + b_sgst + b_igst
            b_total = b_taxable + b_tax
            
            rates_data[bucket]["purchases_taxable"] = round(rates_data[bucket]["purchases_taxable"] + b_taxable, 2)
            rates_data[bucket]["total_value"] = round(rates_data[bucket]["total_value"] + b_total, 2)
            
            rates_data[bucket]["invoices"].append({
                "bill_id": p.get('purchase_id') or f"P{p['id']}",
                "type": "Purchase",
                "date": p['bill_date'],
                "party": p['supplier_name'] or "Supplier",
                "gstin": p.get('supplier_gstin') or "",
                "is_interstate": is_inter,
                "taxable": b_taxable,
                "cgst": b_cgst,
                "sgst": b_sgst,
                "igst": b_igst,
                "total": b_total,
                "items": [{
                    "product": x.get('product'),
                    "hsn": x.get('hsn_code', ''),
                    "qty": float(x.get('qty', 1)),
                    "unit": x.get('unit', 'Pc'),
                    "rate": float(x.get('buy_price', x.get('price', 0))),
                    "amount": round(float(x.get('qty', 1)) * float(x.get('buy_price', x.get('price', 0))), 2),
                    "gst_pct": float(x.get('gst_pct', b_gst_pct))
                } for x in it_list]
            })

    # Account for Sales Returns (Credit Notes) — Deducts from Outward Tax
    sales_returns = rows("SELECT * FROM sales_returns WHERE user_id=? AND return_date LIKE ? ORDER BY return_date ASC", (uid, month + '%'))
    for cn in sales_returns:
        cn_items = rows("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))
        is_inter = bool(cn.get('is_interstate', 0) or cn.get('igst_amount', 0) > 0)
        by_rate = {}
        for it in cn_items:
            pct = float(it.get('gst_pct', 0))
            bucket = "18" if pct >= 12 else ("5" if pct > 0 else "0")
            by_rate.setdefault(bucket, []).append(it)

        for bucket, it_list in by_rate.items():
            b_taxable = sum(round(float(x.get('qty', 1)) * float(x.get('price', 0)), 2) for x in it_list)
            b_gst_pct = 18.0 if bucket == "18" else (5.0 if bucket == "5" else 0.0)
            if is_inter:
                b_igst = round(b_taxable * b_gst_pct / 100, 2)
                b_cgst = b_sgst = 0.0
            else:
                b_cgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_sgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_igst = 0.0
            b_tax = b_cgst + b_sgst + b_igst
            b_total = b_taxable + b_tax

            rates_data[bucket]["sales_taxable"] = round(rates_data[bucket]["sales_taxable"] - b_taxable, 2)
            rates_data[bucket]["cgst"] = round(rates_data[bucket]["cgst"] - b_cgst, 2)
            rates_data[bucket]["sgst"] = round(rates_data[bucket]["sgst"] - b_sgst, 2)
            rates_data[bucket]["igst"] = round(rates_data[bucket]["igst"] - b_igst, 2)
            rates_data[bucket]["total_tax"] = round(rates_data[bucket]["total_tax"] - b_tax, 2)
            rates_data[bucket]["total_value"] = round(rates_data[bucket]["total_value"] - b_total, 2)

            rates_data[bucket]["invoices"].append({
                "bill_id": cn['credit_note_no'],
                "type": "Credit Note",
                "date": cn['return_date'],
                "party": cn.get('customer_name') or "Walk-in",
                "gstin": cn.get('customer_gstin') or "",
                "is_interstate": is_inter,
                "original_ref": cn.get('original_bill_id') or "",
                "taxable": -b_taxable,
                "cgst": -b_cgst,
                "sgst": -b_sgst,
                "igst": -b_igst,
                "total": -b_total,
                "items": [{
                    "product": x.get('product'),
                    "hsn": x.get('hsn_code', ''),
                    "qty": float(x.get('qty', 1)),
                    "unit": x.get('unit', 'Pc'),
                    "rate": float(x.get('price', 0)),
                    "amount": round(float(x.get('qty', 1)) * float(x.get('price', 0)), 2),
                    "gst_pct": float(x.get('gst_pct', b_gst_pct))
                } for x in it_list]
            })

    # Account for Purchase Returns (Debit Notes) — Deducts from Inward ITC
    pur_returns = rows("SELECT * FROM purchase_returns WHERE user_id=? AND return_date LIKE ? ORDER BY return_date ASC", (uid, month + '%'))
    for dn in pur_returns:
        dn_items = rows("SELECT * FROM purchase_return_items WHERE return_id=?", (dn['id'],))
        is_inter = bool(dn.get('is_interstate', 0) or dn.get('igst_amount', 0) > 0)
        by_rate = {}
        for it in dn_items:
            pct = float(it.get('gst_pct', 0))
            bucket = "18" if pct >= 12 else ("5" if pct > 0 else "0")
            by_rate.setdefault(bucket, []).append(it)

        for bucket, it_list in by_rate.items():
            b_taxable = sum(round(float(x.get('qty', 1)) * float(x.get('price', 0)), 2) for x in it_list)
            b_gst_pct = 18.0 if bucket == "18" else (5.0 if bucket == "5" else 0.0)
            if is_inter:
                b_igst = round(b_taxable * b_gst_pct / 100, 2)
                b_cgst = b_sgst = 0.0
            else:
                b_cgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_sgst = round(b_taxable * (b_gst_pct / 2) / 100, 2)
                b_igst = 0.0
            b_tax = b_cgst + b_sgst + b_igst
            b_total = b_taxable + b_tax

            rates_data[bucket]["purchases_taxable"] = round(rates_data[bucket]["purchases_taxable"] - b_taxable, 2)
            rates_data[bucket]["total_value"] = round(rates_data[bucket]["total_value"] - b_total, 2)

            rates_data[bucket]["invoices"].append({
                "bill_id": dn['debit_note_no'],
                "type": "Debit Note",
                "date": dn['return_date'],
                "party": dn.get('supplier_name') or "Supplier",
                "gstin": dn.get('supplier_gstin') or "",
                "is_interstate": is_inter,
                "original_ref": dn.get('original_invoice_no') or "",
                "taxable": -b_taxable,
                "cgst": -b_cgst,
                "sgst": -b_sgst,
                "igst": -b_igst,
                "total": -b_total,
                "items": [{
                    "product": x.get('product'),
                    "hsn": x.get('hsn_code', ''),
                    "qty": float(x.get('qty', 1)),
                    "unit": x.get('unit', 'Pc'),
                    "rate": float(x.get('price', 0)),
                    "amount": round(float(x.get('qty', 1)) * float(x.get('price', 0)), 2),
                    "gst_pct": float(x.get('gst_pct', b_gst_pct))
                } for x in it_list]
            })

    return jsonify({"success": True, "month": month, "rates": rates_data})


# ─────────────────────────────────────────────────────────────
#  REPORTS
# ─────────────────────────────────────────────────────────────
@app.route('/api/reports/dashboard', methods=['GET'])
@login_required
def dashboard_report():
    uid   = get_uid()
    today = str(date.today())
    month = today[:7]

    today_sales  = query("SELECT COALESCE(SUM(total_amount),0) as t, COUNT(*) as c FROM sales WHERE user_id=? AND sale_date=? AND cancelled=0", (uid, today), one=True)
    month_sales  = query("SELECT COALESCE(SUM(total_amount),0) as t, COUNT(*) as c FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0", (uid, month+'%'), one=True)
    month_purch  = query("SELECT COALESCE(SUM(total_amount),0) as t FROM purchases WHERE user_id=? AND bill_date LIKE ?", (uid, month+'%'), one=True)
    products     = query("SELECT COUNT(*) as c FROM products WHERE user_id=?", (uid,), one=True)
    low_stock    = query("SELECT COUNT(*) as c FROM products WHERE user_id=? AND stock <= low_stock", (uid,), one=True)
    pending      = query("SELECT COALESCE(SUM(total_amount-paid_amount),0) as t FROM sales WHERE user_id=? AND payment_status!='Paid' AND cancelled=0", (uid,), one=True)

    return jsonify({
        "success": True,
        "today_sales": today_sales['t'], "today_bills": today_sales['c'],
        "month_sales": month_sales['t'], "month_bills": month_sales['c'],
        "month_purchase": month_purch['t'],
        "total_products": products['c'], "low_stock": low_stock['c'],
        "pending_amount": pending['t']
    })

@app.route('/api/reports/ai-dashboard', methods=['GET'])
@login_required
def ai_dashboard():
    uid  = get_uid()
    year = request.args.get('year', str(date.today().year))

    # Monthly sales data
    monthly_sales = []
    for m in range(1, 13):
        res = query("SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0",
                    (uid, f"{year}-{m:02d}%"), one=True)
        monthly_sales.append(round(res['t'], 2) if res else 0)

    # Linear Regression Forecast (simple least squares)
    non_zero = [(i+1, v) for i, v in enumerate(monthly_sales) if v > 0]
    predicted = []
    if len(non_zero) >= 2:
        n = len(non_zero)
        xs = [x for x,y in non_zero]
        ys = [y for x,y in non_zero]
        sx, sy = sum(xs), sum(ys)
        sxy = sum(x*y for x,y in zip(xs,ys))
        sx2 = sum(x*x for x in xs)
        slope = (n*sxy - sx*sy) / (n*sx2 - sx*sx + 0.001)
        intercept = (sy - slope*sx) / n
        for i in range(3):
            pred = round(max(0, intercept + slope*(13+i)), 2)
            predicted.append(pred)

    avg_monthly = round(sum(v for v in monthly_sales if v > 0) / max(1, sum(1 for v in monthly_sales if v > 0)), 2)
    forecast    = predicted[0] if predicted else avg_monthly
    prev_avg    = round(sum(monthly_sales[:6]) / max(1, sum(1 for v in monthly_sales[:6] if v > 0)), 2)
    curr_avg    = round(sum(monthly_sales[6:]) / max(1, sum(1 for v in monthly_sales[6:] if v > 0)), 2)
    growth_pct  = round((curr_avg - prev_avg) / max(1, prev_avg) * 100, 1) if prev_avg else 0

    # Top products by revenue
    top_products_raw = rows("""
        SELECT si.product as name, SUM(si.amount) as revenue
        FROM sale_items si JOIN sales s ON si.sale_id=s.id
        WHERE s.user_id=? AND s.sale_date LIKE ? AND s.cancelled=0
        GROUP BY si.product ORDER BY revenue DESC LIMIT 6
    """, (uid, year+'%'))

    # Stock Intelligence
    products = rows("SELECT * FROM products WHERE user_id=?", (uid,))
    thirty_ago = str(date.today() - timedelta(days=30))
    stock_intel = []
    for p in products:
        sold_row = query("""
            SELECT COALESCE(SUM(si.qty),0) as qty FROM sale_items si
            JOIN sales s ON si.sale_id=s.id
            WHERE s.user_id=? AND si.product=? AND s.sale_date>=? AND s.cancelled=0
        """, (uid, p['name'], thirty_ago), one=True)
        sold_30d   = float(sold_row['qty']) if sold_row else 0
        daily_rate = round(sold_30d / 30, 2)
        days_left  = round(p['stock'] / daily_rate) if daily_rate > 0 else None
        stock_intel.append({
            'name': p['name'], 'stock': p['stock'], 'unit': p['unit'],
            'sold_30d': int(sold_30d), 'daily_rate': daily_rate, 'days_left': days_left
        })
    stock_intel.sort(key=lambda x: (x['days_left'] is None, x['days_left'] if x['days_left'] is not None else 9999))

    # AI Recommendations
    recs = []
    critical = [s for s in stock_intel if s['days_left'] is not None and s['days_left'] <= 7]
    if critical:
        recs.append({'type':'danger','icon':'🚨','title':f"Reorder: {critical[0]['name']}",'message':f"Only {critical[0]['days_left']} days of stock left at current sales rate."})
    if avg_monthly > 0:
        all_sales = rows("SELECT total_amount, paid_amount FROM sales WHERE user_id=? AND cancelled=0", (uid,))
        total_rev = sum(s['total_amount'] for s in all_sales)
        total_pur_row = query("SELECT COALESCE(SUM(total_amount),0) as t FROM purchases WHERE user_id=?", (uid,), one=True)
        total_pur = total_pur_row['t'] if total_pur_row else 0
        if total_pur > total_rev:
            recs.append({'type':'warning','icon':'📊','title':'Low Profit Margin','message':f"Total purchases ({round(total_pur/1000,1)}K) exceeds sales ({round(total_rev/1000,1)}K). Review pricing."})
    no_gstin = rows("SELECT COUNT(*) as c FROM sales WHERE user_id=? AND (customer_gstin IS NULL OR customer_gstin='') AND cancelled=0", (uid,))
    total_bills = rows("SELECT COUNT(*) as c FROM sales WHERE user_id=? AND cancelled=0", (uid,))
    if no_gstin and total_bills and total_bills[0]['c'] > 0:
        pct = round(no_gstin[0]['c']/total_bills[0]['c']*100)
        if pct > 50:
            recs.append({'type':'info','icon':'📋','title':'Collect GST Numbers','message':f"{no_gstin[0]['c']}/{total_bills[0]['c']} bills have no customer GSTIN. Helps with input tax credit."})
    pending_row = query("SELECT COALESCE(SUM(total_amount-paid_amount),0) as t FROM sales WHERE user_id=? AND payment_status!='Paid' AND cancelled=0",(uid,),one=True)
    pending_amt = pending_row['t'] if pending_row else 0
    if pending_amt > 5000:
        recs.append({'type':'warning','icon':'💰','title':'Pending Payments','message':f"₹{round(pending_amt,0):,.0f} pending. Send payment reminders via Customer Ledger."})
    if not recs:
        recs.append({'type':'success','icon':'✅','title':'Business Looking Good!','message':'No critical issues found. Keep it up!'})

    # Customer Intelligence
    cust_bills = rows("""
        SELECT customer_name as name, COUNT(*) as bills, SUM(total_amount) as spent
        FROM sales WHERE user_id=? AND cancelled=0 AND customer_name!='' AND customer_name!='Walk-in'
        GROUP BY customer_name ORDER BY spent DESC LIMIT 6
    """, (uid,))
    total_custs  = len(cust_bills)
    repeat_custs = sum(1 for c in cust_bills if c['bills'] > 1)
    avg_bill_row = query("SELECT COALESCE(AVG(total_amount),0) as a FROM sales WHERE user_id=? AND cancelled=0",(uid,),one=True)
    avg_bill     = round(avg_bill_row['a'],2) if avg_bill_row else 0
    top_customers = []
    for c in cust_bills:
        loyalty = 'VIP' if c['bills'] >= 5 else 'Loyal' if c['bills'] >= 2 else 'New'
        top_customers.append({'name':c['name'],'bills':c['bills'],'spent':round(c['spent'],2),'loyalty':loyalty})

    return jsonify({
        'success': True,
        'monthly_sales': monthly_sales,
        'predicted':     predicted,
        'forecast':      forecast,
        'avg_monthly':   avg_monthly,
        'growth_pct':    growth_pct,
        'top_products':  top_products_raw,
        'stock_intelligence': stock_intel,
        'recommendations':   recs,
        'customer_stats':    {'total':total_custs,'repeat':repeat_custs,'avg_bill':avg_bill},
        'top_customers':     top_customers
    })

@app.route('/api/sales/<bill_id>', methods=['PUT'])
@login_required
def update_sale(bill_id):
    uid = get_uid()
    d   = request.get_json(silent=True) or {}
    db  = get_db()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=?", (bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "Bill not found"}), 404

    if sale['cancelled']:
        return jsonify({"success": False, "error": "Cancelled bills cannot be edited"}), 400
    items     = d.get('items', [])
    discount  = float(d.get('discount', 0))
    if not items or discount < 0:
        return jsonify({"success": False, "error": "Items and a non-negative discount are required"}), 400
    paid      = max(0, float(d.get('paid_amount', 0)))
    subtotal = cgst = sgst = igst = 0
    line_items = []

    is_interstate = int(d.get('is_interstate', sale.get('is_interstate', 0) if isinstance(sale, dict) else (sale['is_interstate'] if 'is_interstate' in sale.keys() else 0)))
    cgstin = (d.get('customer_gstin') or '').strip().upper()
    state_code = (d.get('state_code') or '').strip()
    state_name = (d.get('state_name') or '').strip()
    pan_no = (d.get('pan_no') or '').strip().upper()
    khata_pen_no = (d.get('khata_pen_no') or d.get('khata_pen_number') or (sale.get('khata_pen_no') if isinstance(sale, dict) else '') or '').strip()
    if not pan_no and len(cgstin) == 15:
        pan_no = cgstin[2:12]
    if not state_code and len(cgstin) >= 2:
        state_code = cgstin[:2]
    vehicle_no = (d.get('vehicle_no') or '').strip()
    transporter_id = (d.get('transporter_id') or '').strip()
    billing_address = (d.get('billing_address') or d.get('address') or '').strip()
    shipping_address = (d.get('shipping_address') or billing_address).strip()

    for it in items:
        name = (it.get('product') or '').strip()
        qty, price = float(it.get('qty', 0)), float(it.get('price', 0))
        if not name or qty <= 0 or price < 0:
            return jsonify({"success": False, "error": "Each item needs a product, positive quantity, and non-negative price"}), 400
        gst = float(it.get('gst_pct', 0))
        if is_interstate:
            igst_pct = float(it.get('igst_pct', gst))
            cgst_pct = sgst_pct = 0
        else:
            igst_pct = 0
            cgst_pct = float(it.get('cgst_pct', gst / 2))
            sgst_pct = float(it.get('sgst_pct', gst / 2))
        amount = round(qty * price, 2)
        subtotal += amount
        cgst += round(amount * cgst_pct / 100, 2)
        sgst += round(amount * sgst_pct / 100, 2)
        igst += round(amount * igst_pct / 100, 2)
        line_items.append((name, it.get('hsn_code',''), it.get('unit','Pcs'), qty, price, gst, cgst_pct, sgst_pct, igst_pct, amount))
    subtotal, cgst, sgst, igst = map(lambda value: round(value, 2), (subtotal, cgst, sgst, igst))
    total     = round(subtotal + cgst + sgst + igst - discount, 2)
    if total < 0:
        return jsonify({"success": False, "error": "Discount cannot exceed the bill total"}), 400
    paid = min(paid, total)
    status    = 'Paid' if paid >= total else ('Partial' if paid > 0 else 'Unpaid')

    db.execute("""UPDATE sales SET customer_name=?, customer_phone=?, customer_gstin=?,
        sale_date=?, payment_mode=?, paid_amount=?, discount=?,
        subtotal=?, cgst_amount=?, sgst_amount=?, igst_amount=?, total_amount=?, payment_status=?,
        is_interstate=?, state_code=?, state_name=?, pan_no=?, khata_pen_no=?, vehicle_no=?, transporter_id=?,
        billing_address=?, shipping_address=?
        WHERE bill_id=? AND user_id=?""",
        (d.get('customer_name',''), d.get('customer_phone',''), cgstin,
         d.get('sale_date', str(date.today())), d.get('payment_mode','Cash'),
         paid, discount, subtotal, cgst, sgst, igst, total, status,
         is_interstate, state_code, state_name, pan_no, khata_pen_no, vehicle_no, transporter_id,
         billing_address, shipping_address,
         bill_id, uid))

    # Put back old stock before applying the revised quantities.
    for old in rows("SELECT product, qty FROM sale_items WHERE sale_id=?", (sale['id'],)):
        db.execute("UPDATE products SET stock=stock+? WHERE user_id=? AND name=?", (old['qty'], uid, old['product']))
    db.execute("DELETE FROM sale_items WHERE sale_id=?", (sale['id'],))
    for name, hsn, unit, qty, price, gst, cgst_pct, sgst_pct, igst_pct, amount in line_items:
        db.execute("""INSERT INTO sale_items (sale_id,product,hsn_code,qty,unit,price,gst_pct,cgst_pct,sgst_pct,igst_pct,amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sale['id'], name, hsn, qty, unit, price, gst, cgst_pct, sgst_pct, igst_pct, amount))
        db.execute("UPDATE products SET stock=MAX(0,stock-?) WHERE user_id=? AND name=?", (qty, uid, name))
    db.commit()
    return jsonify({"success": True, "total_amount": total})

@app.route('/api/sales/<bill_id>/cancel', methods=['POST'])
@login_required
def cancel_sale(bill_id):
    uid = get_uid()
    db  = get_db()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=?", (bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "Bill not found"}), 404
    if sale['cancelled']:
        return jsonify({"success": False, "error": "Bill is already cancelled"}), 400

    # 1. Prevent phantom stock & accounting inconsistency:
    # If a Credit Note / Sales Return already exists for this bill, reject cancellation with HTTP 400.
    active_cn = query("""
        SELECT COUNT(*) as cnt FROM sales_returns 
        WHERE user_id=? AND (original_sale_id=? OR original_bill_id=?)
    """, (uid, sale['id'], bill_id), one=True)
    legacy_ret = query("""
        SELECT COUNT(*) as cnt FROM returns 
        WHERE user_id=? AND original_bill_id=?
    """, (uid, bill_id), one=True)
    if (active_cn and active_cn['cnt'] > 0) or (legacy_ret and legacy_ret['cnt'] > 0):
        return jsonify({
            "success": False,
            "error": "Cannot cancel invoice with active Credit Notes / Sales Returns. Please adjust credit notes or return vouchers instead."
        }), 400

    # 2. No returns exist: restore sold quantities exactly once
    for item in rows("SELECT product, qty FROM sale_items WHERE sale_id=?", (sale['id'],)):
        qty_to_restore = float(item['qty'])
        if qty_to_restore > 0:
            db.execute("UPDATE products SET stock=stock+? WHERE user_id=? AND name=?", (qty_to_restore, uid, item['product']))
    db.execute("UPDATE sales SET cancelled=1 WHERE bill_id=? AND user_id=?", (bill_id, uid))
    db.commit()
    return jsonify({"success": True, "message": "Invoice cancelled and stock restored successfully."})

@app.route('/api/reports/monthly-chart', methods=['GET'])
@login_required
def monthly_chart():
    uid  = get_uid()
    year = request.args.get('year', str(date.today().year))
    monthly = []
    for m in range(1, 13):
        month_str = f"{year}-{m:02d}"
        result = query(
            "SELECT COALESCE(SUM(total_amount),0) as total FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0",
            (uid, month_str + '%'), one=True
        )
        monthly.append(round(result['total'], 2) if result else 0)
    return jsonify({"success": True, "monthly": monthly, "year": year})

@app.route('/api/reports/daily-summary', methods=['GET'])
@login_required
def daily_summary():
    uid   = get_uid()
    today = str(date.today())
    week_ago = str(date.today() - timedelta(days=7))

    today_sales = rows("SELECT * FROM sales WHERE user_id=? AND sale_date=? AND cancelled=0", (uid, today))
    week_sales  = rows("SELECT * FROM sales WHERE user_id=? AND sale_date >= ? AND cancelled=0", (uid, week_ago))

    # Top items today
    today_ids = [s['id'] for s in today_sales]
    top_items = []
    if today_ids:
        ph = ','.join('?'*len(today_ids))
        items = rows(f"SELECT product, SUM(qty) as qty FROM sale_items WHERE sale_id IN ({ph}) GROUP BY product ORDER BY qty DESC LIMIT 5", tuple(today_ids))
        top_items = items

    last_bill = rows("SELECT * FROM sales WHERE user_id=? AND cancelled=0 ORDER BY created_at DESC LIMIT 1", (uid,))

    return jsonify({
        "success":    True,
        "today_sales": round(sum(s['total_amount'] for s in today_sales), 2),
        "today_bills": len(today_sales),
        "week_sales":  round(sum(s['total_amount'] for s in week_sales), 2),
        "week_bills":  len(week_sales),
        "top_items":   top_items,
        "last_bill":   last_bill[0] if last_bill else None
    })

@app.route('/api/reports/gst', methods=['GET'])
@login_required
def gst_report():
    uid   = get_uid()
    month = request.args.get('month', str(date.today())[:7])
    # Strictly include only official Pakka / Tax Invoices in GST reports
    sales = rows("SELECT * FROM sales WHERE user_id=? AND sale_date LIKE ? AND cancelled=0 AND (bill_category!='kacha' OR bill_category IS NULL)", (uid, month+'%'))
    purch = rows("SELECT * FROM purchases WHERE user_id=? AND bill_date LIKE ?", (uid, month+'%'))

    # HSN-wise summary from sale_items
    sale_ids = [s['id'] for s in sales]
    hsn_summary = []
    if sale_ids:
        placeholders = ','.join('?' * len(sale_ids))
        items = rows(
            f"SELECT * FROM sale_items WHERE sale_id IN ({placeholders})",
            tuple(sale_ids)
        )
        hsn_map = {}
        for item in items:
            key = (item.get('hsn_code') or 'N/A').strip() or 'N/A'
            prod = item.get('product', '')
            if key not in hsn_map:
                hsn_map[key] = {
                    'hsn_code':     key,
                    'product':      prod,
                    'qty':          0,
                    'taxable_value':0.0,
                    'gst_pct':      item.get('gst_pct', 0),
                    'cgst':         0.0,
                    'sgst':         0.0,
                    'total_tax':    0.0
                }
            h = hsn_map[key]
            h['qty']           += item.get('qty', 0)
            h['taxable_value'] += item.get('amount', 0)
            h['cgst']          += round(item.get('amount', 0) * item.get('cgst_pct', 0) / 100, 2)
            h['sgst']          += round(item.get('amount', 0) * item.get('sgst_pct', 0) / 100, 2)
            h['total_tax']      = round(h['cgst'] + h['sgst'], 2)
        hsn_summary = sorted(hsn_map.values(), key=lambda x: x['taxable_value'], reverse=True)

    return jsonify({"success": True, "sales": sales, "purchases": purch,
                    "month": month, "hsn_summary": hsn_summary})


# ─────────────────────────────────────────────────────────────
#  PDF INVOICE + EXCEL EXPORT
# ─────────────────────────────────────────────────────────────
@app.route('/api/sales/<bill_id>/invoice')
@login_required
def get_invoice(bill_id):
    uid  = get_uid()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=?", (bill_id, uid), one=True)
    if not sale: return "Invoice not found", 404
    items = rows("SELECT * FROM sale_items WHERE sale_id=?", (sale['id'],))
    shop  = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    return jsonify({"success": True, "sale": dict(sale), "items": [dict(i) for i in items], "shop": dict(shop)})

@app.route('/api/sales/<bill_id>/export/excel')
@login_required
def export_single_sale_excel(bill_id):
    import io, csv
    uid  = get_uid()
    sale = query("SELECT * FROM sales WHERE bill_id=? AND user_id=?", (bill_id, uid), one=True)
    if not sale:
        return jsonify({"success": False, "error": "Invoice not found"}), 404
    items = rows("SELECT * FROM sale_items WHERE sale_id=?", (sale['id'],))
    shop  = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}

    output = io.StringIO()
    writer = csv.writer(output)

    # Header info
    shop_name = shop.get('name') or 'BillZen Invoice'
    writer.writerow([shop_name])
    if shop.get('address'): writer.writerow(['Address:', shop.get('address')])
    if shop.get('phone'):   writer.writerow(['Phone:', shop.get('phone')])
    if shop.get('gstin'):   writer.writerow(['GSTIN:', shop.get('gstin')])
    writer.writerow([])

    # Bill details
    cat_label = 'Kacha (Estimate / Cash Memo)' if sale.get('bill_category') == 'kacha' else 'Pakka (GST Tax Invoice)'
    writer.writerow(['Invoice No:', sale['bill_id'], 'Date:', sale['sale_date']])
    writer.writerow(['Customer Name:', sale['customer_name'], 'Phone:', sale.get('customer_phone') or '', 'Khata Pen No:', sale.get('khata_pen_no') or ''])
    writer.writerow(['Bill Category:', cat_label, 'Bill Type:', sale.get('bill_type') or 'retail'])
    writer.writerow(['Payment Mode:', sale.get('payment_mode') or 'Cash', 'Status:', sale.get('payment_status') or 'Paid'])
    writer.writerow([])

    # Items Table
    writer.writerow(['#', 'Product Name', 'HSN/SAC', 'Quantity', 'Rate (₹)', 'GST %', 'Amount (₹)'])
    for idx, it in enumerate(items, 1):
        writer.writerow([
            idx,
            it.get('product', ''),
            it.get('hsn_code', ''),
            it.get('qty', 0),
            it.get('price', 0),
            f"{it.get('gst_pct', 0)}%",
            it.get('amount', 0)
        ])

    writer.writerow([])
    writer.writerow(['', '', '', '', '', 'Subtotal:', sale.get('subtotal', 0)])
    if (sale.get('cgst_amount') or 0) > 0:
        writer.writerow(['', '', '', '', '', 'CGST:', sale.get('cgst_amount', 0)])
    if (sale.get('sgst_amount') or 0) > 0:
        writer.writerow(['', '', '', '', '', 'SGST:', sale.get('sgst_amount', 0)])
    if (sale.get('igst_amount') or 0) > 0:
        writer.writerow(['', '', '', '', '', 'IGST:', sale.get('igst_amount', 0)])
    if (sale.get('discount') or 0) > 0:
        writer.writerow(['', '', '', '', '', 'Discount:', sale.get('discount', 0)])
    writer.writerow(['', '', '', '', '', 'Grand Total:', sale.get('total_amount', 0)])
    writer.writerow(['', '', '', '', '', 'Paid Amount:', sale.get('paid_amount', 0)])
    due = max(0, (sale.get('total_amount') or 0) - (sale.get('paid_amount') or 0))
    if due > 0:
        writer.writerow(['', '', '', '', '', 'Due Balance:', due])

    from flask import Response
    safe_name = "".join(c for c in (sale.get('customer_name') or '') if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    filename = f"{sale['bill_id']}_{safe_name or 'Bill'}.csv"
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.route('/api/sales/export/excel')
@login_required
def export_sales_excel():
    import io, csv
    uid   = get_uid()
    sales = rows("SELECT * FROM sales WHERE user_id=? ORDER BY sale_date DESC", (uid,))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Bill ID','Date','Customer','Phone','Khata Pen No','Category','Type','Subtotal','CGST','SGST','IGST','Discount','Total','Paid','Status','Payment Mode'])
    for s in sales:
        cat_label = 'Kacha (Estimate)' if s.get('bill_category') == 'kacha' else 'Pakka (GST)'
        writer.writerow([s['bill_id'], s['sale_date'], s['customer_name'], s['customer_phone'], s.get('khata_pen_no') or '',
                        cat_label, s['bill_type'], s['subtotal'], s['cgst_amount'], s['sgst_amount'],
                        s['igst_amount'], s['discount'], s['total_amount'], s['paid_amount'],
                        'Cancelled' if s.get('cancelled') else s['payment_status'], s['payment_mode']])
    from flask import Response
    return Response(output.getvalue(), mimetype='text/csv',
                   headers={"Content-Disposition": "attachment;filename=sales_report.csv"})

@app.route('/api/purchases/export/excel')
@login_required
def export_purchases_excel():
    import io, csv
    uid  = get_uid()
    purs = rows("SELECT * FROM purchases WHERE user_id=? ORDER BY bill_date DESC", (uid,))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Purchase ID','Date','Supplier','GSTIN','Invoice No','Subtotal','CGST','SGST','IGST','Total'])
    for p in purs:
        writer.writerow([p['purchase_id'], p['bill_date'], p['supplier_name'], p['supplier_gstin'],
                        p['invoice_no'], p['subtotal'], p['cgst_amount'], p['sgst_amount'],
                        p['igst_amount'], p['total_amount']])
    from flask import Response
    return Response(output.getvalue(), mimetype='text/csv',
                   headers={"Content-Disposition": "attachment;filename=purchase_report.csv"})

@app.route('/api/products/export/excel')
@login_required
def export_products_excel():
    import io, csv
    uid  = get_uid()
    prods = rows("SELECT * FROM products WHERE user_id=? ORDER BY name", (uid,))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Product Name','HSN Code','Category','Unit','GST%','Buy Price','Sell Price','Stock','Status'])
    for p in prods:
        status = 'Low Stock' if p['stock'] <= (p.get('low_stock') or 5) else 'In Stock'
        writer.writerow([p['name'], p.get('hsn_code',''), p.get('category','General'),
                        p.get('unit','Pcs'), p.get('gst_pct',18), p['buy_price'],
                        p['sell_price'], p['stock'], status])
    from flask import Response
    return Response(output.getvalue(), mimetype='text/csv',
                   headers={"Content-Disposition": "attachment;filename=products_report.csv"})

# ─────────────────────────────────────────────────────────────
#  QUOTATIONS / ESTIMATES (Full Backend Persistence)
# ─────────────────────────────────────────────────────────────
@app.route('/api/quotations', methods=['GET'])
@login_required
def get_quotations():
    uid = get_uid()
    quotes = rows("SELECT * FROM quotations WHERE user_id=? ORDER BY created_at DESC", (uid,))
    for q in quotes:
        q['items'] = rows("SELECT * FROM quotation_items WHERE quote_id=?", (q['id'],))
    return jsonify({"success": True, "quotations": quotes})

@app.route('/api/quotations', methods=['POST'])
@login_required
def create_quotation():
    d = request.get_json(silent=True) or {}
    customer_name = (d.get('customer_name') or '').strip()
    if not customer_name:
        return jsonify({"success": False, "error": "Customer name is required"}), 400
    items = d.get('items', [])
    if not items:
        return jsonify({"success": False, "error": "At least one item is required"}), 400

    uid = get_uid()
    db = get_db()
    quote_id = gen_id('Q')

    subtotal = tax_total = 0
    line_items = []
    for it in items:
        name = (it.get('product') or '').strip()
        qty = float(it.get('qty', 0))
        price = float(it.get('price', 0))
        gst = float(it.get('gst_pct', 0))
        if not name or qty <= 0:
            continue
        amt = round(qty * price, 2)
        subtotal += amt
        tax_total += round(amt * gst / 100, 2)
        line_items.append((name, it.get('hsn_code', ''), it.get('unit', 'Pcs'), qty, price, gst, amt))

    discount = float(d.get('discount', 0))
    total = round(subtotal + tax_total - discount, 2)

    cur = db.execute("""INSERT INTO quotations (user_id, quote_id, customer_name, customer_phone,
                        subtotal, tax_amount, discount, total_amount, quote_date, valid_until, notes, status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (uid, quote_id, customer_name, d.get('customer_phone', ''),
                      subtotal, tax_total, discount, total,
                      d.get('quote_date', str(date.today())), d.get('valid_until', ''),
                      d.get('notes', ''), 'Pending'))
    qid = cur.lastrowid

    for name, hsn, unit, qty, price, gst, amt in line_items:
        db.execute("""INSERT INTO quotation_items (quote_id, product, hsn_code, unit, qty, price, gst_pct, amount)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (qid, name, hsn, unit, qty, price, gst, amt))

    db.commit()
    return jsonify({"success": True, "quote_id": quote_id, "total": total}), 201

@app.route('/api/quotations/<quote_id>', methods=['GET'])
@login_required
def get_quotation_detail(quote_id):
    uid = get_uid()
    quote = query("SELECT * FROM quotations WHERE quote_id=? AND user_id=?", (quote_id, uid), one=True)
    if not quote:
        return jsonify({"success": False, "error": "Quotation not found"}), 404
    items = rows("SELECT * FROM quotation_items WHERE quote_id=?", (quote['id'],))
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    return jsonify({"success": True, "quotation": dict(quote), "items": items, "shop": dict(shop)})

@app.route('/api/quotations/<int:qid>', methods=['DELETE'])
@login_required
def delete_quotation(qid):
    uid = get_uid()
    db = get_db()
    db.execute("DELETE FROM quotations WHERE id=? AND user_id=?", (qid, uid))
    db.commit()
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────
#  DELIVERY CHALLANS (Full Backend Persistence)
# ─────────────────────────────────────────────────────────────
@app.route('/api/challans', methods=['GET'])
@login_required
def get_challans():
    uid = get_uid()
    challans = rows("SELECT * FROM delivery_challans WHERE user_id=? ORDER BY created_at DESC", (uid,))
    for c in challans:
        c['items'] = rows("SELECT * FROM delivery_challan_items WHERE challan_id=?", (c['id'],))
    return jsonify({"success": True, "challans": challans})

@app.route('/api/challans', methods=['POST'])
@login_required
def create_challan():
    d = request.get_json(silent=True) or {}
    consignee = (d.get('consignee_name') or '').strip()
    if not consignee:
        return jsonify({"success": False, "error": "Consignee name is required"}), 400
    items = d.get('items', [])
    if not items:
        return jsonify({"success": False, "error": "At least one item is required"}), 400

    uid = get_uid()
    db = get_db()
    challan_id = gen_id('DC')

    cur = db.execute("""INSERT INTO delivery_challans (user_id, challan_id, consignee_name, address,
                        phone, challan_date, vehicle_no, driver_name, notes, status)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (uid, challan_id, consignee, d.get('address', ''),
                      d.get('phone', ''), d.get('challan_date', str(date.today())),
                      d.get('vehicle_no', ''), d.get('driver_name', ''),
                      d.get('notes', ''), 'Delivered'))
    cid = cur.lastrowid

    for it in items:
        name = (it.get('product') or '').strip()
        qty = float(it.get('qty', 0))
        if not name or qty <= 0:
            continue
        db.execute("""INSERT INTO delivery_challan_items (challan_id, product, hsn_code, unit, qty, notes)
                      VALUES (?,?,?,?,?,?)""",
                   (cid, name, it.get('hsn_code', ''), it.get('unit', 'Pcs'), qty, it.get('notes', '')))

    db.commit()
    return jsonify({"success": True, "challan_id": challan_id}), 201

@app.route('/api/challans/<challan_id>', methods=['GET'])
@login_required
def get_challan_detail(challan_id):
    uid = get_uid()
    challan = query("SELECT * FROM delivery_challans WHERE challan_id=? AND user_id=?", (challan_id, uid), one=True)
    if not challan:
        return jsonify({"success": False, "error": "Challan not found"}), 404
    items = rows("SELECT * FROM delivery_challan_items WHERE challan_id=?", (challan['id'],))
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    return jsonify({"success": True, "challan": dict(challan), "items": items, "shop": dict(shop)})

@app.route('/api/challans/<int:cid>', methods=['DELETE'])
@login_required
def delete_challan(cid):
    uid = get_uid()
    db = get_db()
    db.execute("DELETE FROM delivery_challans WHERE id=? AND user_id=?", (cid, uid))
    db.commit()
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────
#  BACKUP & RESTORE ENGINE (True Database Persistence)
# ─────────────────────────────────────────────────────────────
@app.route('/api/backup', methods=['GET'])
@login_required
def full_backup():
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    uid = get_uid()
    shop = query("SELECT * FROM shops WHERE user_id=?", (uid,), one=True) or {}
    sales_list = rows("SELECT * FROM sales WHERE user_id=?", (uid,))
    for s in sales_list:
        s['items'] = rows("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))
    pur_list = rows("SELECT * FROM purchases WHERE user_id=?", (uid,))
    for p in pur_list:
        p['items'] = rows("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))
    quotes = rows("SELECT * FROM quotations WHERE user_id=?", (uid,))
    for q in quotes:
        q['items'] = rows("SELECT * FROM quotation_items WHERE quote_id=?", (q['id'],))
    challans = rows("SELECT * FROM delivery_challans WHERE user_id=?", (uid,))
    for c in challans:
        c['items'] = rows("SELECT * FROM delivery_challan_items WHERE challan_id=?", (c['id'],))

    clean_shop = dict(shop)
    clean_shop['gemini_key'] = ''
    clean_shop['openai_key'] = ''
    clean_shop['anthropic_key'] = ''
    clean_shop['tally_connector_token'] = ''

    sales_returns_list = rows("SELECT * FROM sales_returns WHERE user_id=?", (uid,))
    for sr in sales_returns_list:
        sr['items'] = rows("SELECT * FROM sales_return_items WHERE return_id=?", (sr['id'],))

    pur_returns_list = rows("SELECT * FROM purchase_returns WHERE user_id=?", (uid,))
    for pr in pur_returns_list:
        pr['items'] = rows("SELECT * FROM purchase_return_items WHERE return_id=?", (pr['id'],))

    ledger_maps = rows("SELECT key_name, ledger_name FROM shop_ledger_mappings WHERE user_id=?", (uid,))

    backup_payload = {
        "version": "2.0",
        "app": "BillZen AI",
        "exported_at": datetime.now().isoformat(),
        "shop": clean_shop,
        "products": rows("SELECT * FROM products WHERE user_id=?", (uid,)),
        "customers": rows("SELECT * FROM customers WHERE user_id=?", (uid,)),
        "suppliers": rows("SELECT * FROM suppliers WHERE user_id=?", (uid,)),
        "units": rows("SELECT * FROM units WHERE user_id=?", (uid,)),
        "categories": rows("SELECT * FROM categories WHERE user_id=?", (uid,)),
        "sales": sales_list,
        "purchases": pur_list,
        "quotations": quotes,
        "challans": challans,
        "returns": rows("SELECT * FROM returns WHERE user_id=?", (uid,)),
        "sales_returns": sales_returns_list,
        "purchase_returns": pur_returns_list,
        "shop_ledger_mappings": ledger_maps,
        "stock_adjustments": rows("SELECT * FROM stock_adjustments WHERE user_id=?", (uid,)),
        "customer_payments": rows("SELECT * FROM customer_payments WHERE user_id=?", (uid,))
    }
    return jsonify({"success": True, "backup": backup_payload})

@app.route('/api/restore', methods=['POST'])
@login_required
def full_restore():
    if session.get('role') != 'admin':
        return jsonify({"success": False, "error": "Admin only"}), 403
    d = request.get_json(silent=True) or {}
    backup = d.get('backup') or d
    if not isinstance(backup, dict):
        return jsonify({"success": False, "error": "Invalid backup data"}), 400

    uid = get_uid()
    db = get_db()
    try:
        if 'products' in backup:
            for p in backup['products']:
                existing = db.execute("SELECT id FROM products WHERE user_id=? AND name=?", (uid, p.get('name'))).fetchone()
                if not existing:
                    db.execute("""INSERT INTO products (user_id, name, hsn_code, category, unit, gst_pct, buy_price, sell_price, stock, low_stock, barcode)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                               (uid, p.get('name'), p.get('hsn_code',''), p.get('category','General'),
                                p.get('unit','Pcs'), float(p.get('gst_pct',18)), float(p.get('buy_price',0)),
                                float(p.get('sell_price',0)), float(p.get('stock',0)), float(p.get('low_stock',5)), p.get('barcode','')))
        if 'customers' in backup:
            for c in backup['customers']:
                existing = db.execute("SELECT id FROM customers WHERE user_id=? AND name=?", (uid, c.get('name'))).fetchone()
                if not existing:
                    db.execute("INSERT INTO customers (user_id, name, phone, email, gstin, address) VALUES (?,?,?,?,?,?)",
                               (uid, c.get('name'), c.get('phone',''), c.get('email',''), c.get('gstin',''), c.get('address','')))
        if 'suppliers' in backup:
            for s in backup['suppliers']:
                existing = db.execute("SELECT id FROM suppliers WHERE user_id=? AND name=?", (uid, s.get('name'))).fetchone()
                if not existing:
                    db.execute("INSERT INTO suppliers (user_id, name, phone, email, gstin, address) VALUES (?,?,?,?,?,?)",
                               (uid, s.get('name'), s.get('phone',''), s.get('email',''), s.get('gstin',''), s.get('address','')))

        # Restore Sales Returns (Credit Notes)
        if 'sales_returns' in backup:
            for sr in backup['sales_returns']:
                existing_sr = db.execute("SELECT id FROM sales_returns WHERE user_id=? AND credit_note_no=?", (uid, sr.get('credit_note_no'))).fetchone()
                if not existing_sr:
                    cur_sr = db.execute("""INSERT INTO sales_returns (credit_note_no, user_id, original_sale_id, original_bill_id,
                                           original_sale_date, customer_name, customer_phone, customer_gstin, place_of_supply,
                                           is_interstate, return_date, reason, subtotal, cgst_amount, sgst_amount, igst_amount,
                                           total_amount, refund_mode, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                        (sr.get('credit_note_no'), uid, sr.get('original_sale_id', 0), sr.get('original_bill_id', ''),
                                         sr.get('original_sale_date', ''), sr.get('customer_name', ''), sr.get('customer_phone', ''),
                                         sr.get('customer_gstin', ''), sr.get('place_of_supply', '07'), int(sr.get('is_interstate', 0)),
                                         sr.get('return_date', ''), sr.get('reason', ''), float(sr.get('subtotal', 0)),
                                         float(sr.get('cgst_amount', 0)), float(sr.get('sgst_amount', 0)), float(sr.get('igst_amount', 0)),
                                         float(sr.get('total_amount', 0)), sr.get('refund_mode', 'Credit Note'), sr.get('notes', '')))
                    new_sr_id = cur_sr.lastrowid
                    for srit in sr.get('items', []):
                        db.execute("""INSERT INTO sales_return_items (return_id, original_item_id, product, hsn_code, qty, unit, price,
                                      taxable_amount, gst_pct, cgst_amount, sgst_amount, igst_amount, total_amount)
                                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (new_sr_id, srit.get('original_item_id', 0), srit.get('product', ''), srit.get('hsn_code', ''),
                                    float(srit.get('qty', 0)), srit.get('unit', 'Pcs'), float(srit.get('price', 0)),
                                    float(srit.get('taxable_amount', 0)), float(srit.get('gst_pct', 18)),
                                    float(srit.get('cgst_amount', 0)), float(srit.get('sgst_amount', 0)),
                                    float(srit.get('igst_amount', 0)), float(srit.get('total_amount', 0))))

        # Restore Purchase Returns (Debit Notes)
        if 'purchase_returns' in backup:
            for pr in backup['purchase_returns']:
                existing_pr = db.execute("SELECT id FROM purchase_returns WHERE user_id=? AND debit_note_no=?", (uid, pr.get('debit_note_no'))).fetchone()
                if not existing_pr:
                    cur_pr = db.execute("""INSERT INTO purchase_returns (debit_note_no, user_id, original_purchase_id, original_invoice_no,
                                           original_bill_date, supplier_name, supplier_phone, supplier_gstin, place_of_supply,
                                           is_interstate, return_date, reason, subtotal, cgst_amount, sgst_amount, igst_amount,
                                           total_amount, refund_mode, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                        (pr.get('debit_note_no'), uid, pr.get('original_purchase_id', 0), pr.get('original_invoice_no', ''),
                                         pr.get('original_bill_date', ''), pr.get('supplier_name', ''), pr.get('supplier_phone', ''),
                                         pr.get('supplier_gstin', ''), pr.get('place_of_supply', '07'), int(pr.get('is_interstate', 0)),
                                         pr.get('return_date', ''), pr.get('reason', ''), float(pr.get('subtotal', 0)),
                                         float(pr.get('cgst_amount', 0)), float(pr.get('sgst_amount', 0)), float(pr.get('igst_amount', 0)),
                                         float(pr.get('total_amount', 0)), pr.get('refund_mode', 'Debit Note'), pr.get('notes', '')))
                    new_pr_id = cur_pr.lastrowid
                    for prit in pr.get('items', []):
                        db.execute("""INSERT INTO purchase_return_items (return_id, original_item_id, product, hsn_code, qty, unit, price,
                                      taxable_amount, gst_pct, cgst_amount, sgst_amount, igst_amount, total_amount)
                                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (new_pr_id, prit.get('original_item_id', 0), prit.get('product', ''), prit.get('hsn_code', ''),
                                    float(prit.get('qty', 0)), prit.get('unit', 'Pcs'), float(prit.get('price', 0)),
                                    float(prit.get('taxable_amount', 0)), float(prit.get('gst_pct', 18)),
                                    float(prit.get('cgst_amount', 0)), float(prit.get('sgst_amount', 0)),
                                    float(prit.get('igst_amount', 0)), float(prit.get('total_amount', 0))))

        # Restore Shop Ledger Mappings
        if 'shop_ledger_mappings' in backup:
            for lm in backup['shop_ledger_mappings']:
                db.execute("""INSERT INTO shop_ledger_mappings (user_id, key_name, ledger_name, updated_at)
                              VALUES (?, ?, ?, datetime('now'))
                              ON CONFLICT(user_id, key_name) DO UPDATE SET ledger_name=excluded.ledger_name""",
                           (uid, lm.get('key_name'), lm.get('ledger_name')))

        db.commit()
        return jsonify({"success": True, "message": "Data restored successfully into database"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": f"Restore failed: {str(e)}"}), 500

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('.', 'sw.js', mimetype='application/javascript')

@app.route('/manifest.json')
def serve_manifest():
    import json
    manifest = {
        "name": "BillZen AI",
        "short_name": "BillZen AI",
        "description": "AI-Powered GST Billing for India",
        "start_url": "/dashboard",
        "display": "standalone",
        "background_color": "#0f0a1e",
        "theme_color": "#6d28d9",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }
    return jsonify(manifest)

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "app": "BillZen AI"})

# ─────────────────────────────────────────────────────────────
#  MASTER ADMIN (Client & Subscription Manager)
# ─────────────────────────────────────────────────────────────
@app.route('/api/master/shops', methods=['GET'])
@login_required
def master_get_shops():
    actual_uid = session.get('login_id') or session.get('user_id')
    curr_user = query("SELECT is_master_admin FROM users WHERE id=?", (actual_uid,), one=True)
    if not curr_user or not curr_user.get('is_master_admin'):
        return jsonify({"success": False, "error": "Master Admin only"}), 403

    shop_owners = rows("""
        SELECT u.id, u.name, u.username, u.role, u.plan_status, u.trial_ends_at, 
               u.subscription_ends_at, u.is_master_admin, u.created_at,
               s.name AS shop_name, s.phone AS shop_phone, s.gstin AS shop_gstin
        FROM users u
        LEFT JOIN shops s ON s.user_id = u.id
        WHERE u.admin_id IS NULL AND u.is_master_admin = 0
        ORDER BY u.created_at DESC
    """)
    for sh in shop_owners:
        sh['plan_info'] = get_user_plan_info(sh['id'])
        bill_cnt = query("SELECT COUNT(*) AS cnt, SUM(total_amount) AS revenue FROM sales WHERE user_id=? AND cancelled=0", (sh['id'],), one=True)
        sh['bill_count'] = (bill_cnt or {}).get('cnt', 0) or 0
        sh['total_revenue'] = (bill_cnt or {}).get('revenue', 0) or 0
    return jsonify({"success": True, "shops": shop_owners})

@app.route('/api/master/extend-plan', methods=['POST'])
@login_required
def master_extend_plan():
    actual_uid = session.get('login_id') or session.get('user_id')
    curr_user = query("SELECT is_master_admin FROM users WHERE id=?", (actual_uid,), one=True)
    if not curr_user or not curr_user.get('is_master_admin'):
        return jsonify({"success": False, "error": "Master Admin only"}), 403

    d = request.get_json(silent=True) or {}
    target_id = d.get('target_user_id')
    days = d.get('days')
    if not target_id:
        return jsonify({"success": False, "error": "Target user ID required"}), 400

    db = get_db()
    if str(days).lower() == 'lifetime':
        db.execute("UPDATE users SET plan_status='lifetime', subscription_ends_at='2099-12-31 23:59:59' WHERE id=?", (target_id,))
    else:
        try:
            num_days = int(days or 30)
        except Exception:
            num_days = 30
        new_end = (datetime.now() + timedelta(days=num_days)).strftime('%Y-%m-%d %H:%M:%S')
        db.execute("UPDATE users SET plan_status='active', subscription_ends_at=? WHERE id=?", (new_end, target_id))
    db.commit()
    return jsonify({"success": True, "message": f"Plan updated successfully for user #{target_id}"})

@app.route('/api/master/block-shop', methods=['POST'])
@login_required
def master_block_shop():
    actual_uid = session.get('login_id') or session.get('user_id')
    curr_user = query("SELECT is_master_admin FROM users WHERE id=?", (actual_uid,), one=True)
    if not curr_user or not curr_user.get('is_master_admin'):
        return jsonify({"success": False, "error": "Master Admin only"}), 403

    d = request.get_json(silent=True) or {}
    target_id = d.get('target_user_id')
    if not target_id:
        return jsonify({"success": False, "error": "Target user ID required"}), 400

    db = get_db()
    db.execute("UPDATE users SET plan_status='expired', subscription_ends_at='2000-01-01 00:00:00', trial_ends_at='2000-01-01 00:00:00' WHERE id=?", (target_id,))
    db.commit()
    return jsonify({"success": True, "message": f"Shop #{target_id} blocked."})

@app.route('/api/master/add-shop', methods=['POST'])
@login_required
def master_add_shop():
    actual_uid = session.get('login_id') or session.get('user_id')
    curr_user = query("SELECT is_master_admin FROM users WHERE id=?", (actual_uid,), one=True)
    if not curr_user or not curr_user.get('is_master_admin'):
        return jsonify({"success": False, "error": "Master Admin only"}), 403

    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    username = (d.get('username') or '').strip()
    password = (d.get('password') or '').strip()
    shop_name = (d.get('shop_name') or name + ' Store').strip()
    phone = (d.get('phone') or '').strip()
    gstin = (d.get('gstin') or '').strip()
    plan = (d.get('plan') or 'trial').strip().lower()
    state = (d.get('state') or 'DELHI').strip()

    if not name or not username or not password:
        return jsonify({"success": False, "error": "Name, username, and password are required"}), 400
    if len(password) < 4:
        return jsonify({"success": False, "error": "Password must be at least 4 characters"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        return jsonify({"success": False, "error": f"Username '{username}' already exists. Please choose a different username."}), 409

    trial_end = ''
    sub_end = ''
    plan_status = 'trial'

    if plan == 'lifetime':
        plan_status = 'lifetime'
        sub_end = '2099-12-31 23:59:59'
    elif plan in ('30', 'monthly'):
        plan_status = 'active'
        sub_end = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    elif plan in ('365', 'yearly', 'annual'):
        plan_status = 'active'
        sub_end = (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%d %H:%M:%S')
    else:
        plan_status = 'trial'
        trial_end = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

    cur = db.execute(
        """INSERT INTO users (name, username, password, role, plan_status, trial_ends_at, subscription_ends_at, is_master_admin) 
           VALUES (?, ?, ?, 'admin', ?, ?, ?, 0)""",
        (name, username, generate_password_hash(password), plan_status, trial_end, sub_end)
    )
    new_user_id = cur.lastrowid

    db.execute(
        "INSERT INTO shops (user_id, name, phone, gstin, state, state_code) VALUES (?, ?, ?, ?, ?, ?)",
        (new_user_id, shop_name, phone, gstin, state, state[:2])
    )

    for u in ['Pcs', 'Kg', 'Ltr', 'Box', 'Meter', 'Pack']:
        db.execute("INSERT INTO units (user_id, name) VALUES (?, ?)", (new_user_id, u))
    for c in ['General', 'Grocery', 'Electronics', 'Clothing', 'Services']:
        db.execute("INSERT INTO categories (user_id, name) VALUES (?, ?)", (new_user_id, c))

    db.commit()

    return jsonify({
        "success": True,
        "message": f"Shop '{shop_name}' created successfully!",
        "client": {
            "id": new_user_id,
            "name": name,
            "username": username,
            "shop_name": shop_name,
            "plan_status": plan_status
        }
    }), 201

# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    init_db()
    port = int(os.environ.get('PORT', 5000))
    print("\n" + "="*50)
    print("  BillZen AI - GST Billing Software")
    print(f"  URL      : http://localhost:{port}")
    print("  Login    : Use an existing administrator account.")
    print(f"  Database : {DATABASE}")
    print("="*50 + "\n")
    app.run(debug=False, host='0.0.0.0', port=port)
