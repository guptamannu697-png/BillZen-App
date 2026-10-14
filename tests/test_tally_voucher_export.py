import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.abspath('.'))

import app as billzen
from werkzeug.security import generate_password_hash

class TallyVoucherExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        billzen.DATABASE = 'test_tally_vch.db'
        if os.path.exists('test_tally_vch.db'):
            try: os.remove('test_tally_vch.db')
            except: pass
        billzen.init_db()
        billzen.migrate_db()

        db = billzen.get_db()
        db.execute("DELETE FROM users WHERE username = 'tally_tester'")
        db.execute("INSERT INTO users (id, name, username, password, plain_password, role) VALUES (?, ?, ?, ?, ?, ?)",
                   (88, 'Tally Tester', 'tally_tester', generate_password_hash('testpass123'), '', 'admin'))
        
        # User 89 (Other tenant for isolation test)
        db.execute("DELETE FROM users WHERE username = 'other_tenant'")
        db.execute("INSERT INTO users (id, name, username, password, plain_password, role) VALUES (?, ?, ?, ?, ?, ?)",
                   (89, 'Other Tenant', 'other_tenant', generate_password_hash('testpass123'), '', 'admin'))

        # Seed products for User 88
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, unit, stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (301, 88, 'Item Alpha', '8471', 'Pcs', 100, 100, 200, 18))
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, unit, stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (302, 88, 'Item Beta', '8472', 'Pcs', 100, 150, 300, 18))
        
        # Seed product for User 89
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, unit, stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (303, 89, 'Tenant89 Item', '8473', 'Pcs', 50, 50, 100, 18))

        db.commit()
        db.close()

        cls.client = billzen.app.test_client()
        # Login User 88
        resp = cls.client.post('/api/login', json={'username': 'tally_tester', 'password': 'testpass123'})
        assert resp.status_code == 200
        cls.csrf = resp.get_json()['csrf_token']
        cls.headers = {'X-CSRF-Token': cls.csrf}

        # Create 3 sales for User 88 across different dates
        s1 = cls.client.post('/api/sales', headers=cls.headers, json={
            'customer_name': 'Client One',
            'sale_date': '2026-04-10',
            'items': [{'product': 'Item Alpha', 'qty': 2, 'price': 200, 'gst_pct': 18}],
            'paid_amount': 472
        })
        cls.bill1 = s1.get_json()['bill_id']

        s2 = cls.client.post('/api/sales', headers=cls.headers, json={
            'customer_name': 'Client Two',
            'sale_date': '2026-05-15',
            'items': [{'product': 'Item Beta', 'qty': 1, 'price': 300, 'gst_pct': 18}],
            'paid_amount': 354
        })
        cls.bill2 = s2.get_json()['bill_id']

        s3 = cls.client.post('/api/sales', headers=cls.headers, json={
            'customer_name': 'Client Three',
            'sale_date': '2026-10-04',
            'items': [{'product': 'Item Alpha', 'qty': 1, 'price': 200, 'gst_pct': 18}],
            'paid_amount': 236
        })
        cls.bill3 = s3.get_json()['bill_id']

        # Create 1 sale for User 89 (Other Tenant)
        u89_login = cls.client.post('/api/login', json={'username': 'other_tenant', 'password': 'testpass123'})
        u89_headers = {'X-CSRF-Token': u89_login.get_json()['csrf_token']}
        s_other = cls.client.post('/api/sales', headers=u89_headers, json={
            'customer_name': 'Other Client',
            'items': [{'product': 'Tenant89 Item', 'qty': 1, 'price': 100, 'gst_pct': 18}],
            'paid_amount': 118
        })
        cls.other_bill = s_other.get_json()['bill_id']

        # Re-login User 88
        resp = cls.client.post('/api/login', json={'username': 'tally_tester', 'password': 'testpass123'})
        cls.csrf = resp.get_json()['csrf_token']
        cls.headers = {'X-CSRF-Token': cls.csrf}

    @classmethod
    def tearDownClass(cls):
        if os.path.exists('test_tally_vch.db'):
            try: os.remove('test_tally_vch.db')
            except: pass

    # TEST 1: Single selected Sales voucher produces EXACTLY ONE TALLYMESSAGE and VOUCHER
    def test_01_single_selected_sales_voucher_produces_exactly_one_voucher(self):
        # We simulate the exact frontend call from October, exporting an April bill
        payload = {
            'voucher_type': 'sales',
            'category': 'all',
            'from_date': '2026-10-01',  # Notice date range is October
            'to_date': '2026-10-31',
            'selected_sales': [self.bill1],  # But bill1 is from April 2026!
            'selected_purchases': []
        }
        res = self.client.post('/api/tally/export/v2', headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 200)
        xml_text = res.data.decode('utf-8')
        
        # Verify XML envelope and REQUESTDATA
        self.assertIn('<REQUESTDATA>', xml_text)
        self.assertIn('</REQUESTDATA>', xml_text)
        
        # Count vouchers
        tally_msg_count = xml_text.count('<TALLYMESSAGE')
        voucher_count = xml_text.count('<VOUCHER ')
        self.assertEqual(tally_msg_count, 1, f"Expected 1 TALLYMESSAGE, got {tally_msg_count}")
        self.assertEqual(voucher_count, 1, f"Expected 1 VOUCHER, got {voucher_count}")
        
        # Verify voucher references the exact selected bill
        self.assertIn(f'<REFERENCE>{self.bill1}</REFERENCE>', xml_text)
        self.assertNotIn(self.bill2, xml_text)
        self.assertNotIn(self.bill3, xml_text)

        # Parse with ElementTree to verify valid XML tree structure
        root = ET.fromstring(res.data)
        req_data = root.find('.//REQUESTDATA')
        self.assertIsNotNone(req_data)
        vouchers = req_data.findall('.//VOUCHER')
        self.assertEqual(len(vouchers), 1)
        self.assertEqual(vouchers[0].attrib.get('VCHTYPE'), 'Sales')

    # TEST 2: Multiple selected Sales vouchers produce the correct number of vouchers
    def test_02_multiple_selected_sales_vouchers_produce_correct_count(self):
        # Select 2 bills (bill1 from April, bill2 from May)
        payload = {
            'voucher_type': 'sales',
            'category': 'all',
            'selected_sales': [self.bill1, self.bill2],
            'selected_purchases': []
        }
        res = self.client.post('/api/tally/export/v2', headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 200)
        xml_text = res.data.decode('utf-8')

        tally_msg_count = xml_text.count('<TALLYMESSAGE')
        voucher_count = xml_text.count('<VOUCHER ')
        self.assertEqual(tally_msg_count, 2, f"Expected 2 TALLYMESSAGE, got {tally_msg_count}")
        self.assertEqual(voucher_count, 2, f"Expected 2 VOUCHER, got {voucher_count}")
        self.assertIn(self.bill1, xml_text)
        self.assertIn(self.bill2, xml_text)
        self.assertNotIn(self.bill3, xml_text)

        # Select all 3 bills
        payload3 = {
            'voucher_type': 'sales',
            'category': 'all',
            'selected_sales': [self.bill1, self.bill2, self.bill3]
        }
        res3 = self.client.post('/api/tally/export/v2', headers=self.headers, json=payload3)
        self.assertEqual(res3.status_code, 200)
        xml_text3 = res3.data.decode('utf-8')
        self.assertEqual(xml_text3.count('<VOUCHER '), 3)

    # TEST 3: Zero matching vouchers returns a clear error instead of empty successful XML
    def test_03_zero_matching_vouchers_returns_clear_error(self):
        # Case A: Explicit non-existent bill IDs
        payload_non_existent = {
            'voucher_type': 'sales',
            'category': 'all',
            'selected_sales': ['NON_EXISTENT_BILL_9999'],
            'selected_purchases': []
        }
        res_a = self.client.post('/api/tally/export/v2', headers=self.headers, json=payload_non_existent)
        self.assertEqual(res_a.status_code, 400, "Must return HTTP 400 error when zero matching vouchers found")
        data_a = res_a.get_json()
        self.assertFalse(data_a.get('success'))
        self.assertIn('None of the selected vouchers could be found', data_a.get('error', ''))

        # Case B: Date range filter with zero vouchers
        payload_empty_range = {
            'voucher_type': 'sales',
            'category': 'all',
            'from_date': '1999-01-01',
            'to_date': '1999-12-31'
        }
        res_b = self.client.post('/api/tally/export/v2', headers=self.headers, json=payload_empty_range)
        self.assertEqual(res_b.status_code, 400)
        data_b = res_b.get_json()
        self.assertFalse(data_b.get('success'))
        self.assertIn('No vouchers found matching', data_b.get('error', ''))

    # TEST 4: Tenant isolation — cannot export other tenant's voucher
    def test_04_tenant_isolation_prevents_unauthorized_voucher_export(self):
        # User 88 tries to export User 89's voucher
        payload = {
            'voucher_type': 'sales',
            'category': 'all',
            'selected_sales': [self.other_bill]
        }
        res = self.client.post('/api/tally/export/v2', headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json().get('success'))

if __name__ == '__main__':
    unittest.main()
