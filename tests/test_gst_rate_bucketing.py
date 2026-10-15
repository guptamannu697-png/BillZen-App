import os
import io
import tempfile
import unittest
import gc
import hashlib
import openpyxl
from werkzeug.security import generate_password_hash

import app as billzen


CURRENT_LIVE_DB_MD5 = '09012db0b34846cbc60230a905b1a44d'
EXPECTED_BACKUP_MD5 = 'f603319a812f781776cb2726c72a5ea2'


class GSTRateBucketingTestCase(unittest.TestCase):
    def setUp(self):
        # Save original database path
        self.orig_database = billzen.DATABASE
        self.tempdir = tempfile.TemporaryDirectory()
        for key in ['BILLZEN_BOOTSTRAP_ADMIN_NAME', 'BILLZEN_BOOTSTRAP_ADMIN_USERNAME', 'BILLZEN_BOOTSTRAP_ADMIN_PASSWORD']:
            os.environ.pop(key, None)
        
        # Point to temporary test database and initialize full schema
        billzen.DATABASE = os.path.join(self.tempdir.name, 'test_gst_bucketing.db')
        billzen.ensure_database_ready()

        db = billzen.get_db()
        db.execute("DELETE FROM users WHERE username='gst_tester'")
        
        # Create test user and shop
        db.execute(
            "INSERT INTO users (id, name, username, password, role, is_master_admin, plan_status, subscription_ends_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (101, 'GST Test User', 'gst_tester', generate_password_hash('gstpass123'), 'admin', 0, 'active', '2028-12-31 00:00:00')
        )
        db.execute(
            "INSERT INTO shops (user_id, name, phone, gstin, state, state_code, address, email) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (101, 'Apex Enterprises', '9876500000', '27ABCDE1234F1Z5', 'MAHARASHTRA', '27', '123 Market Road, Mumbai', 'apex@example.com')
        )

        month = "2026-10"
        
        # 1. Sale 1: Local Intra-State invoice with 0%, 5%, 12%, 18%, 28% items
        db.execute("""
            INSERT INTO sales (id, user_id, bill_id, customer_name, customer_gstin, sale_date, subtotal, 
                               cgst_amount, sgst_amount, igst_amount, total_amount, is_interstate, bill_category, cancelled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (1, 101, 'INV-2026-001', 'Local Retail Buyer', '27XYZAB9999K1Z2', f'{month}-05', 
              1500.0, 129.0, 129.0, 0.0, 1758.0, 0, 'tax_invoice', 0))
        
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (1, 'Exempt Book', '4901', 1, 'Pc', 100.0, 0.0, 100.0))
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (1, 'Tea Pack 5%', '0902', 2, 'Pack', 100.0, 5.0, 210.0))
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (1, 'Processed Butter 12%', '0405', 3, 'Kg', 100.0, 12.0, 336.0))
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (1, 'Monitor Cable 18%', '8544', 4, 'Pc', 100.0, 18.0, 472.0))
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (1, 'Air Conditioner Part 28%', '8415', 5, 'Pc', 100.0, 28.0, 640.0))

        # 2. Sale 2: Inter-State invoice with 12% and 28% items
        db.execute("""
            INSERT INTO sales (id, user_id, bill_id, customer_name, customer_gstin, sale_date, subtotal, 
                               cgst_amount, sgst_amount, igst_amount, total_amount, is_interstate, bill_category, cancelled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (2, 101, 'INV-2026-002', 'Delhi Wholesaler', '07AAAAA1111A1Z1', f'{month}-10', 
              3000.0, 0.0, 0.0, 680.0, 3680.0, 1, 'tax_invoice', 0))
        
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (2, 'Interstate Hardware 12%', '8471', 10, 'Pc', 100.0, 12.0, 1120.0))
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (2, 'Luxury Electronic 28%', '8528', 20, 'Pc', 100.0, 28.0, 2560.0))

        # 3. Sale 3: Kacha Bill (must be excluded from GST returns & drilldown)
        db.execute("""
            INSERT INTO sales (id, user_id, bill_id, customer_name, customer_gstin, sale_date, subtotal, 
                               cgst_amount, sgst_amount, igst_amount, total_amount, is_interstate, bill_category, cancelled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (3, 101, 'EST-001', 'Cash Customer', '', f'{month}-12', 
              999.0, 0.0, 0.0, 0.0, 999.0, 0, 'kacha', 0))
        db.execute("INSERT INTO sale_items (sale_id, product, hsn_code, qty, unit, price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (3, 'Kacha Raw Material 18%', '0000', 1, 'Pc', 999.0, 18.0, 1178.82))

        # 4. Purchases: Inward supplies across rates
        db.execute("""
            INSERT INTO purchases (id, user_id, purchase_id, invoice_no, supplier_name, supplier_gstin, bill_date, 
                                   subtotal, cgst_amount, sgst_amount, igst_amount, total_amount, is_interstate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (10, 101, 'PUR-10', 'PUR-2026-101', 'National Supplier Ltd', '27ABCDE5555F1Z9', f'{month}-08',
              4000.0, 310.0, 310.0, 0.0, 4620.0, 0))
        
        db.execute("INSERT INTO purchase_items (purchase_id, product, hsn_code, qty, unit, buy_price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (10, 'Inward 5% Goods', '1101', 10, 'Kg', 100.0, 5.0, 1050.0))
        db.execute("INSERT INTO purchase_items (purchase_id, product, hsn_code, qty, unit, buy_price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (10, 'Inward 12% Goods', '1201', 10, 'Kg', 100.0, 12.0, 1120.0))
        db.execute("INSERT INTO purchase_items (purchase_id, product, hsn_code, qty, unit, buy_price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (10, 'Inward 18% Goods', '1301', 10, 'Kg', 100.0, 18.0, 1180.0))
        db.execute("INSERT INTO purchase_items (purchase_id, product, hsn_code, qty, unit, buy_price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (10, 'Inward 28% Goods', '1401', 10, 'Kg', 100.0, 28.0, 1280.0))

        # 5. Purchase 2: Inter-state purchase
        db.execute("""
            INSERT INTO purchases (id, user_id, purchase_id, invoice_no, supplier_name, supplier_gstin, bill_date, 
                                   subtotal, cgst_amount, sgst_amount, igst_amount, total_amount, is_interstate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (11, 101, 'PUR-11', 'PUR-2026-102', 'Haryana Steel Mills', '06AAAAA0000A1Z5', f'{month}-14',
              2000.0, 0.0, 0.0, 240.0, 2240.0, 1))
        db.execute("INSERT INTO purchase_items (purchase_id, product, hsn_code, qty, unit, buy_price, gst_pct, amount) VALUES (?,?,?,?,?,?,?,?)",
                   (11, 'Steel Sheets 12%', '7208', 20, 'Sheet', 100.0, 12.0, 2240.0))

        db.commit()
        db.close()
        
        self.client = billzen.app.test_client()

    def tearDown(self):
        self.client = None
        billzen.DATABASE = self.orig_database
        gc.collect()
        try:
            self.tempdir.cleanup()
        except Exception:
            pass

    def login(self):
        res = self.client.post('/api/login', json={'username': 'gst_tester', 'password': 'gstpass123'})
        self.assertEqual(res.status_code, 200)
        return {'X-CSRF-Token': res.get_json()['csrf_token']}

    def test_classify_gst_bucket_unit(self):
        """Verify classify_gst_bucket accurately maps rates to 0, 5, 12, 18, 28 buckets without misgrouping."""
        # Standard rates
        self.assertEqual(billzen.classify_gst_bucket(0), ("0", 0.0))
        self.assertEqual(billzen.classify_gst_bucket(5), ("5", 5.0))
        self.assertEqual(billzen.classify_gst_bucket(12), ("12", 12.0))
        self.assertEqual(billzen.classify_gst_bucket(18), ("18", 18.0))
        self.assertEqual(billzen.classify_gst_bucket(28), ("28", 28.0))

        # Float / rounded values
        self.assertEqual(billzen.classify_gst_bucket(5.0), ("5", 5.0))
        self.assertEqual(billzen.classify_gst_bucket(12.0), ("12", 12.0))
        self.assertEqual(billzen.classify_gst_bucket(18.0), ("18", 18.0))
        self.assertEqual(billzen.classify_gst_bucket(28.0), ("28", 28.0))

        # Thresholds: 12% must NEVER be classified as 18% or 5%
        self.assertEqual(billzen.classify_gst_bucket(12.0)[0], "12")
        self.assertNotEqual(billzen.classify_gst_bucket(12.0)[0], "18")
        self.assertNotEqual(billzen.classify_gst_bucket(12.0)[0], "5")

        # 28% must NEVER be classified as 18%
        self.assertEqual(billzen.classify_gst_bucket(28.0)[0], "28")
        self.assertNotEqual(billzen.classify_gst_bucket(28.0)[0], "18")

        # Edge cases
        self.assertEqual(billzen.classify_gst_bucket(None), ("0", 0.0))
        self.assertEqual(billzen.classify_gst_bucket(0.0), ("0", 0.0))
        self.assertEqual(billzen.classify_gst_bucket("12"), ("12", 12.0))
        self.assertEqual(billzen.classify_gst_bucket("28.0"), ("28", 28.0))

        # Unknown rates must NEVER be classified into any slab
        self.assertEqual(billzen.classify_gst_bucket(3.0), (None, None))
        self.assertEqual(billzen.classify_gst_bucket(14.0), (None, None))
        self.assertEqual(billzen.classify_gst_bucket(40.0), (None, None))

    def test_gst_drilldown_endpoint_all_slabs(self):
        """Verify /api/gst/drilldown returns all 5 distinct buckets with exact math and excludes kacha."""
        self.login()
        res = self.client.get('/api/gst/drilldown?month=2026-10')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        rates = data.get('rates', {})

        # All 5 slabs must be present
        for slab in ["0", "5", "12", "18", "28"]:
            self.assertIn(slab, rates, f"Slab {slab}% must be present in GST drilldown rates!")

        # 0% Exempt Slab
        b0 = rates["0"]
        self.assertEqual(b0["sales_taxable"], 100.0)
        self.assertEqual(b0["total_tax"], 0.0)
        self.assertEqual(b0["total_value"], 100.0)
        self.assertEqual(len(b0["invoices"]), 1)
        self.assertEqual(b0["invoices"][0]["bill_id"], "INV-2026-001")

        # 5% Slab: Sale 1 (taxable 200, cgst 5, sgst 5 -> total 210) + Purchase 1 (taxable 1000, cgst 25, sgst 25 -> total 1050)
        b5 = rates["5"]
        self.assertEqual(b5["sales_taxable"], 200.0)
        self.assertEqual(b5["cgst"], 5.0)
        self.assertEqual(b5["sgst"], 5.0)
        self.assertEqual(b5["igst"], 0.0)
        self.assertEqual(b5["total_tax"], 10.0)
        self.assertEqual(b5["total_value"], 1260.0) # 210.0 sales + 1050.0 purchases
        self.assertEqual(b5["purchases_taxable"], 1000.0)

        # 12% Slab:
        # Sale 1 (taxable 300, cgst 18, sgst 18) + Sale 2 (taxable 1000, igst 120) -> sales total 1456.0
        # Purchases: Purchase 1 (taxable 1000, tax 120 -> 1120.0) + Purchase 2 (taxable 2000, tax 240 -> 2240.0) -> purchases total 3360.0
        b12 = rates["12"]
        self.assertEqual(b12["sales_taxable"], 1300.0)
        self.assertEqual(b12["cgst"], 18.0)
        self.assertEqual(b12["sgst"], 18.0)
        self.assertEqual(b12["igst"], 120.0)
        self.assertEqual(b12["total_tax"], 156.0)
        self.assertEqual(b12["total_value"], 4816.0) # 1456.0 sales + 3360.0 purchases
        self.assertEqual(b12["purchases_taxable"], 3000.0)
        self.assertEqual(len(b12["invoices"]), 4) # 2 sales + 2 purchases

        # 18% Slab: Sale 1 (taxable 400, cgst 36, sgst 36 -> total 472.0) + Purchase 1 (taxable 1000, tax 180 -> total 1180.0)
        # Note: Kacha bill (EST-001) MUST NOT be present!
        b18 = rates["18"]
        self.assertEqual(b18["sales_taxable"], 400.0)
        self.assertEqual(b18["cgst"], 36.0)
        self.assertEqual(b18["sgst"], 36.0)
        self.assertEqual(b18["igst"], 0.0)
        self.assertEqual(b18["total_tax"], 72.0)
        self.assertEqual(b18["total_value"], 1652.0) # 472.0 sales + 1180.0 purchases
        self.assertEqual(b18["purchases_taxable"], 1000.0)
        for inv in b18["invoices"]:
            self.assertNotEqual(inv.get("bill_id"), "EST-001", "Kacha estimate bill must be excluded from GST return!")

        # 28% Slab:
        # Sale 1 (taxable 500, cgst 70, sgst 70) + Sale 2 (taxable 2000, igst 560) -> sales total 3200.0
        # Purchases: Purchase 1 (taxable 1000, tax 280 -> total 1280.0)
        b28 = rates["28"]
        self.assertEqual(b28["sales_taxable"], 2500.0)
        self.assertEqual(b28["cgst"], 70.0)
        self.assertEqual(b28["sgst"], 70.0)
        self.assertEqual(b28["igst"], 560.0)
        self.assertEqual(b28["total_tax"], 700.0)
        self.assertEqual(b28["total_value"], 4480.0) # 3200.0 sales + 1280.0 purchases
        self.assertEqual(b28["purchases_taxable"], 1000.0)

    def test_gst_ca_excel_export_all_slabs(self):
        """Verify /api/gst/export/excel produces CA workbook with 28, 18, 12, 5 registers and summary."""
        self.login()
        res = self.client.get('/api/gst/export/excel?month=2026-10')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data.startswith(b'PK\x03\x04'), "Must return valid xlsx zip archive")

        wb = openpyxl.load_workbook(io.BytesIO(res.data), data_only=False)
        sheet_names = wb.sheetnames

        # Verify summary sheet and all 4 itemized rate registers exist
        self.assertIn("GST Summary & Reconciliation", sheet_names)
        self.assertIn("28% GST Register", sheet_names)
        self.assertIn("18% GST Register", sheet_names)
        self.assertIn("12% GST Register", sheet_names)
        self.assertIn("5% GST Register", sheet_names)

        # Inspect Summary Sheet
        ws_sum = wb["GST Summary & Reconciliation"]
        # Rows 6 to 10 are the 5 rates: 28%, 18%, 12%, 5%, 0%
        r6_label = ws_sum.cell(row=6, column=1).value
        r7_label = ws_sum.cell(row=7, column=1).value
        r8_label = ws_sum.cell(row=8, column=1).value
        r9_label = ws_sum.cell(row=9, column=1).value
        r10_label = ws_sum.cell(row=10, column=1).value

        self.assertIn("28%", r6_label)
        self.assertIn("18%", r7_label)
        self.assertIn("12%", r8_label)
        self.assertIn("5%", r9_label)
        self.assertIn("0%", r10_label)

        # 28% Taxable (col 2) and Total Tax (col 6)
        self.assertEqual(float(ws_sum.cell(row=6, column=2).value), 2500.0)
        self.assertEqual(float(ws_sum.cell(row=6, column=6).value), 700.0)

        # 12% Taxable and Total Tax
        self.assertEqual(float(ws_sum.cell(row=8, column=2).value), 1300.0)
        self.assertEqual(float(ws_sum.cell(row=8, column=6).value), 156.0)

        # Row 11: Totals Row formula check
        tot_label = ws_sum.cell(row=11, column=1).value
        self.assertEqual(tot_label, "TOTAL OUTWARD GST")
        tot_taxable_formula = ws_sum.cell(row=11, column=2).value
        self.assertEqual(tot_taxable_formula, "=SUM(B6:B10)")
        tot_tax_formula = ws_sum.cell(row=11, column=6).value
        self.assertEqual(tot_tax_formula, "=SUM(F6:F10)")

        # Inspect 28% Register Sheet
        ws_28 = wb["28% GST Register"]
        # Verify items exist: Air Conditioner Part (Sale 1) and Luxury Electronic (Sale 2)
        found_ac = False
        found_lux = False
        for r in range(5, ws_28.max_row + 1):
            val = ws_28.cell(row=r, column=6).value # Item Name
            if val and "Air Conditioner" in str(val):
                found_ac = True
                self.assertEqual(float(ws_28.cell(row=r, column=10).value), 500.0) # Taxable
                self.assertEqual(float(ws_28.cell(row=r, column=11).value), 140.0) # Tax (28% of 500)
            if val and "Luxury Electronic" in str(val):
                found_lux = True
                self.assertEqual(float(ws_28.cell(row=r, column=10).value), 2000.0) # Taxable
                self.assertEqual(float(ws_28.cell(row=r, column=11).value), 560.0)  # Tax (28% of 2000)
        self.assertTrue(found_ac, "Air Conditioner 28% item must be in 28% Register")
        self.assertTrue(found_lux, "Luxury Electronic 28% item must be in 28% Register")

        # Inspect 12% Register Sheet
        ws_12 = wb["12% GST Register"]
        found_butter = False
        found_hw = False
        for r in range(5, ws_12.max_row + 1):
            val = ws_12.cell(row=r, column=6).value
            if val and "Processed Butter" in str(val):
                found_butter = True
                self.assertEqual(float(ws_12.cell(row=r, column=10).value), 300.0) # Taxable
                self.assertEqual(float(ws_12.cell(row=r, column=11).value), 36.0)  # Tax (12% of 300)
            if val and "Interstate Hardware" in str(val):
                found_hw = True
                self.assertEqual(float(ws_12.cell(row=r, column=10).value), 1000.0) # Taxable
                self.assertEqual(float(ws_12.cell(row=r, column=11).value), 120.0)  # Tax (12% of 1000)
        self.assertTrue(found_butter, "Processed Butter 12% item must be in 12% Register")
        self.assertTrue(found_hw, "Interstate Hardware 12% item must be in 12% Register")

    def test_financial_reports_workbook_gst_summary_sheet(self):
        """Verify full financial reports workbook Sheet 6 (GST Summary) classifies 28% and 12% correctly."""
        self.login()
        res = self.client.get('/api/reports/export/excel?month=2026-10')
        self.assertEqual(res.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(res.data), data_only=False)
        self.assertIn("GST Summary", wb.sheetnames)
        ws_gst = wb["GST Summary"]

        # Check rate rows
        rates_found = {}
        for r in range(5, ws_gst.max_row + 1):
            rate_label = ws_gst.cell(row=r, column=1).value
            if rate_label and "%" in str(rate_label):
                taxable = ws_gst.cell(row=r, column=2).value
                tot_gst = ws_gst.cell(row=r, column=6).value
                rates_found[str(rate_label).strip()] = (taxable, tot_gst)

        self.assertIn("28%", rates_found)
        self.assertIn("18%", rates_found)
        self.assertIn("12%", rates_found)
        self.assertIn("5%", rates_found)
        self.assertIn("0% (Exempt)", rates_found)

        # 28% should be 2500 taxable, 700 total tax
        self.assertEqual(rates_found["28%"], (2500.0, 700.0))
        # 12% should be 1300 taxable, 156 total tax
        self.assertEqual(rates_found["12%"], (1300.0, 156.0))
        # 18% should be 400 taxable, 72 total tax
        self.assertEqual(rates_found["18%"], (400.0, 72.0))
        # 5% should be 200 taxable, 10 total tax
        self.assertEqual(rates_found["5%"], (200.0, 10.0))
        # 0% should be 100 taxable, 0 total tax
        self.assertEqual(rates_found["0% (Exempt)"], (100.0, 0.0))

    def test_real_database_untouched(self):
        """Verify that the real billzen.db and backups were never modified during tests."""
        with open('billzen.db', 'rb') as f:
            h1 = hashlib.md5(f.read()).hexdigest()
        with open('backups/billzen_backup_2026-10-13.db', 'rb') as f:
            h2 = hashlib.md5(f.read()).hexdigest()
        self.assertEqual(h1, CURRENT_LIVE_DB_MD5, "billzen.db MD5 hash changed!")
        self.assertEqual(h2, EXPECTED_BACKUP_MD5, "backup database MD5 hash changed!")


if __name__ == '__main__':
    unittest.main()
