import os
import sys
import unittest
import json
import xml.etree.ElementTree as ET
from io import BytesIO

# Ensure working directory
sys.path.insert(0, os.path.abspath('.'))

import app as billzen
from bi_agent import BillZenBIAgent, get_bi_grounded_response
from gst_return_engine import calculate_rule_88a_itc_setoff, perform_book_reconciliation
from werkzeug.security import generate_password_hash, check_password_hash

class ProductionFixesVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        billzen.DATABASE = 'test_verify.db'
        if os.path.exists('test_verify.db'):
            try: os.remove('test_verify.db')
            except: pass
        billzen.init_db()
        billzen.migrate_db()

        db = billzen.get_db()
        # Seed test user
        db.execute("DELETE FROM users WHERE username = 'auditor'")
        db.execute("INSERT INTO users (id, name, username, password, plain_password, role) VALUES (?, ?, ?, ?, ?, ?)",
                   (99, 'Auditor Admin', 'auditor', generate_password_hash('auditpass123'), '', 'admin'))
        
        # Seed products
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, stock, low_stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (101, 99, 'Item LowStock', '8471', 2, 10, 100, 200, 18))
        db.execute("INSERT OR REPLACE INTO products (id, user_id, name, hsn_code, stock, low_stock, buy_price, sell_price, gst_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (102, 99, 'Item Abundant', '8472', 50, 5, 200, 400, 18))
        db.commit()
        db.close()

        cls.client = billzen.app.test_client()
        # Login
        resp = cls.client.post('/api/login', json={'username': 'auditor', 'password': 'auditpass123'})
        assert resp.status_code == 200, f"Login failed: {resp.data}"
        cls.csrf = resp.get_json()['csrf_token']
        cls.headers = {'X-CSRF-Token': cls.csrf}

    @classmethod
    def tearDownClass(cls):
        if os.path.exists('test_verify.db'):
            try: os.remove('test_verify.db')
            except: pass

    # 1. TALLY XML EXPORT V2
    def test_01_tally_export_v2_make_response(self):
        # Create a sale
        sale_res = self.client.post('/api/sales', headers=self.headers, json={
            'customer_name': 'Tally Client Ltd',
            'customer_gstin': '07AAAAA0000A1Z5',
            'items': [{'product': 'Item Abundant', 'qty': 2, 'price': 400, 'gst_pct': 18, 'cgst_pct': 9, 'sgst_pct': 9}],
            'paid_amount': 944
        })
        self.assertIn(sale_res.status_code, [200, 201])
        bill_id = sale_res.get_json()['bill_id']

        # Call /api/tally/export/v2
        resp = self.client.post('/api/tally/export/v2', headers=self.headers, json={
            'sales_ids': [bill_id],
            'category': 'all'
        })
        self.assertEqual(resp.status_code, 200, f"Expected 200 but got {resp.status_code}: {resp.data}")
        xml_data = resp.data.decode('utf-8')
        self.assertIn('<ENVELOPE>', xml_data)
        self.assertIn('<VOUCHER', xml_data)
        self.assertIn(bill_id, xml_data)
        self.assertIn('attachment;', resp.headers.get('Content-Disposition', ''))

    # 2. SALES CANCELLATION + STOCK (PHANTOM STOCK FIX)
    def test_02_sale_cancellation_with_returns_blocked(self):
        # Initial stock of Item Abundant
        init_stock = billzen.query("SELECT stock FROM products WHERE id = 102", one=True)['stock']

        # Create sale of 4 items
        sale_res = self.client.post('/api/sales', headers=self.headers, json={
            'customer_name': 'Return Test Cust',
            'items': [{'product': 'Item Abundant', 'qty': 4, 'price': 400, 'gst_pct': 18}],
            'paid_amount': 1888
        })
        self.assertIn(sale_res.status_code, [200, 201])
        bill_id = sale_res.get_json()['bill_id']
        self.assertEqual(billzen.query("SELECT stock FROM products WHERE id = 102", one=True)['stock'], init_stock - 4)

        # Issue partial return of 1 item
        ret_res = self.client.post('/api/sales/returns', headers=self.headers, json={
            'original_bill_id': bill_id,
            'reason': 'Damaged Goods',
            'refund_mode': 'Cash',
            'items': [{'product': 'Item Abundant', 'qty': 1, 'price': 400, 'gst_pct': 18}]
        })
        self.assertIn(ret_res.status_code, [200, 201], f"Sales return creation failed: {ret_res.data}")
        # Stock restored by 1 -> init_stock - 3
        self.assertEqual(billzen.query("SELECT stock FROM products WHERE id = 102", one=True)['stock'], init_stock - 3)

        # Attempt to cancel the sale while it has an active Credit Note / Sales Return
        cancel_res = self.client.post(f'/api/sales/{bill_id}/cancel', headers=self.headers)
        self.assertEqual(cancel_res.status_code, 400, "Cancellation should be rejected when active returns exist!")
        cancel_err = cancel_res.get_json()['error']
        self.assertTrue('active' in cancel_err.lower() and ('return' in cancel_err.lower() or 'credit note' in cancel_err.lower()))

        # Verify stock was NOT modified by the rejected cancellation
        self.assertEqual(billzen.query("SELECT stock FROM products WHERE id = 102", one=True)['stock'], init_stock - 3)

    # 3. AI AGENT DETERMINISTIC QUERIES (NO min_stock CRASH)
    def test_03_ai_agent_low_stock_and_queries(self):
        db = billzen.get_db()
        agent = BillZenBIAgent(db, 99)
        # What stock is low?
        res = agent.answer_query("What stock is low?")
        self.assertIn('Item LowStock', res)

        # Invoices with returns
        res_ret = agent.answer_query("Which invoices have returns?")
        self.assertTrue(len(res_ret) > 0)

        # Pending for Tally
        res_tally = agent.answer_query("What is pending for Tally?")
        self.assertIn('Tally', res_tally)

    # 4. STAFF PASSWORD SECURITY
    def test_04_staff_password_never_exposed(self):
        # Create a new staff member
        staff_add = self.client.post('/api/staff', headers=self.headers, json={
            'name': 'Test Cashier',
            'username': 'cashier1',
            'password': 'SuperSecretStaffPassword123!',
            'role': 'cashier'
        })
        self.assertIn(staff_add.status_code, [200, 201])

        # GET /api/staff
        staff_list_res = self.client.get('/api/staff', headers=self.headers)
        self.assertEqual(staff_list_res.status_code, 200)
        staff_members = staff_list_res.get_json()['staff']
        for s in staff_members:
            self.assertNotIn('plain_password', s, "plain_password must never be returned in staff list!")
            self.assertNotIn('password', s, "password hash must not be exposed!")

        # Verify password in DB is securely hashed
        user_row = billzen.query("SELECT password, plain_password FROM users WHERE username = 'cashier1'", one=True)
        self.assertTrue(check_password_hash(user_row['password'], 'SuperSecretStaffPassword123!'))
        self.assertEqual(user_row['plain_password'], '', "plain_password column in database must be empty!")

        # Verify staff login works using separate client so admin session isn't overwritten
        cashier_client = billzen.app.test_client()
        c_login = cashier_client.post('/api/login', json={'username': 'cashier1', 'password': 'SuperSecretStaffPassword123!'})
        self.assertEqual(c_login.status_code, 200)

    # 5. LIVE TALLY SSRF BLOCKED & CONNECTOR TOKEN/VOUCHERS
    def test_05_ssrf_blocked_and_connector_endpoints(self):
        # Arbitrary external host should be blocked with SSRF error
        ssrf_res = self.client.post('/api/tally/sync/live', headers=self.headers, json={
            'tally_host': '192.168.1.100',
            'tally_port': 9000
        })
        self.assertEqual(ssrf_res.status_code, 400)
        self.assertIn('SSRF', ssrf_res.get_json().get('error', ''))

        # Connector pairing token
        tok_res = self.client.get('/api/tally/connector/token', headers=self.headers)
        self.assertEqual(tok_res.status_code, 200)
        token = tok_res.get_json()['connector_token']
        self.assertTrue(token.startswith('bz_conn_'))

        # Fetch vouchers using connector token
        v_res = self.client.get('/api/tally/connector/vouchers', headers={'X-Connector-Token': token})
        self.assertEqual(v_res.status_code, 200)
        v_xml = v_res.data.decode('utf-8')
        self.assertIn('<ENVELOPE>', v_xml)

    # 6. PURCHASE GST & DELETION WITH DEBIT NOTES
    def test_06_purchase_interstate_and_delete_protection(self):
        # Create an interstate purchase
        pur_res = self.client.post('/api/purchases', headers=self.headers, json={
            'supplier_name': 'Interstate Supplier Corp',
            'supplier_gstin': '27ABCDE1234F1Z5',
            'is_interstate': 1,
            'state_code': '27',
            'invoice_no': 'PUR-INT-001',
            'bill_date': '2026-10-01',
            'items': [{'product': 'Item Abundant', 'qty': 5, 'buy_price': 150, 'gst_pct': 18, 'cgst_pct': 0, 'sgst_pct': 0, 'igst_pct': 18}]
        })
        self.assertIn(pur_res.status_code, [200, 201])
        pur_id = pur_res.get_json()['id']

        # Verify is_interstate persisted
        pur_db = billzen.query("SELECT is_interstate, state_code, igst_amount FROM purchases WHERE id = ?", (pur_id,), one=True)
        self.assertEqual(pur_db['is_interstate'], 1)
        self.assertEqual(pur_db['state_code'], '27')
        self.assertGreater(pur_db['igst_amount'], 0)

        # Create Debit Note / Purchase Return
        dn_res = self.client.post('/api/purchases/returns', headers=self.headers, json={
            'original_purchase_id': pur_id,
            'reason': 'Excess billing',
            'refund_mode': 'Bank Adjustment',
            'items': [{'product': 'Item Abundant', 'qty': 1, 'price': 150, 'gst_pct': 18}]
        })
        self.assertIn(dn_res.status_code, [200, 201])

        # Attempt to delete purchase with active Debit Note
        del_res = self.client.delete(f'/api/purchases/{pur_id}', headers=self.headers)
        self.assertEqual(del_res.status_code, 400, "Deleting purchase with active debit notes must be rejected!")
        self.assertIn('Debit Note', del_res.get_json().get('error', ''))

    # 7. GSTR-3B RULE 88A ITC SETOFF
    def test_07_rule_88a_itc_setoff(self):
        # Test case: IGST ITC 1000, CGST ITC 500, SGST ITC 500
        # Liabilities: IGST 400, CGST 800, SGST 600
        # Rule 88A dictates: IGST ITC (1000) first covers IGST (400) -> 600 remaining.
        # Remaining 600 covers CGST or SGST before CGST/SGST credit is touched.
        res = calculate_rule_88a_itc_setoff(
            net_out_igst=400, net_out_cgst=800, net_out_sgst=600,
            net_itc_igst=1000, net_itc_cgst=500, net_itc_sgst=500
        )
        self.assertEqual(res['used_igst_for_igst'], 400)
        self.assertEqual(res['used_igst_for_cgst'], 600)
        self.assertEqual(res['remaining_itc_igst'], 0, "All IGST ITC must be fully utilized first under Rule 88A")
        self.assertEqual(res['payable_igst'], 0)

    # 8. GST BOOK RECONCILIATION DIFFERENCE REPORTING
    def test_08_gst_book_reconciliation(self):
        resp = self.client.get('/api/gst/return-center?month=2026-10', headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('reconciliation', data)
        recon = data['reconciliation']
        self.assertIn('is_reconciled', recon)
        self.assertIn('books', recon)
        self.assertIn('returns', recon)
        self.assertIn('discrepancies', recon)
        self.assertIn('gstr1_vs_3b_tax_diff', recon['discrepancies'])

    # 9. BACKUP AND RESTORE RETURNS AND LEDGERS
    def test_09_backup_and_restore_returns_and_mappings(self):
        # Save a custom ledger mapping
        self.client.post('/api/tally/ledger-mappings', headers=self.headers, json={
            'local_sales_18': 'My Special Sales 18%',
            'cgst_ledger': 'Custom Central Tax'
        })

        # Run backup
        bk_res = self.client.get('/api/backup')
        self.assertEqual(bk_res.status_code, 200)
        raw_bk = json.loads(bk_res.data.decode('utf-8'))
        backup_data = raw_bk.get('backup', raw_bk)
        
        self.assertIn('sales_returns', backup_data)
        self.assertIn('purchase_returns', backup_data)
        self.assertIn('shop_ledger_mappings', backup_data)

        # Restore from backup
        restore_file = (BytesIO(json.dumps(raw_bk).encode('utf-8')), 'backup.json')
        rst_res = self.client.post('/api/restore', headers=self.headers, data={'backup_file': restore_file}, content_type='multipart/form-data')
        self.assertEqual(rst_res.status_code, 200)
        self.assertTrue(rst_res.get_json()['success'])

if __name__ == '__main__':
    unittest.main()
