import os
import tempfile
import unittest
import gc
from werkzeug.security import generate_password_hash, check_password_hash

import app as billzen


class MasterClientManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        for key in ['BILLZEN_BOOTSTRAP_ADMIN_NAME', 'BILLZEN_BOOTSTRAP_ADMIN_USERNAME', 'BILLZEN_BOOTSTRAP_ADMIN_PASSWORD']:
            os.environ.pop(key, None)
        billzen.DATABASE = os.path.join(self.tempdir.name, 'test_master.db')
        billzen.init_db()
        db = billzen.get_db()
        db.execute("DELETE FROM users WHERE username IN ('admin', 'master', 'client_a')")
        # Create Master Admin
        db.execute("INSERT INTO users (id, name, username, password, role, is_master_admin, plan_status) VALUES (?,?,?,?,?,?,?)",
                   (1, 'Master Admin', 'master', generate_password_hash('masterpass123'), 'admin', 1, 'lifetime'))
        # Create Client Shop Owner (no plaintext password stored)
        db.execute("INSERT INTO users (id, name, username, password, plain_password, role, is_master_admin, plan_status, subscription_ends_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (2, 'Shopkeeper Ram', 'client_ram', generate_password_hash('rampass123'), '', 'admin', 0, 'active', '2027-01-01 00:00:00'))
        db.execute("INSERT INTO shops (user_id, name, phone, gstin, state, state_code, address, email) VALUES (?,?,?,?,?,?,?,?)",
                   (2, 'Ram Electronics', '9876543210', '07AAAAA0000A1Z5', 'DELHI', '07', 'Shop 5, Chandni Chowk', 'ram@example.com'))
        db.commit()
        db.close()
        self.client = billzen.app.test_client()

    def tearDown(self):
        self.client = None
        gc.collect()
        self.tempdir.cleanup()

    def login_master(self):
        res = self.client.post('/api/login', json={'username': 'master', 'password': 'masterpass123'})
        self.assertEqual(res.status_code, 200)
        return {'X-CSRF-Token': res.get_json()['csrf_token']}

    def login_client(self, password='rampass123'):
        res = self.client.post('/api/login', json={'username': 'client_ram', 'password': password})
        return res

    def test_security_guards_non_master(self):
        # 1. Non-master cannot access master endpoints
        res = self.login_client()
        self.assertEqual(res.status_code, 200)
        client_headers = {'X-CSRF-Token': res.get_json()['csrf_token']}

        self.assertEqual(self.client.get('/api/master/shops').status_code, 403)
        self.assertEqual(self.client.post('/api/master/toggle-status', headers=client_headers, json={'target_user_id': 2}).status_code, 403)
        self.assertEqual(self.client.post('/api/master/reset-password', headers=client_headers, json={'target_user_id': 2, 'new_password': '123'}).status_code, 403)
        self.assertEqual(self.client.post('/api/master/update-shop', headers=client_headers, json={'target_user_id': 2, 'name': 'Hacker'}).status_code, 403)

    def test_unauthenticated_dashboard_redirects_and_apis_return_401(self):
        """Verify unauthenticated requests cannot access dashboard or protected business APIs."""
        # 1. /dashboard must redirect to /login
        res = self.client.get('/dashboard')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login', res.headers.get('Location', ''))

        # 2. Protected APIs must return 401 Unauthorized
        self.assertEqual(self.client.get('/api/reports/dashboard').status_code, 401)
        self.assertEqual(self.client.get('/api/sales').status_code, 401)
        self.assertEqual(self.client.get('/api/purchases').status_code, 401)
        self.assertEqual(self.client.get('/api/shop').status_code, 401)
        self.assertEqual(self.client.get('/api/customers').status_code, 401)
        self.assertEqual(self.client.get('/api/gst/drilldown').status_code, 401)

        # 3. /api/me returns loggedIn: False
        res_me = self.client.get('/api/me')
        self.assertEqual(res_me.status_code, 200)
        self.assertFalse(res_me.get_json().get('loggedIn'))

    def test_master_get_shops_never_exposes_passwords(self):
        master_headers = self.login_master()
        res = self.client.get('/api/master/shops', headers=master_headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        shops = data['shops']
        self.assertEqual(len(shops), 1)
        client = shops[0]
        self.assertEqual(client['username'], 'client_ram')
        self.assertNotIn('plain_password', client)
        self.assertNotIn('password', client)
        self.assertEqual(client['shop_name'], 'Ram Electronics')
        self.assertEqual(client['shop_phone'], '9876543210')
        self.assertEqual(client['shop_address'], 'Shop 5, Chandni Chowk')

    def test_suspend_and_resume_subscription(self):
        master_headers = self.login_master()
        # Suspend client
        res = self.client.post('/api/master/toggle-status', headers=master_headers, json={'target_user_id': 2, 'action': 'suspend'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['plan_status'], 'suspended')

        # Suspended client cannot log in
        login_res = self.login_client()
        self.assertEqual(login_res.status_code, 403)
        self.assertIn('stopped / suspended', login_res.get_json()['error'])

        # Reactivate client
        res_act = self.client.post('/api/master/toggle-status', headers=master_headers, json={'target_user_id': 2, 'action': 'activate'})
        self.assertEqual(res_act.status_code, 200)
        self.assertEqual(res_act.get_json()['plan_status'], 'active')

        # Reactivated client can log in again
        login_res2 = self.login_client()
        self.assertEqual(login_res2.status_code, 200)

    def test_reset_password_flow(self):
        master_headers = self.login_master()
        new_pass = 'newSecretPass789'
        res = self.client.post('/api/master/reset-password', headers=master_headers, json={'target_user_id': 2, 'new_password': new_pass})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertNotIn('new_password', data)

        # Database check: plain_password MUST be empty, password MUST be securely hashed
        user = billzen.query("SELECT password, plain_password FROM users WHERE id=2", one=True)
        self.assertEqual(user['plain_password'], '')
        self.assertTrue(check_password_hash(user['password'], new_pass))

        # Old password fails
        old_login = self.login_client('rampass123')
        self.assertEqual(old_login.status_code, 401)

        # New password succeeds
        new_login = self.login_client(new_pass)
        self.assertEqual(new_login.status_code, 200)

        # Re-login as master to inspect shops list
        master_headers = self.login_master()
        shops_res = self.client.get('/api/master/shops', headers=master_headers)
        self.assertEqual(shops_res.status_code, 200)
        client = shops_res.get_json()['shops'][0]
        self.assertNotIn('plain_password', client)
        self.assertNotIn('password', client)

    def test_add_shop_never_stores_or_returns_plain_password(self):
        master_headers = self.login_master()
        new_shop_data = {
            'name': 'Pooja Sharma',
            'username': 'client_pooja',
            'password': 'PoojaPassword123!',
            'shop_name': 'Pooja Sarees',
            'phone': '9811122233',
            'plan': 'monthly'
        }
        res = self.client.post('/api/master/add-shop', headers=master_headers, json=new_shop_data)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertNotIn('password', data.get('client', {}))
        self.assertNotIn('plain_password', data.get('client', {}))

        # Database verification: plain_password MUST be empty, password MUST be securely hashed
        user = billzen.query("SELECT password, plain_password FROM users WHERE username='client_pooja'", one=True)
        self.assertEqual(user['plain_password'], '')
        self.assertTrue(check_password_hash(user['password'], 'PoojaPassword123!'))

        # Verify new shop owner can log in with new password
        login_res = self.client.post('/api/login', json={'username': 'client_pooja', 'password': 'PoojaPassword123!'})
        self.assertEqual(login_res.status_code, 200)

    def test_edit_client_details(self):
        master_headers = self.login_master()
        edit_payload = {
            'target_user_id': 2,
            'name': 'Ramesh Kumar',
            'shop_name': 'Ram Digital World',
            'phone': '9999988888',
            'gstin': '07BBBBB1111B1Z2',
            'state': 'DELHI',
            'address': 'Plot 42, Okhla Phase 2',
            'email': 'ramesh@digital.com'
        }
        res = self.client.post('/api/master/update-shop', headers=master_headers, json=edit_payload)
        self.assertEqual(res.status_code, 200)

        # Verify updated values in /api/master/shops
        shops_res = self.client.get('/api/master/shops', headers=master_headers)
        client = shops_res.get_json()['shops'][0]
        self.assertEqual(client['name'], 'Ramesh Kumar')
        self.assertEqual(client['shop_name'], 'Ram Digital World')
        self.assertEqual(client['shop_phone'], '9999988888')
        self.assertEqual(client['shop_gstin'], '07BBBBB1111B1Z2')
        self.assertEqual(client['shop_address'], 'Plot 42, Okhla Phase 2')
        self.assertEqual(client['shop_email'], 'ramesh@digital.com')


if __name__ == '__main__':
    unittest.main()
