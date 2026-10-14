import os
import sys
import unittest
import json
import xml.etree.ElementTree as ET

# Ensure working directory
sys.path.insert(0, os.path.abspath('.'))

import app as billzen
from werkzeug.security import generate_password_hash

class ManualTestingFixesVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        billzen.DATABASE = 'test_manual_fixes.db'
        if os.path.exists('test_manual_fixes.db'):
            try: os.remove('test_manual_fixes.db')
            except: pass
        billzen.init_db()
        billzen.migrate_db()

        db = billzen.get_db()
        # Seed test user 1
        db.execute("DELETE FROM users WHERE username = 'manual_tester'")
        db.execute("INSERT INTO users (id, name, username, password, plain_password, role) VALUES (?, ?, ?, ?, ?, ?)",
                   (77, 'Manual Tester', 'manual_tester', generate_password_hash('testerpass123'), '', 'admin'))
        
        # Seed test user 2 (for multi-tenant credit note collision test)
        db.execute("DELETE FROM users WHERE username = 'tenant_two'")
        db.execute("INSERT INTO users (id, name, username, password, plain_password, role) VALUES (?, ?, ?, ?, ?, ?)",
                   (78, 'Tenant Two', 'tenant_two', generate_password_hash('testerpass123'), '', 'admin'))

        # Seed products for user 77
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, unit, stock, low_stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (201, 77, 'Discrete Item Box', '8471', 'Pcs', 10, 2, 100, 200, 18))
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, unit, stock, low_stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (202, 77, 'Continuous Item Bulk', '8472', 'Kg', 100.0, 5, 50, 80, 18))

        # Seed products for user 78
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, unit, stock, low_stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (203, 78, 'Tenant2 Widget', '8473', 'Pcs', 20, 2, 50, 100, 18))

        db.commit()
        db.close()

        cls.client = billzen.app.test_client()
        # Login user 1
        resp = cls.client.post('/api/login', json={'username': 'manual_tester', 'password': 'testerpass123'})
        assert resp.status_code == 200, f"Login user 1 failed: {resp.data}"
        cls.csrf = resp.get_json()['csrf_token']
        cls.headers = {'X-CSRF-Token': cls.csrf}

    @classmethod
    def tearDownClass(cls):
        if os.path.exists('test_manual_fixes.db'):
            try: os.remove('test_manual_fixes.db')
            except: pass

    # 1. TALLY XML DOWNLOAD V2 (HEADERS, CSRF, CONTENT-TYPE, CONTENT-DISPOSITION, XML VALIDITY)
    def test_01_tally_xml_download(self):
        # Create a sale first
        s_res = self.client.post('/api/sales', headers=self.headers, json={
            'customer_name': 'Tally Export Test Co',
            'customer_gstin': '29ABCDE1234F1Z5',
            'items': [{'product': 'Discrete Item Box', 'qty': 2, 'price': 200, 'gst_pct': 18, 'cgst_pct': 9, 'sgst_pct': 9}],
            'paid_amount': 472
        })
        self.assertIn(s_res.status_code, [200, 201])
        bill_id = s_res.get_json()['bill_id']

        # Download XML via /api/tally/export/v2
        payload = {
            'sales_ids': [bill_id],
            'category': 'all'
        }
        res = self.client.post('/api/tally/export/v2', headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertIn('application/xml', res.headers.get('Content-Type', ''))
        cd = res.headers.get('Content-Disposition', '')
        self.assertTrue('attachment;' in cd and 'filename=' in cd and '.xml' in cd)
        self.assertIn('Content-Disposition', res.headers.get('Access-Control-Expose-Headers', ''))
        
        # Verify XML structure
        root = ET.fromstring(res.data)
        self.assertEqual(root.tag, 'ENVELOPE')
        body = root.find('BODY')
        self.assertIsNotNone(body)

    # 2. PURCHASE RETURN FETCH (SLASHES IN INVOICE & INTERNAL ID)
    def test_02_purchase_return_fetch(self):
        # Add purchase with slash in supplier invoice no
        p_res = self.client.post('/api/purchases', headers=self.headers, json={
            'supplier_name': 'Unicom Services 25-26',
            'supplier_gstin': '29AMEPK5421C1ZA',
            'invoice_no': 'UGST76-2/171',
            'date': '2025-04-13',
            'items': [{
                'product': 'TEACHMINT XLarge Format Display 65 NEOTINTERACTIVE',
                'qty': 5,
                'buy_price': 96000,
                'gst_pct': 18,
                'unit': 'Pcs',
                'hsn_code': '8471'
            }]
        })
        self.assertIn(p_res.status_code, [200, 201])
        p_data = p_res.get_json()
        purchase_id = p_data.get('purchase_id') or p_data.get('id')
        self.assertIsNotNone(purchase_id)

        # Test Fetch via query param ?ref=UGST76-2/171
        ctx1 = self.client.get('/api/purchases/return-context?ref=UGST76-2/171', headers=self.headers)
        self.assertEqual(ctx1.status_code, 200)
        ctx1_data = ctx1.get_json()
        self.assertTrue(ctx1_data.get('success'))
        self.assertEqual(ctx1_data['purchase']['invoice_no'], 'UGST76-2/171')
        self.assertEqual(len(ctx1_data['items']), 1)

        # Test Fetch via query param ?ref=<purchase_id>
        ctx2 = self.client.get(f'/api/purchases/return-context?ref={purchase_id}', headers=self.headers)
        self.assertEqual(ctx2.status_code, 200)
        self.assertTrue(ctx2.get_json().get('success'))

        # Test Fetch via path with slash: /api/purchases/UGST76-2/171/return-context
        ctx3 = self.client.get('/api/purchases/UGST76-2/171/return-context', headers=self.headers)
        self.assertEqual(ctx3.status_code, 200)
        self.assertTrue(ctx3.get_json().get('success'))

    # 3. SALES RETURN SERVER ERROR & UNIQUE NOTE NUMBERING COLLISION-PROOF
    def test_03_sales_return_creation_and_unique_numbering(self):
        # Record initial stock before sale
        init_stock = billzen.query("SELECT stock FROM products WHERE id=201", one=True)['stock']

        # Create sale
        s_res = self.client.post('/api/sales', headers=self.headers, json={
            'customer_name': 'Return Client A',
            'items': [{'product': 'Discrete Item Box', 'qty': 5, 'price': 200, 'gst_pct': 18}],
            'paid_amount': 1180
        })
        self.assertIn(s_res.status_code, [200, 201])
        bill_id = s_res.get_json()['bill_id']

        # Get context
        ctx = self.client.get(f'/api/sales/return-context?bill_id={bill_id}', headers=self.headers)
        self.assertEqual(ctx.status_code, 200)
        sale = ctx.get_json()['sale']

        # Process return for user 1
        ret_res1 = self.client.post('/api/sales/returns', headers=self.headers, json={
            'original_bill_id': bill_id,
            'reason': 'Customer requested refund',
            'return_date': '2026-10-07',
            'items': [{
                'product_id': 201,
                'product': 'Discrete Item Box',
                'qty': 2,
                'price': 200,
                'gst_pct': 18
            }]
        })
        self.assertEqual(ret_res1.status_code, 201)
        ret1 = ret_res1.get_json()
        self.assertTrue(ret1.get('success'))
        cn1 = ret1.get('credit_note_no')
        self.assertTrue(cn1.startswith('CN-'))

        # Check stock restoration (was init_stock - 5 + 2)
        prod = billzen.query("SELECT stock FROM products WHERE id=201", one=True)
        self.assertEqual(prod['stock'], init_stock - 5 + 2)

        # Login as User 2 (Tenant 2) and create sale + return to test sequential uniqueness
        u2_login = self.client.post('/api/login', json={'username': 'tenant_two', 'password': 'testerpass123'})
        self.assertEqual(u2_login.status_code, 200)
        u2_headers = {'X-CSRF-Token': u2_login.get_json()['csrf_token']}

        s2_res = self.client.post('/api/sales', headers=u2_headers, json={
            'customer_name': 'Tenant 2 Client',
            'items': [{'product': 'Tenant2 Widget', 'qty': 2, 'price': 100, 'gst_pct': 18}],
            'paid_amount': 236
        })
        self.assertIn(s2_res.status_code, [200, 201])
        s2_bill_id = s2_res.get_json()['bill_id']

        ret_res2 = self.client.post('/api/sales/returns', headers=u2_headers, json={
            'original_bill_id': s2_bill_id,
            'reason': 'Defective',
            'return_date': '2026-10-07',
            'items': [{
                'product_id': 203,
                'product': 'Tenant2 Widget',
                'qty': 1,
                'price': 100,
                'gst_pct': 18
            }]
        })
        # Must not fail with UNIQUE constraint failed
        self.assertEqual(ret_res2.status_code, 201)
        cn2 = ret_res2.get_json().get('credit_note_no')
        self.assertNotEqual(cn1, cn2, "Credit note numbers must be distinct across all tenants")

    # 4. SALES RETURN QUANTITY PRECISION (REJECT FRACTIONAL FOR DISCRETE UNITS)
    def test_04_sales_return_quantity_precision(self):
        # Re-login as manual_tester
        resp = self.client.post('/api/login', json={'username': 'manual_tester', 'password': 'testerpass123'})
        headers = {'X-CSRF-Token': resp.get_json()['csrf_token']}

        s_res = self.client.post('/api/sales', headers=headers, json={
            'customer_name': 'Discrete Check Client',
            'items': [{'product': 'Discrete Item Box', 'qty': 3, 'price': 200, 'gst_pct': 18}],
            'paid_amount': 708
        })
        bill_id = s_res.get_json()['bill_id']

        # Attempt fractional return 0.37 for Pcs
        bad_ret = self.client.post('/api/sales/returns', headers=headers, json={
            'original_bill_id': bill_id,
            'reason': 'Fractional test',
            'items': [{
                'product': 'Discrete Item Box',
                'qty': 0.37,
                'price': 200,
                'gst_pct': 18
            }]
        })
        self.assertEqual(bad_ret.status_code, 400)
        self.assertIn("countable", bad_ret.get_json().get('error', '').lower())

    # 5. PURCHASE RETURN QUANTITY PRECISION (REJECT FRACTIONAL FOR DISCRETE, ACCEPT FOR CONTINUOUS)
    def test_05_purchase_return_quantity_precision(self):
        # Re-login
        resp = self.client.post('/api/login', json={'username': 'manual_tester', 'password': 'testerpass123'})
        headers = {'X-CSRF-Token': resp.get_json()['csrf_token']}

        # Create purchase with 1 discrete (Pcs) and 1 continuous (Kg)
        p_res = self.client.post('/api/purchases', headers=headers, json={
            'supplier_name': 'Precision Supplier',
            'invoice_no': 'PREC-INV-001',
            'items': [
                {'product': 'Discrete Item Box', 'qty': 10, 'buy_price': 100, 'unit': 'Pcs', 'gst_pct': 18},
                {'product': 'Continuous Item Bulk', 'qty': 50, 'buy_price': 50, 'unit': 'Kg', 'gst_pct': 18}
            ]
        })
        self.assertIn(p_res.status_code, [200, 201])
        p_id = p_res.get_json().get('purchase_id') or p_res.get_json().get('id')

        # Try fractional return on discrete item -> 400
        bad_dn = self.client.post('/api/purchases/returns', headers=headers, json={
            'purchase_id': p_id,
            'reason': 'Fractional Discrete',
            'items': [{
                'product_name': 'Discrete Item Box',
                'unit': 'Pcs',
                'return_qty': 0.5,
                'rate': 100,
                'gst_pct': 18
            }]
        })
        self.assertEqual(bad_dn.status_code, 400)
        self.assertIn("countable", bad_dn.get_json().get('error', '').lower())

        # Try fractional return on continuous item (Kg) -> 201
        good_dn = self.client.post('/api/purchases/returns', headers=headers, json={
            'purchase_id': p_id,
            'reason': 'Fractional Bulk',
            'items': [{
                'product_name': 'Continuous Item Bulk',
                'unit': 'Kg',
                'return_qty': 1.5,
                'rate': 50,
                'gst_pct': 18
            }]
        })
        self.assertEqual(good_dn.status_code, 201)
        self.assertTrue(good_dn.get_json().get('success'))
        self.assertTrue(good_dn.get_json().get('debit_note_no', '').startswith('DN-'))

    # 6. RETURN HISTORY COMPATIBILITY KEYS
    def test_06_return_history_compatibility(self):
        resp = self.client.post('/api/login', json={'username': 'manual_tester', 'password': 'testerpass123'})
        headers = {'X-CSRF-Token': resp.get_json()['csrf_token']}

        sr_res = self.client.get('/api/sales/returns', headers=headers)
        self.assertEqual(sr_res.status_code, 200)
        sr_json = sr_res.get_json()
        self.assertIn('sales_returns', sr_json)
        self.assertIn('returns', sr_json)

        pr_res = self.client.get('/api/purchases/returns', headers=headers)
        self.assertEqual(pr_res.status_code, 200)
        pr_json = pr_res.get_json()
        self.assertIn('purchase_returns', pr_json)
        self.assertIn('returns', pr_json)

    # 7. CSV EXPORT ENDPOINTS
    def test_07_returns_csv_export(self):
        resp = self.client.post('/api/login', json={'username': 'manual_tester', 'password': 'testerpass123'})
        headers = {'X-CSRF-Token': resp.get_json()['csrf_token']}

        csv_sr = self.client.get('/api/sales/returns/export/csv', headers=headers)
        self.assertEqual(csv_sr.status_code, 200)
        self.assertIn('text/csv', csv_sr.headers.get('Content-Type', ''))
        self.assertIn('Credit Note No', csv_sr.data.decode('utf-8'))

        csv_pr = self.client.get('/api/purchases/returns/export/csv', headers=headers)
        self.assertEqual(csv_pr.status_code, 200)
        self.assertIn('text/csv', csv_pr.headers.get('Content-Type', ''))
        self.assertIn('Debit Note No', csv_pr.data.decode('utf-8'))

    # 8. STOCK ADJUSTMENT UNIT PRECISION & RECORDING
    def test_08_stock_adjustment_precision(self):
        resp = self.client.post('/api/login', json={'username': 'manual_tester', 'password': 'testerpass123'})
        headers = {'X-CSRF-Token': resp.get_json()['csrf_token']}

        # Discrete (id 201, Pcs): Fractional adjustment should fail
        res_bad = self.client.post('/api/stock/adjust', headers=headers, json={
            'product_id': 201,
            'qty': 0.45,
            'type': 'add',
            'reason': 'Count'
        })
        self.assertEqual(res_bad.status_code, 400)

        # Discrete: Integer adjustment should succeed
        res_good1 = self.client.post('/api/stock/adjust', headers=headers, json={
            'product_id': 201,
            'qty': 3.0,
            'type': 'add',
            'reason': 'Physical Count Correction'
        })
        self.assertEqual(res_good1.status_code, 200)

        # Continuous (id 202, Kg): Fractional adjustment should succeed
        res_good2 = self.client.post('/api/stock/adjust', headers=headers, json={
            'product_id': 202,
            'qty': 2.75,
            'type': 'remove',
            'reason': 'Damaged/Expired'
        })
        self.assertEqual(res_good2.status_code, 200)

        # Verify history
        hist = self.client.get('/api/stock/adjustments', headers=headers)
        self.assertEqual(hist.status_code, 200)
        adj_items = hist.get_json().get('adjustments', [])
        self.assertTrue(len(adj_items) >= 2)

    # 9. ZERO-VALUE SUPPLIER ITEM PRESERVATION
    def test_09_zero_value_supplier_item(self):
        resp = self.client.post('/api/login', json={'username': 'manual_tester', 'password': 'testerpass123'})
        headers = {'X-CSRF-Token': resp.get_json()['csrf_token']}

        p_res = self.client.post('/api/purchases', headers=headers, json={
            'supplier_name': 'Zero Promo Supplier',
            'invoice_no': 'ZERO-001',
            'items': [
                {'product': 'Paid Main Item', 'qty': 10, 'buy_price': 500, 'gst_pct': 18, 'unit': 'Pcs'},
                {'product': 'SKB 2692 5PC PARKA SO CONTAINER', 'qty': 210, 'buy_price': 0.0, 'gst_pct': 0, 'unit': 'Pcs'}
            ]
        })
        self.assertIn(p_res.status_code, [200, 201])
        p_id = p_res.get_json().get('purchase_id') or p_res.get_json().get('id')

        # Fetch return context and confirm item with price 0.0 is present without calculation errors
        ctx = self.client.get(f'/api/purchases/return-context?ref={p_id}', headers=headers)
        self.assertEqual(ctx.status_code, 200)
        items = ctx.get_json()['items']
        zero_item = next((it for it in items if 'PARKA SO CONTAINER' in it['product']), None)
        self.assertIsNotNone(zero_item)
        self.assertEqual(zero_item['price'], 0.0)
        self.assertEqual(zero_item['purchased_qty'], 210)

if __name__ == '__main__':
    unittest.main()
