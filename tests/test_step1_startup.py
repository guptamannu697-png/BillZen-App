import os
import tempfile
import unittest
import sqlite3
import threading
from werkzeug.security import generate_password_hash, check_password_hash

import app as billzen

def _mp_worker_task(db_path, barrier, queue, env_vars=None):
    try:
        import os
        import app as bz
        bz.DATABASE = db_path
        if env_vars:
            for k, v in env_vars.items():
                os.environ[k] = v
        barrier.wait()
        bz.ensure_database_ready()
        queue.put(('success', None))
    except Exception as e:
        queue.put(('error', f"{type(e).__name__}: {e}"))

class Step1StartupSecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.test_db = os.path.join(self.tempdir.name, 'step1_test.db')
        self.orig_db = billzen.DATABASE
        billzen.DATABASE = self.test_db
        # Clean bootstrap environment variables
        for k in ['BILLZEN_BOOTSTRAP_ADMIN_USERNAME', 'BILLZEN_BOOTSTRAP_ADMIN_PASSWORD', 'BILLZEN_BOOTSTRAP_ADMIN_NAME']:
            os.environ.pop(k, None)

    def tearDown(self):
        billzen.DATABASE = self.orig_db
        for k in ['BILLZEN_BOOTSTRAP_ADMIN_USERNAME', 'BILLZEN_BOOTSTRAP_ADMIN_PASSWORD', 'BILLZEN_BOOTSTRAP_ADMIN_NAME']:
            os.environ.pop(k, None)
        import gc
        gc.collect()
        try:
            self.tempdir.cleanup()
        except Exception:
            pass

    def test_fresh_database_auto_initialization(self):
        """Verify that a fresh database is automatically and cleanly initialized with all 25 tables."""
        self.assertFalse(os.path.exists(self.test_db))
        billzen.ensure_database_ready()
        self.assertTrue(os.path.exists(self.test_db))

        # Check all 25 application core tables exist
        conn = sqlite3.connect(self.test_db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = set(r[0] for r in cur.fetchall())
        conn.close()

        self.assertTrue(billzen.REQUIRED_CORE_TABLES.issubset(tables))
        self.assertIn('sales_returns', tables)
        self.assertIn('purchase_returns', tables)
        self.assertIn('quotations', tables)
        self.assertIn('delivery_challans', tables)
        self.assertIn('customer_payments', tables)
        self.assertIn('stock_adjustments', tables)
        self.assertIn('shop_ledger_mappings', tables)
        self.assertIn('tax_rules', tables)

        # Verify /api/setup-status returns HTTP 200 with has_users=False
        client = billzen.app.test_client()
        res = client.get('/api/setup-status')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertFalse(data['has_users'])

    def test_incomplete_database_fails_safely_with_explicit_error(self):
        """Verify that a partially initialized / corrupt database halts boot with clear error."""
        # Create a database with only 2 tables
        conn = sqlite3.connect(self.test_db)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE shops (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()

        with self.assertRaises(RuntimeError) as ctx:
            billzen.ensure_database_ready()

        self.assertIn("incomplete or corrupt", str(ctx.exception))
        self.assertIn("Missing required tables", str(ctx.exception))

    def test_incomplete_database_missing_features_tables(self):
        """Verify that missing feature tables (returns, quotations, challans) are caught by health check."""
        conn = sqlite3.connect(self.test_db)
        # Create basic tables but omit feature tables like sales_returns and quotations
        for t in ['users', 'shops', 'units', 'categories', 'products', 'customers', 'suppliers', 'sales', 'sale_items', 'purchases', 'purchase_items', 'tax_rules']:
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        with self.assertRaises(RuntimeError) as ctx:
            billzen.ensure_database_ready()

        err_msg = str(ctx.exception)
        self.assertIn("sales_returns", err_msg)
        self.assertIn("quotations", err_msg)
        self.assertIn("delivery_challans", err_msg)
        self.assertIn("shop_ledger_mappings", err_msg)

    def test_existing_healthy_database_untouched(self):
        """Verify that an existing healthy database is not modified on ensure_database_ready."""
        billzen.ensure_database_ready()
        
        # Insert a sentinel record
        conn = sqlite3.connect(self.test_db)
        conn.execute("INSERT INTO users (name, username, password, is_master_admin) VALUES ('Custom', 'custom1', 'hash', 0)")
        conn.commit()
        conn.close()

        # Re-run ensure_database_ready
        billzen.ensure_database_ready()

        conn = sqlite3.connect(self.test_db)
        user = conn.execute("SELECT is_master_admin FROM users WHERE username='custom1'").fetchone()
        conn.close()
        self.assertEqual(user[0], 0, "Existing user must not be mutated on boot!")

    def test_concurrent_database_initialization(self):
        """Verify that multiple simultaneous worker threads initialize cleanly with a synchronization barrier."""
        self.assertFalse(os.path.exists(self.test_db))
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_USERNAME'] = 'thread_admin@billzen.com'
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_PASSWORD'] = 'ThreadPass1234!'
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_NAME'] = 'Thread Root Admin'

        barrier = threading.Barrier(4)
        exceptions = []

        def worker_task():
            try:
                barrier.wait()
                billzen.ensure_database_ready()
            except Exception as e:
                exceptions.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker_task) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(exceptions, [], f"Concurrent workers encountered exceptions: {exceptions}")

        # Check resulting database is 100% complete
        conn = sqlite3.connect(self.test_db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = set(r[0] for r in cur.fetchall())
        self.assertTrue(billzen.REQUIRED_CORE_TABLES.issubset(tables))

        # Check bootstrap admin created exactly once
        cur.execute("SELECT COUNT(*) FROM users WHERE username='thread_admin@billzen.com'")
        self.assertEqual(cur.fetchone()[0], 1)

        # Check tax rules created exactly once
        cur.execute("SELECT COUNT(*) FROM tax_rules")
        self.assertEqual(cur.fetchone()[0], 5)
        conn.close()

    def test_multiprocess_concurrent_database_initialization(self):
        """Verify that multiple simultaneous OS processes initialize cleanly and execute setup exactly once."""
        import multiprocessing
        self.assertFalse(os.path.exists(self.test_db))

        env_vars = {
            'BILLZEN_BOOTSTRAP_ADMIN_USERNAME': 'mp_admin@billzen.com',
            'BILLZEN_BOOTSTRAP_ADMIN_PASSWORD': 'MpPassword1234!',
            'BILLZEN_BOOTSTRAP_ADMIN_NAME': 'MP Root Admin'
        }

        barrier = multiprocessing.Barrier(4)
        queue = multiprocessing.Queue()

        procs = [
            multiprocessing.Process(
                target=_mp_worker_task,
                args=(self.test_db, barrier, queue, env_vars)
            )
            for _ in range(4)
        ]

        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)

        # Collect results
        results = []
        while not queue.empty():
            results.append(queue.get())

        errors = [r for r in results if r[0] == 'error']
        self.assertEqual(errors, [], f"Multi-process workers failed: {errors}")
        self.assertEqual(len(results), 4, f"Expected 4 results, got {len(results)}")

        # Verify database is complete
        conn = sqlite3.connect(self.test_db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = set(r[0] for r in cur.fetchall())
        self.assertTrue(billzen.REQUIRED_CORE_TABLES.issubset(tables))

        # Verify bootstrap admin was created exactly ONCE
        cur.execute("SELECT COUNT(*) FROM users WHERE username='mp_admin@billzen.com'")
        user_count = cur.fetchone()[0]
        self.assertEqual(user_count, 1, f"Expected exactly 1 bootstrap admin, got {user_count}")

        # Verify tax rules seeded exactly ONCE (5 rules)
        cur.execute("SELECT COUNT(*) FROM tax_rules")
        rule_count = cur.fetchone()[0]
        self.assertEqual(rule_count, 5, f"Expected exactly 5 tax rules, got {rule_count}")
        conn.close()

    def test_public_registration_never_grants_master_admin(self):
        """Verify that public registration strictly creates ordinary trial shopkeepers."""
        billzen.ensure_database_ready()
        client = billzen.app.test_client()

        res = client.post('/api/register', json={
            'name': 'Public Merchant',
            'username': 'merchant_public',
            'password': 'password123',
            'shop_name': 'My Shop'
        })
        self.assertEqual(res.status_code, 201)

        conn = sqlite3.connect(self.test_db)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE username='merchant_public'").fetchone()
        conn.close()

        self.assertIsNotNone(user)
        self.assertEqual(user['is_master_admin'], 0)
        self.assertEqual(user['plan_status'], 'trial')
        self.assertEqual(user['plain_password'], '')
        self.assertTrue(check_password_hash(user['password'], 'password123'))

    def test_secure_bootstrap_admin_creation(self):
        """Verify bootstrap admin creation from environment credentials with hashed password."""
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_USERNAME'] = 'secadmin@billzen.com'
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_PASSWORD'] = 'SuperSecret1234!'
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_NAME'] = 'Head Admin'

        billzen.ensure_database_ready()

        conn = sqlite3.connect(self.test_db)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE username='secadmin@billzen.com'").fetchone()
        shop = conn.execute("SELECT * FROM shops WHERE user_id=?", (user['id'],)).fetchone()
        conn.close()

        self.assertIsNotNone(user)
        self.assertEqual(user['is_master_admin'], 1)
        self.assertEqual(user['plan_status'], 'lifetime')
        self.assertEqual(user['plain_password'], '')
        self.assertTrue(check_password_hash(user['password'], 'SuperSecret1234!'))
        self.assertIsNotNone(shop)

        # Test login succeeds with bootstrap credentials
        client = billzen.app.test_client()
        res = client.post('/api/login', json={
            'username': 'secadmin@billzen.com',
            'password': 'SuperSecret1234!'
        })
        self.assertEqual(res.status_code, 200)

    def test_bootstrap_never_elevates_existing_ordinary_user(self):
        """Verify that an existing ordinary user is NEVER elevated even if username matches env var."""
        billzen.ensure_database_ready()
        
        # Create ordinary user first
        conn = sqlite3.connect(self.test_db)
        conn.execute("INSERT INTO users (name, username, password, is_master_admin, plan_status) VALUES ('Regular', 'ordinary_joe', 'hash123', 0, 'trial')")
        conn.commit()
        conn.close()

        # Set env credentials matching ordinary_joe
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_USERNAME'] = 'ordinary_joe'
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_PASSWORD'] = 'NewPassword123!'

        conn = sqlite3.connect(self.test_db)
        conn.row_factory = sqlite3.Row
        billzen.bootstrap_admin_account(conn)
        conn.close()

        # Verify ordinary_joe is STILL ordinary and password was NOT changed
        conn = sqlite3.connect(self.test_db)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE username='ordinary_joe'").fetchone()
        conn.close()

        self.assertEqual(user['is_master_admin'], 0, "Ordinary user must NEVER be automatically elevated!")
        self.assertEqual(user['password'], 'hash123', "Existing password must NOT be overwritten!")

    def test_short_bootstrap_password_rejected(self):
        """Verify that passwords shorter than 8 characters are rejected for bootstrap admin."""
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_USERNAME'] = 'short_admin'
        os.environ['BILLZEN_BOOTSTRAP_ADMIN_PASSWORD'] = 'short'

        billzen.ensure_database_ready()

        conn = sqlite3.connect(self.test_db)
        user = conn.execute("SELECT * FROM users WHERE username='short_admin'").fetchone()
        conn.close()
        self.assertIsNone(user, "Bootstrap user with weak password (<8 chars) must not be created!")


if __name__ == '__main__':
    unittest.main()
