import os
import io
import json
import sqlite3
import pytest
import app as billzen

import shutil
import tempfile

TEST_CSRF_TOKEN = 'test-csrf-token-12345'

@pytest.fixture
def isolated_db():
    orig_db = billzen.DATABASE
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, 'test_isolated_purchase.db')
    
    # Copy real billzen.db to isolated temp DB so tests run with full realistic data
    # without touching or modifying the live billzen.db!
    shutil.copyfile('billzen.db', temp_db_path)
    billzen.DATABASE = os.path.abspath(temp_db_path)
    
    yield temp_db_path
    
    # Teardown: Restore original DATABASE and remove temp directory
    billzen.DATABASE = orig_db
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

@pytest.fixture
def client(isolated_db):
    billzen.app.config['TESTING'] = True
    with billzen.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['username'] = 'sachin'
            sess['role'] = 'owner'
            sess['csrf_token'] = TEST_CSRF_TOKEN
        yield client

@pytest.fixture
def auth_headers():
    return {'X-CSRF-Token': TEST_CSRF_TOKEN}

def test_purchase_duplicate_prevention(client, auth_headers, isolated_db):
    # 1. Verify historical purchases ID 12 and ID 15 exist in DB
    con = sqlite3.connect(isolated_db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    p12 = cur.execute("SELECT * FROM purchases WHERE id=12").fetchone()
    p15 = cur.execute("SELECT * FROM purchases WHERE id=15").fetchone()
    assert p12 is not None, "ID 12 must exist"
    assert p15 is not None, "ID 15 must exist"
    assert p12['invoice_no'] == '1788'
    assert p15['invoice_no'] == '1788'

    items_12 = [dict(r) for r in cur.execute("SELECT product, hsn_code, qty, buy_price, gst_pct FROM purchase_items WHERE purchase_id=12").fetchall()]
    items_15 = [dict(r) for r in cur.execute("SELECT product, hsn_code, qty, buy_price, gst_pct FROM purchase_items WHERE purchase_id=15").fetchall()]
    con.close()

    # 2. Attempt to ACCIDENTALLY RE-SAVE the EXACT bill of ID 12 (same invoice 1788, same supplier, same items)
    # MUST BE HARD BLOCKED with HTTP 400
    dup_payload_12 = {
        'supplier_name': 'KRISHNA MEGAMART',
        'supplier_gstin': '06CEAPS1373G1ZL',
        'invoice_no': '1788',
        'bill_date': '2023-10-17',
        'items': items_12
    }
    resp12 = client.post('/api/purchases', headers=auth_headers, json=dup_payload_12)
    assert resp12.status_code == 400
    data12 = resp12.get_json()
    assert data12['success'] is False
    assert "Duplicate Purchase Bill!" in data12['error']
    assert "1788" in data12['error']
    assert "KRISHNA MEGAMART" in data12['error']

    # 3. Attempt to ACCIDENTALLY RE-SAVE the EXACT bill of ID 15 (same invoice 1788, same supplier, same items)
    # MUST BE HARD BLOCKED with HTTP 400
    dup_payload_15 = {
        'supplier_name': 'krishna megamart',
        'supplier_gstin': '06ceafps1373g1zl',
        'invoice_no': ' 1788 ',
        'bill_date': '2023-10-17',
        'items': items_15
    }
    resp15 = client.post('/api/purchases', headers=auth_headers, json=dup_payload_15)
    assert resp15.status_code == 400
    assert "Duplicate Purchase Bill!" in resp15.get_json()['error']

    # 3B. Attempt to save an OCR RE-SCAN of invoice 1788 with OCR noise in quantities/rates
    # (Same invoice 1788, date 2023-10-17, KRISHNA MEGAMART, 90% same products)
    # MUST BE HARD BLOCKED with HTTP 400
    ocr_variation_payload = {
        'supplier_name': 'KRISHNA MEGAMART',
        'supplier_gstin': '06CEAPSI373G1ZL',
        'invoice_no': '1788',
        'bill_date': '2023-10-17',
        'total': 65795.74,
        'items': [
            {'product': 'EASY COOK INDUCTION COOKTOP NIC19313', 'qty': 28.0, 'buy_price': 224.58},
            {'product': 'EASY COOK INDUCTION COOKTOP NIC19314', 'qty': 48.0, 'buy_price': 224.58},
            {'product': 'EASY COOK INDUCTION NIC19314', 'qty': 48.0, 'buy_price': 224.58},
            {'product': 'INDUCTION COOKTOP NIC19315', 'qty': 48.0, 'buy_price': 224.58},
            {'product': 'SKB2014 ROMANIA ROCK GLASS SET', 'qty': 12.0, 'buy_price': 170.0},
            {'product': 'SKB 2233 GLASS SET', 'qty': 42.0, 'buy_price': 42.0},
            {'product': 'SKB2016 DRINK GLASS', 'qty': 48.0, 'buy_price': 38.14},
            {'product': 'SKB1005 INFINITY GOBLET SET OF 2PCS', 'qty': 48.0, 'buy_price': 114.41},
            {'product': 'SKB 2692 5PC PARKA SO CONTAINER', 'qty': 210.0, 'buy_price': 72.03},
            {'product': 'SKB 1001 INFINITY GOBLET 435ML SET', 'qty': 8.0, 'buy_price': 114.41}
        ]
    }
    resp_ocr = client.post('/api/purchases', headers=auth_headers, json=ocr_variation_payload)
    assert resp_ocr.status_code == 400
    assert "Duplicate Purchase Bill!" in resp_ocr.get_json()['error']

    created_ids = []
    try:
        # 4. Allow DIFFERENT genuine part-bill for KRISHNA MEGAMART with Invoice No 1788 but DIFFERENT line items
        new_part_payload = {
            'supplier_name': 'KRISHNA MEGAMART',
            'supplier_gstin': '06CEAPS1373G1ZL',
            'invoice_no': '1788',
            'bill_date': '2023-10-17',
            'items': [
                {'product': 'GENUINE TEST PART ITEM 999', 'qty': 3, 'buy_price': 150.0, 'gst_pct': 18.0}
            ]
        }
        resp_part = client.post('/api/purchases', headers=auth_headers, json=new_part_payload)
        assert resp_part.status_code == 201, f"Separate part bill should be allowed: {resp_part.get_json()}"
        part_pid = resp_part.get_json()['id']
        created_ids.append(part_pid)

        # 5. Adding the SAME part-bill AGAIN must be blocked as duplicate
        resp_part_dup = client.post('/api/purchases', headers=auth_headers, json=new_part_payload)
        assert resp_part_dup.status_code == 400
        assert "Duplicate Purchase Bill!" in resp_part_dup.get_json()['error']

        # 6. Buying products under a NEW invoice number (e.g. 1789) must be allowed
        new_inv_payload = {
            'supplier_name': 'KRISHNA MEGAMART',
            'supplier_gstin': '06CEAPS1373G1ZL',
            'invoice_no': '1789',
            'bill_date': '2026-10-12',
            'items': [
                {'product': 'GENUINE TEST PART ITEM 999', 'qty': 3, 'buy_price': 150.0, 'gst_pct': 18.0}
            ]
        }
        resp_new_inv = client.post('/api/purchases', headers=auth_headers, json=new_inv_payload)
        assert resp_new_inv.status_code == 201
        new_inv_pid = resp_new_inv.get_json()['id']
        created_ids.append(new_inv_pid)

        # 7. Same invoice number for a DIFFERENT supplier must be allowed
        diff_sup_payload = {
            'supplier_name': 'HARYANA WHOLESALE TRADERS',
            'supplier_gstin': '06AABCH1234F1Z8',
            'invoice_no': '1788',
            'bill_date': '2023-10-17',
            'items': [
                {'product': 'GENUINE TEST PART ITEM 999', 'qty': 3, 'buy_price': 150.0, 'gst_pct': 18.0}
            ]
        }
        resp_diff_sup = client.post('/api/purchases', headers=auth_headers, json=diff_sup_payload)
        assert resp_diff_sup.status_code == 201
        diff_pid = resp_diff_sup.get_json()['id']
        created_ids.append(diff_pid)

        # 8. Editing ID 12 and ID 15 without conflict must succeed (they do not falsely block themselves)
        resp_edit_12 = client.put('/api/purchases/12', headers=auth_headers, json={
            'supplier_name': 'KRISHNA MEGAMART',
            'supplier_gstin': '06CEAPS1373G1ZL',
            'invoice_no': '1788',
            'items': items_12
        })
        assert resp_edit_12.status_code == 200, f"Editing ID 12 failed: {resp_edit_12.get_json()}"

        resp_edit_15 = client.put('/api/purchases/15', headers=auth_headers, json={
            'supplier_name': 'KRISHNA MEGAMART',
            'supplier_gstin': '06CEAFPS1373G1ZL',
            'invoice_no': '1788',
            'items': items_15
        })
        assert resp_edit_15.status_code == 200, f"Editing ID 15 failed: {resp_edit_15.get_json()}"

        # 9. Editing diff_pid to become an EXACT duplicate of ID 12 must be blocked
        resp_edit_collide = client.put(f'/api/purchases/{diff_pid}', headers=auth_headers, json={
            'supplier_name': 'KRISHNA MEGAMART',
            'supplier_gstin': '06CEAPS1373G1ZL',
            'invoice_no': '1788',
            'items': items_12
        })
        assert resp_edit_collide.status_code == 400
        assert "Duplicate Purchase Bill!" in resp_edit_collide.get_json()['error']

    finally:
        # 10. Clean up temporary test purchases created in this test directly in isolated DB
        con_clean = sqlite3.connect(isolated_db)
        cur_clean = con_clean.cursor()
        for pid in created_ids:
            cur_clean.execute("DELETE FROM purchase_items WHERE purchase_id=?", (pid,))
            cur_clean.execute("DELETE FROM purchases WHERE id=?", (pid,))
        cur_clean.execute("DELETE FROM products WHERE name = 'GENUINE TEST PART ITEM 999'")
        cur_clean.execute("DELETE FROM suppliers WHERE name = 'HARYANA WHOLESALE TRADERS'")
        con_clean.commit()
        con_clean.close()
