import os
import tempfile
import unittest
import gc
from unittest import mock
from datetime import date

import app as billzen
from werkzeug.security import generate_password_hash


class BillZenTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        for key in ['BILLZEN_BOOTSTRAP_ADMIN_NAME', 'BILLZEN_BOOTSTRAP_ADMIN_USERNAME', 'BILLZEN_BOOTSTRAP_ADMIN_PASSWORD']:
            os.environ.pop(key, None)
        billzen.DATABASE = os.path.join(self.tempdir.name, 'test.db')
        billzen.init_db()
        db = billzen.get_db()
        db.execute("DELETE FROM users WHERE username = 'admin'")
        db.execute("INSERT INTO users (id,name,username,password,role) VALUES (?,?,?,?,?)",
                   (1, 'Owner', 'owner', generate_password_hash('correct horse battery staple'), 'admin'))
        db.execute("INSERT INTO products (user_id,name,stock,gst_pct,buy_price,sell_price) VALUES (?,?,?,?,?,?)",
                   (1, 'Widget', 20, 18, 50, 100))
        db.commit()
        db.close()
        self.client = billzen.app.test_client()

    def tearDown(self):
        self.client = None
        gc.collect()
        self.tempdir.cleanup()

    def login(self):
        response = self.client.post('/api/login', json={'username': 'owner', 'password': 'correct horse battery staple'})
        self.assertEqual(response.status_code, 200)
        return {'X-CSRF-Token': response.get_json()['csrf_token']}

    def test_purchase_requires_authentication(self):
        response = self.client.post('/api/purchases', json={'items': [{'product': 'Widget', 'qty': 1, 'buy_price': 10}]})
        self.assertEqual(response.status_code, 401)

    def test_empty_database_does_not_create_default_credentials(self):
        original_database = billzen.DATABASE
        empty_dir = tempfile.TemporaryDirectory()
        try:
            billzen.DATABASE = os.path.join(empty_dir.name, 'empty.db')
            billzen.init_db()
            self.assertEqual(billzen.query('SELECT COUNT(*) AS count FROM users', one=True)['count'], 0)
        finally:
            billzen.DATABASE = original_database
            gc.collect()
            empty_dir.cleanup()

    def test_logout_and_csrf_protect_state_changes(self):
        headers = self.login()
        self.assertEqual(self.client.post('/api/products', json={'name': 'Blocked'}).status_code, 403)
        self.assertEqual(self.client.post('/api/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/products').status_code, 401)

    def test_shop_never_returns_provider_keys(self):
        headers = self.login()
        response = self.client.post('/api/shop', headers=headers, json={'name': 'Shop', 'openai_key': 'secret-value'})
        self.assertEqual(response.status_code, 200)
        shop = self.client.get('/api/shop').get_json()['shop']
        self.assertNotIn('openai_key', shop)
        self.assertTrue(shop['openai_key_configured'])

    def test_sale_edit_cancel_and_payment_keep_stock_and_ledger_consistent(self):
        headers = self.login()
        sale = self.client.post('/api/sales', headers=headers, json={
            'customer_name': 'Alice', 'items': [{'product': 'Widget', 'qty': 2, 'price': 100, 'gst_pct': 18, 'cgst_pct': 9, 'sgst_pct': 9}],
            'paid_amount': 0,
        }).get_json()
        bill_id = sale['bill_id']
        self.assertEqual(self.client.get('/api/products').get_json()['products'][0]['stock'], 18)
        edited = self.client.put('/api/sales/' + bill_id, headers=headers, json={
            'customer_name': 'Alice', 'items': [{'product': 'Widget', 'qty': 3, 'price': 100, 'gst_pct': 5, 'cgst_pct': 2.5, 'sgst_pct': 2.5}],
            'paid_amount': 0,
        })
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()['total_amount'], 315.0)
        self.assertEqual(self.client.get('/api/products').get_json()['products'][0]['stock'], 17)
        customer = self.client.get('/api/customers').get_json()['customers'][0]
        payment = self.client.post('/api/customers/%s/payment' % customer['id'], headers=headers, json={'amount': 100, 'mode': 'UPI'})
        self.assertEqual(payment.get_json()['applied'], 100)
        self.assertEqual(billzen.rows('SELECT amount FROM customer_payments'), [{'amount': 100.0}])
        self.assertEqual(self.client.post('/api/sales/' + bill_id + '/cancel', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/products').get_json()['products'][0]['stock'], 20)

    def test_returns_are_limited_to_the_original_sale_quantity(self):
        headers = self.login()
        bill_id = self.client.post('/api/sales', headers=headers, json={
            'customer_name': 'Alice', 'items': [{'product': 'Widget', 'qty': 2, 'price': 100, 'gst_pct': 18, 'cgst_pct': 9, 'sgst_pct': 9}],
        }).get_json()['bill_id']
        accepted = self.client.post('/api/returns', headers=headers, json={
            'original_bill_id': bill_id, 'items': [{'product': 'Widget', 'qty': 1, 'price': 100}],
        })
        self.assertEqual(accepted.status_code, 201)
        rejected = self.client.post('/api/returns', headers=headers, json={
            'original_bill_id': bill_id, 'items': [{'product': 'Widget', 'qty': 2, 'price': 100}],
        })
        self.assertEqual(rejected.status_code, 400)

    def test_staff_login_works_on_owner_shop_data(self):
        headers = self.login()
        self.assertEqual(self.client.post('/api/staff', headers=headers, json={
            'name': 'Cashier', 'username': 'cashier', 'password': 'cashier-pass', 'role': 'superuser'}).status_code, 201)
        self.assertEqual(billzen.query("SELECT role FROM users WHERE username='cashier'", one=True)['role'], 'staff')
        self.client.post('/api/logout', headers=headers)
        response = self.client.post('/api/login', json={'username': 'cashier', 'password': 'cashier-pass'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/products').get_json()['products'][0]['name'], 'Widget')
        self.assertEqual(self.client.get('/api/staff').status_code, 403)

    def test_ai_chat_uses_configured_provider_and_falls_back(self):
        headers = self.login()
        self.client.post('/api/shop', headers=headers, json={'name': 'Shop', 'ai_provider': 'anthropic', 'gemini_key': 'g-key'})
        calls = []

        def fake_complete(provider, key, system, messages, **kwargs):
            calls.append((provider, key, system, messages))
            return 'Sales look good.'

        with mock.patch.object(billzen, 'ai_complete', side_effect=fake_complete):
            response = self.client.post('/api/ai/chat', headers=headers, json={'messages': [
                {'role': 'system', 'content': 'shop data'}, {'role': 'user', 'content': 'How are sales?'}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['provider'], 'gemini')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'gemini')
        self.assertEqual(calls[0][1], 'g-key')
        self.assertIn('shop data', calls[0][2])
        self.assertEqual(calls[0][3], [{'role': 'user', 'content': 'How are sales?'}])
        self.assertEqual(self.client.get('/api/ai/status').get_json()['configured'], ['gemini'])

    def test_scan_bill_parses_provider_json(self):
        headers = self.login()
        self.client.post('/api/shop', headers=headers, json={'name': 'Shop', 'ai_provider': 'anthropic', 'anthropic_key': 'a-key'})
        with mock.patch.object(billzen, 'ai_complete', return_value='```json\n{"supplier_name": "ACME", "items": []}\n```'):
            response = self.client.post('/api/scan-bill', headers=headers, json={'image_b64': 'aGk=', 'mime_type': 'image/png'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data']['supplier_name'], 'ACME')
        bad = self.client.post('/api/scan-bill', headers=headers, json={'image_b64': 'aGk=', 'mime_type': 'text/html'})
        self.assertEqual(bad.status_code, 400)


    def test_user_registration_and_setup_status(self):
        status = self.client.get('/api/setup-status').get_json()
        self.assertTrue(status['has_users'])
        reg = self.client.post('/api/register', json={
            'name': 'New Trader',
            'username': 'trader1',
            'password': 'trader-pass-123',
            'shop_name': 'Super Traders',
            'phone': '9876543210'
        })
        self.assertEqual(reg.status_code, 201)
        self.assertTrue(reg.get_json()['success'])
        # Verify trader can fetch their own shop data
        me = self.client.get('/api/me').get_json()
        self.assertTrue(me['loggedIn'])
        self.assertEqual(me['username'], 'trader1')

    def test_kacha_pakka_billing_and_conversion(self):
        headers = self.login()
        # Create a Kacha (Estimate) bill
        kacha = self.client.post('/api/sales', headers=headers, json={
            'customer_name': 'Bob',
            'bill_category': 'kacha',
            'items': [{'product': 'Widget', 'qty': 2, 'price': 100, 'gst_pct': 0}],
            'paid_amount': 200
        }).get_json()
        kacha_id = kacha['bill_id']
        self.assertTrue(kacha_id.startswith('EST'))
        self.assertEqual(kacha['bill_category'], 'kacha')

        # Create a Pakka (Tax) bill
        pakka = self.client.post('/api/sales', headers=headers, json={
            'customer_name': 'Charlie',
            'bill_category': 'pakka',
            'items': [{'product': 'Widget', 'qty': 1, 'price': 100, 'gst_pct': 18, 'cgst_pct': 9, 'sgst_pct': 9}],
            'paid_amount': 118
        }).get_json()
        pakka_id = pakka['bill_id']
        self.assertTrue(pakka_id.startswith('INV'))

        # Verify GST report includes ONLY Pakka bill
        today_month = str(date.today())[:7]
        gst_rep = self.client.get(f'/api/reports/gst?month={today_month}', headers=headers).get_json()
        gst_bill_ids = [s['bill_id'] for s in gst_rep['sales']]
        self.assertIn(pakka_id, gst_bill_ids)
        self.assertNotIn(kacha_id, gst_bill_ids)

        # Convert Kacha bill to Pakka Tax invoice
        convert_res = self.client.post(f'/api/sales/{kacha_id}/convert-to-pakka', headers=headers)
        self.assertEqual(convert_res.status_code, 200)
        converted_data = convert_res.get_json()
        new_inv_id = converted_data['new_bill_id']
        self.assertTrue(new_inv_id.startswith('INV'))

        # Now newly converted invoice must appear in GST report
        gst_rep_updated = self.client.get(f'/api/reports/gst?month={today_month}', headers=headers).get_json()
        updated_bill_ids = [s['bill_id'] for s in gst_rep_updated['sales']]
        self.assertIn(new_inv_id, updated_bill_ids)

    def test_quotation_and_challan_persistence(self):
        headers = self.login()
        # Create quotation
        q_res = self.client.post('/api/quotations', headers=headers, json={
            'customer_name': 'David',
            'customer_phone': '9999988888',
            'items': [{'product': 'Widget', 'qty': 5, 'price': 90, 'gst_pct': 18}]
        })
        self.assertEqual(q_res.status_code, 201)
        qid = q_res.get_json()['quote_id']
        self.assertTrue(qid.startswith('Q'))

        # Fetch quotations list
        q_list = self.client.get('/api/quotations', headers=headers).get_json()
        self.assertEqual(len(q_list['quotations']), 1)
        self.assertEqual(q_list['quotations'][0]['customer_name'], 'David')

        # Create delivery challan
        dc_res = self.client.post('/api/challans', headers=headers, json={
            'consignee_name': 'Metro Logistics',
            'vehicle_no': 'DL-01-AB-1234',
            'items': [{'product': 'Widget', 'qty': 5}]
        })
        self.assertEqual(dc_res.status_code, 201)
        dc_id = dc_res.get_json()['challan_id']
        self.assertTrue(dc_id.startswith('DC'))

        dc_list = self.client.get('/api/challans', headers=headers).get_json()
        self.assertEqual(len(dc_list['challans']), 1)
        self.assertEqual(dc_list['challans'][0]['consignee_name'], 'Metro Logistics')

    def test_database_backup_and_restore(self):
        headers = self.login()
        backup_res = self.client.get('/api/backup', headers=headers)
        self.assertEqual(backup_res.status_code, 200)
        backup_data = backup_res.get_json()['backup']
        self.assertIn('products', backup_data)
        self.assertIn('sales', backup_data)
        self.assertEqual(backup_data['products'][0]['name'], 'Widget')

        # Test restore
        restore_res = self.client.post('/api/restore', headers=headers, json={
            'backup': {
                'products': [{'name': 'New Restored Item', 'stock': 50, 'buy_price': 100, 'sell_price': 150}]
            }
        })
        self.assertEqual(restore_res.status_code, 200)
        prods = self.client.get('/api/products', headers=headers).get_json()['products']
        prod_names = [p['name'] for p in prods]
        self.assertIn('New Restored Item', prod_names)

    def test_direct_pdf_scan_processing(self):
        import fitz
        import base64
        # Generate a sample PDF in memory
        doc = fitz.open()
        p = doc.new_page()
        p.insert_text((50, 50), "Tax Invoice INV-2026-99\nSupplier: Sharma Distributors\nGSTIN: 07ABCDE1234F1Z5\nGrand Total: 15000")
        pdf_bytes = doc.tobytes()
        pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')

        # Test process_pdf_document helper directly
        extracted_text, rendered_jpeg = billzen.process_pdf_document(pdf_b64)
        self.assertIn("Sharma Distributors", extracted_text)
        self.assertIn("07ABCDE1234F1Z5", extracted_text)
        self.assertIsNotNone(rendered_jpeg)
        self.assertTrue(len(rendered_jpeg) > 100)

        # Test /api/scan-bill endpoint with mock AI completion
        headers = self.login()
        with mock.patch('app.pick_ai_provider', return_value=('openai', 'fake-key')):
            with mock.patch('app.ai_complete') as mock_ai:
                mock_ai.return_value = '{"supplier_name": "Sharma Distributors", "invoice_no": "INV-2026-99", "grand_total": 15000, "items": []}'
                res = self.client.post('/api/scan-bill', headers=headers, json={
                    'image_b64': pdf_b64,
                    'mime_type': 'application/pdf',
                    'filename': 'invoice.pdf'
                })
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertTrue(data['success'])
                self.assertTrue(data['was_pdf'])
                self.assertEqual(data['data']['supplier_name'], 'Sharma Distributors')
                # Verify that mock_ai received the extracted text in its prompt
                called_prompt = mock_ai.call_args[0][3][0]['content']
                self.assertIn("Sharma Distributors", called_prompt)

    def test_owner_and_staff_lifecycle_and_role_isolation(self):
        # 1. Test Admin Login & /api/me
        admin_headers = self.login()
        me = self.client.get('/api/me').get_json()
        self.assertTrue(me['loggedIn'])
        self.assertEqual(me['role'], 'admin')
        self.assertEqual(me['username'], 'owner')

        # 2. Admin creates a staff member
        staff_res = self.client.post('/api/staff', headers=admin_headers, json={
            'name': 'Rahul Cashier',
            'username': 'rahul_staff',
            'password': 'rahulpassword123',
            'role': 'staff'
        })
        self.assertEqual(staff_res.status_code, 201)

        # 3. Admin can list staff members
        staff_list_res = self.client.get('/api/staff', headers=admin_headers)
        self.assertEqual(staff_list_res.status_code, 200)
        staff_users = staff_list_res.get_json()['staff']
        usernames = [u['username'] for u in staff_users]
        self.assertIn('rahul_staff', usernames)
        rahul_obj = [u for u in staff_users if u['username'] == 'rahul_staff'][0]
        rahul_id = rahul_obj['id']

        # 4. Admin logs out; verify session cleared
        logout_res = self.client.post('/api/logout', headers=admin_headers)
        self.assertEqual(logout_res.status_code, 200)
        self.assertFalse(self.client.get('/api/me').get_json()['loggedIn'])

        # 5. Staff logs in with staff credentials
        staff_login_res = self.client.post('/api/login', json={
            'username': 'rahul_staff',
            'password': 'rahulpassword123'
        })
        self.assertEqual(staff_login_res.status_code, 200)
        staff_login_data = staff_login_res.get_json()
        self.assertTrue(staff_login_data['success'])
        self.assertEqual(staff_login_data['role'], 'staff')
        staff_headers = {'X-CSRF-Token': staff_login_data['csrf_token']}

        # Verify staff session details
        staff_me = self.client.get('/api/me').get_json()
        self.assertTrue(staff_me['loggedIn'])
        self.assertEqual(staff_me['role'], 'staff')
        self.assertEqual(staff_me['username'], 'rahul_staff')

        # 6. Staff can access owner's products & inventory
        prods_res = self.client.get('/api/products')
        self.assertEqual(prods_res.status_code, 200)
        prods = prods_res.get_json()['products']
        self.assertEqual(len(prods), 1)
        self.assertEqual(prods[0]['name'], 'Widget')
        initial_stock = prods[0]['stock']

        # 7. Staff creates a sales bill
        sale_res = self.client.post('/api/sales', headers=staff_headers, json={
            'customer_name': 'Walkin Customer',
            'items': [{'product': 'Widget', 'qty': 2, 'price': 100, 'gst_pct': 18}],
            'paid_amount': 236,
            'payment_mode': 'Cash'
        })
        self.assertEqual(sale_res.status_code, 201)
        sale_data = sale_res.get_json()
        self.assertTrue(sale_data['success'])

        # Verify stock decreased in owner's inventory
        updated_prods = self.client.get('/api/products').get_json()['products']
        self.assertEqual(updated_prods[0]['stock'], initial_stock - 2)

        # Verify bill recorded with created_by = Rahul Cashier
        sales_history = self.client.get('/api/sales').get_json()['sales']
        self.assertEqual(sales_history[0]['customer_name'], 'Walkin Customer')
        self.assertEqual(sales_history[0]['created_by'], 'Rahul Cashier')

        # 8. Staff Security Checks: Staff MUST BE FORBIDDEN (403) from Admin actions
        # a. Staff cannot view or manage staff
        self.assertEqual(self.client.get('/api/staff').status_code, 403)
        self.assertEqual(self.client.post('/api/staff', headers=staff_headers, json={
            'name': 'Hacker', 'username': 'hacker', 'password': '123', 'role': 'admin'
        }).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/staff/{rahul_id}', headers=staff_headers).status_code, 403)
        self.assertEqual(self.client.put(f'/api/staff/{rahul_id}/password', headers=staff_headers, json={'password': 'new'}).status_code, 403)

        # b. Staff cannot alter shop settings or bank details
        self.assertEqual(self.client.post('/api/shop', headers=staff_headers, json={
            'name': 'Hijacked Shop', 'upi_id': 'fraud@upi'
        }).status_code, 403)

        # c. Staff cannot download full database backup or restore
        self.assertEqual(self.client.get('/api/backup').status_code, 403)
        self.assertEqual(self.client.post('/api/restore', headers=staff_headers, json={'backup': {}}).status_code, 403)

        # 9. Test GET /logout redirect route
        logout_get_res = self.client.get('/logout')
        self.assertEqual(logout_get_res.status_code, 302)
        self.assertIn('/login', logout_get_res.headers['Location'])
        self.assertFalse(self.client.get('/api/me').get_json()['loggedIn'])

        # 10. Admin can reset staff password and delete staff
        admin_headers2 = self.login()
        pwd_res = self.client.put(f'/api/staff/{rahul_id}/password', headers=admin_headers2, json={'password': 'newpassword456'})
        self.assertEqual(pwd_res.status_code, 200)

        # Staff logs in with old password (fails) and new password (succeeds)
        self.assertEqual(self.client.post('/api/login', json={'username': 'rahul_staff', 'password': 'rahulpassword123'}).status_code, 401)
        new_login = self.client.post('/api/login', json={'username': 'rahul_staff', 'password': 'newpassword456'})
        self.assertEqual(new_login.status_code, 200)

        # Staff tries to delete themselves or anyone -> blocked 403
        staff_token = {'X-CSRF-Token': new_login.get_json()['csrf_token']}
        self.assertEqual(self.client.delete(f'/api/staff/{rahul_id}', headers=staff_token).status_code, 403)

        # Admin logs back in and deletes staff
        admin_headers3 = self.login()
        del_res = self.client.delete(f'/api/staff/{rahul_id}', headers=admin_headers3)
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(self.client.post('/api/login', json={'username': 'rahul_staff', 'password': 'newpassword456'}).status_code, 401)

    def test_trial_expiration_and_master_admin_controls(self):
        # 1. Register a new shop owner
        reg_res = self.client.post('/api/register', json={
            'name': 'Kapoor Cloth Store',
            'username': 'kapoor_user',
            'password': 'kapoorpassword123',
            'shop_name': 'Kapoor Fabrics',
            'phone': '9811223344'
        })
        self.assertEqual(reg_res.status_code, 201)
        kapoor_token = {'X-CSRF-Token': reg_res.get_json()['csrf_token']}

        # 2. Verify new user gets 7-day trial
        me = self.client.get('/api/me').get_json()
        self.assertEqual(me['plan']['status'], 'trial')
        self.assertTrue(me['plan']['days_left'] >= 6)

        # 3. Create a product and bill during trial (allowed)
        p_res = self.client.post('/api/products', headers=kapoor_token, json={'name': 'Silk Saree', 'sell_price': 500})
        self.assertEqual(p_res.status_code, 201)
        sale_res = self.client.post('/api/sales', headers=kapoor_token, json={
            'customer_name': 'Pooja',
            'items': [{'product': 'Silk Saree', 'qty': 1, 'price': 500, 'gst_pct': 5}],
            'paid_amount': 525
        })
        self.assertEqual(sale_res.status_code, 201)

        # 4. Expire trial manually in database
        db = billzen.get_db()
        db.execute("UPDATE users SET trial_ends_at = '2000-01-01 00:00:00' WHERE username = 'kapoor_user'")
        db.commit()
        db.close()

        # 5. Check /api/me: status is expired
        me_exp = self.client.get('/api/me').get_json()
        self.assertEqual(me_exp['plan']['status'], 'expired')

        # 6. Attempting to create a new sale bill is blocked (403)
        blocked_sale = self.client.post('/api/sales', headers=kapoor_token, json={
            'customer_name': 'Another Customer',
            'items': [{'product': 'Silk Saree', 'qty': 1, 'price': 500}],
        })
        self.assertEqual(blocked_sale.status_code, 403)
        self.assertTrue(blocked_sale.get_json()['trial_expired'])

        # 7. But viewing existing sales and products still works (read-only mode)
        self.assertEqual(self.client.get('/api/sales').status_code, 200)
        self.assertEqual(self.client.get('/api/products').status_code, 200)

        # 8. Non-master user cannot access master admin endpoints (403)
        self.assertEqual(self.client.get('/api/master/shops').status_code, 403)
        self.assertEqual(self.client.post('/api/master/extend-plan', headers=kapoor_token, json={'target_user_id': 1, 'days': 30}).status_code, 403)

        # 9. Designate owner user as master admin and extend kapoor_user plan
        db = billzen.get_db()
        db.execute("UPDATE users SET is_master_admin = 1 WHERE username = 'owner'")
        kapoor_uid = db.execute("SELECT id FROM users WHERE username = 'kapoor_user'").fetchone()['id']
        db.commit()
        db.close()

        master_headers = self.login()
        # Master admin can list shops
        shops_res = self.client.get('/api/master/shops')
        self.assertEqual(shops_res.status_code, 200)
        shop_list = shops_res.get_json()['shops']
        shop_names = [s['shop_name'] for s in shop_list]
        self.assertIn('Kapoor Fabrics', shop_names)

        # Master admin extends plan by 30 days
        ext_res = self.client.post('/api/master/extend-plan', headers=master_headers, json={'target_user_id': kapoor_uid, 'days': 30})
        self.assertEqual(ext_res.status_code, 200)

        # 10. Kapoor user logs in again and can now create sale!
        k_login = self.client.post('/api/login', json={'username': 'kapoor_user', 'password': 'kapoorpassword123'})
        k_headers = {'X-CSRF-Token': k_login.get_json()['csrf_token']}
        k_me = self.client.get('/api/me').get_json()
        self.assertEqual(k_me['plan']['status'], 'active')
        self.assertTrue(k_me['plan']['days_left'] >= 29)

        unblocked_sale = self.client.post('/api/sales', headers=k_headers, json={
            'customer_name': 'Post-Renewal Customer',
            'items': [{'product': 'Silk Saree', 'qty': 1, 'price': 500, 'gst_pct': 5}],
            'paid_amount': 525
        })
        self.assertEqual(unblocked_sale.status_code, 201)

    def test_sales_and_purchase_return_workflows(self):
        headers = self.login()
        # Set Delhi shop GSTIN
        self.client.post('/api/shop', headers=headers, json={
            'name': 'Birla Industrial Supplies',
            'gstin': '07AAAAA0000A1Z5',
            'state': 'Delhi'
        })
        # Add stock product
        p_res = self.client.post('/api/products', headers=headers, json={
            'name': 'Industrial Bearing 6205',
            'hsn': '8482',
            'stock': 50,
            'sell_price': 1000,
            'gst_pct': 18
        })
        self.assertEqual(p_res.status_code, 201)

        # 1. Create a B2B local Sale with 10 units
        sale_res = self.client.post('/api/sales', headers=headers, json={
            'customer_name': 'Tata Motors Delhi Ltd',
            'customer_gstin': '07AABCT1234A1Z1',
            'customer_state': 'Delhi',
            'items': [{'product': 'Industrial Bearing 6205', 'qty': 10, 'price': 1000, 'gst_pct': 18, 'hsn': '8482'}],
            'paid_amount': 11800,
            'bill_category': 'pakka'
        })
        self.assertEqual(sale_res.status_code, 201)
        bill_id = sale_res.get_json()['bill_id']

        # Check stock reduced to 40
        prod = self.client.get('/api/products').get_json()['products'][0]
        self.assertEqual(prod['stock'], 40)

        # 2. Test Sales Return Context
        ctx_res = self.client.get(f'/api/sales/{bill_id}/return-context')
        self.assertEqual(ctx_res.status_code, 200)
        ctx = ctx_res.get_json()
        self.assertEqual(ctx['items'][0]['original_qty'], 10)
        self.assertEqual(ctx['items'][0]['already_returned_qty'], 0)
        self.assertEqual(ctx['items'][0]['returnable_qty'], 10)

        # 3. Validation: Try to return 15 units (exceeds returnable) -> blocked 400
        bad_return = self.client.post('/api/sales/returns', headers=headers, json={
            'original_bill_id': bill_id,
            'items': [{'product': 'Industrial Bearing 6205', 'return_qty': 15, 'price': 1000, 'gst_pct': 18, 'hsn': '8482'}],
            'reason': 'Over ordered'
        })
        self.assertEqual(bad_return.status_code, 400)

        # 4. Process valid partial return of 3 units
        ret_res = self.client.post('/api/sales/returns', headers=headers, json={
            'original_bill_id': bill_id,
            'items': [{'product': 'Industrial Bearing 6205', 'return_qty': 3, 'price': 1000, 'gst_pct': 18, 'hsn': '8482'}],
            'reason': 'Defective',
            'refund_mode': 'Credit Note'
        })
        self.assertEqual(ret_res.status_code, 201)
        ret_data = ret_res.get_json()
        self.assertTrue(ret_data['credit_note_no'].startswith('CN-'))
        self.assertEqual(ret_data['total_taxable'], 3000.0)
        self.assertEqual(ret_data['total_cgst'], 270.0) # 9%
        self.assertEqual(ret_data['total_sgst'], 270.0) # 9%
        self.assertEqual(ret_data['total_igst'], 0.0)
        self.assertEqual(ret_data['total_amount'], 3540.0)

        # Verify stock restored to 43 (40 + 3)
        prod = self.client.get('/api/products').get_json()['products'][0]
        self.assertEqual(prod['stock'], 43)

        # 5. Check remaining returnable qty is now 7
        ctx2 = self.client.get(f'/api/sales/{bill_id}/return-context').get_json()
        self.assertEqual(ctx2['items'][0]['already_returned_qty'], 3)
        self.assertEqual(ctx2['items'][0]['returnable_qty'], 7)

        # 6. Purchase Return (Debit Note) Workflow
        pur_res = self.client.post('/api/purchases', headers=headers, json={
            'supplier_name': 'SKF India Ltd',
            'supplier_gstin': '27AABCS9876B1Z3', # Maharashtra - interstate
            'bill_date': '2026-10-05',
            'invoice_no': 'SKF-INV-9901',
            'items': [{'product': 'Industrial Bearing 6205', 'qty': 20, 'buy_price': 800, 'gst_pct': 18, 'hsn_code': '8482'}],
            'notes': 'Factory stock batch'
        })
        self.assertEqual(pur_res.status_code, 201)

        # Stock is now 43 + 20 = 63
        prod = self.client.get('/api/products').get_json()['products'][0]
        self.assertEqual(prod['stock'], 63)

        # Test Purchase Return Context
        pur_ctx = self.client.get('/api/purchases/SKF-INV-9901/return-context').get_json()
        self.assertEqual(pur_ctx['items'][0]['returnable_qty'], 20)

        # Try to return 25 units -> blocked 400
        bad_pur_ret = self.client.post('/api/purchases/returns', headers=headers, json={
            'original_purchase_ref': 'SKF-INV-9901',
            'items': [{'product': 'Industrial Bearing 6205', 'return_qty': 25, 'price': 800, 'gst_pct': 18}],
            'reason': 'Damaged'
        })
        self.assertEqual(bad_pur_ret.status_code, 400)

        # Process valid Purchase Return of 5 units (Interstate -> IGST reversal)
        dn_res = self.client.post('/api/purchases/returns', headers=headers, json={
            'original_purchase_ref': 'SKF-INV-9901',
            'items': [{'product': 'Industrial Bearing 6205', 'return_qty': 5, 'price': 800, 'gst_pct': 18, 'hsn': '8482'}],
            'reason': 'Damaged in transit'
        })
        self.assertEqual(dn_res.status_code, 201)
        dn_data = dn_res.get_json()
        self.assertTrue(dn_data['debit_note_no'].startswith('DN-'))
        self.assertEqual(dn_data['total_taxable'], 4000.0)
        self.assertEqual(dn_data['total_igst'], 720.0) # 18% interstate
        self.assertEqual(dn_data['total_cgst'], 0.0)

        # Verify stock deducted: 63 - 5 = 58
        prod = self.client.get('/api/products').get_json()['products'][0]
        self.assertEqual(prod['stock'], 58)

    def test_gst_return_center_and_reconciliation(self):
        headers = self.login()
        # Fetch GST return center for current month
        now_month = '2026-10'
        res = self.client.get(f'/api/gst/return-center?month={now_month}')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertIn('gstr1', data)
        self.assertIn('gstr3b', data)
        self.assertIn('reconciliation', data)
        self.assertIn('tax_rules', data)

        # Verify GSTR-1 sections
        gstr1 = data['gstr1']
        self.assertIn('b2b', gstr1)
        self.assertIn('cdnr', gstr1)
        self.assertIn('hsn', gstr1)
        self.assertIn('doc_issue', gstr1)

        # Verify GSTR-3B structure
        gstr3b = data['gstr3b']
        self.assertIn('table_3_1', gstr3b)
        self.assertIn('table_4_itc', gstr3b)
        self.assertIn('table_6_1', gstr3b)

        # Test official JSON download
        json_res = self.client.get(f'/api/gst/return-center/export/gstr1-json?month={now_month}')
        self.assertEqual(json_res.status_code, 200)
        self.assertEqual(json_res.content_type, 'application/json')
        gstr1_json = json_res.get_json()
        self.assertIn('gstin', gstr1_json)
        self.assertIn('fp', gstr1_json)

        # Test GSTR-3B CSV download
        csv_res = self.client.get(f'/api/gst/return-center/export/gstr3b-csv?month={now_month}')
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn(b'GSTR-3B', csv_res.data)

    def test_bi_agent_and_tally_mappings(self):
        headers = self.login()
        # 1. Test deterministic BI Agent queries
        q_res = self.client.post('/api/ai/query', headers=headers, json={'query': 'What is the GST summary?'})
        self.assertEqual(q_res.status_code, 200)
        self.assertTrue(len(q_res.get_json()['answer']) > 0)

        # 2. Test Tally Ledger Mappings GET and POST
        get_map = self.client.get('/api/tally/ledger-mappings')
        self.assertEqual(get_map.status_code, 200)
        mappings = get_map.get_json()['mappings']
        self.assertIn('sale_local_18', mappings)

        post_map = self.client.post('/api/tally/ledger-mappings', headers=headers, json={
            'sales_ledger_cgst_sgst': 'Sales Account GST 18%',
            'credit_note_ledger': 'Sales Return Account'
        })
        self.assertEqual(post_map.status_code, 200)

        # Verify persisted
        updated_map = self.client.get('/api/tally/ledger-mappings').get_json()['mappings']
        self.assertEqual(updated_map['sales_ledger_cgst_sgst'], 'Sales Account GST 18%')
        self.assertEqual(updated_map['credit_note_ledger'], 'Sales Return Account')


if __name__ == '__main__':
    unittest.main()


