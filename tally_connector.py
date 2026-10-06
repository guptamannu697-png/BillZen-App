"""
BillZen AI to Tally Desktop Connector (Windows)
================================================
Background synchronization service that connects BillZen AI to TallyPrime / Tally.ERP 9.
Provides Livekeeping-style live sync, auto-polling daemon, and one-click XML import.

Usage:
    python tally_connector.py              (Interactive Menu)
    python tally_connector.py --test       (Test connection to Tally on port 9000)
    python tally_connector.py --sync       (Sync all unsynced pakka bills to Tally now)
    python tally_connector.py --daemon     (Run continuous background sync every N seconds)
    python tally_connector.py --export     (Export Tally V2 Item Invoice XML file)
"""

import sys
import os
import time
import argparse
import sqlite3
import urllib.request
import urllib.error
import re
from datetime import datetime

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Import Tally Engine
try:
    from tally_engine import generate_tally_v2_xml, DEFAULT_LEDGER_MAP
except ImportError:
    print("[ERROR] tally_engine.py not found in the current directory.")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "billzen.db")
DEFAULT_TALLY_URL = "http://127.0.0.1:9000"


def get_db_connection():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def check_tally_connection(tally_url=DEFAULT_TALLY_URL, timeout=4):
    """Checks if TallyPrime XML server is active on the given port."""
    test_envelope = """<ENVELOPE>
<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>List of Companies</ID></HEADER>
<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES></DESC></BODY>
</ENVELOPE>"""
    try:
        req = urllib.request.Request(
            tally_url,
            data=test_envelope.encode('utf-8'),
            headers={'Content-Type': 'application/xml; charset=utf-8'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('utf-8', errors='ignore')
            return True, f"Connected to Tally successfully! (Status: {resp.status})"
    except urllib.error.URLError as e:
        return False, f"Cannot connect to Tally at {tally_url}: {e.reason}"
    except Exception as e:
        return False, f"Connection error: {str(e)}"


def get_unsynced_data(conn):
    """Fetches shop settings, sales, and purchases from database."""
    cur = conn.cursor()
    company_name = "My Shop"
    try:
        cur.execute("SELECT name FROM shops LIMIT 1")
        shop_row = cur.fetchone()
        if shop_row and shop_row['name']:
            company_name = shop_row['name']
    except Exception:
        pass

    # Pakka sales (exclude kacha and cancelled)
    cur.execute("SELECT * FROM sales WHERE cancelled=0 AND (bill_category != 'kacha' OR bill_category IS NULL) ORDER BY sale_date ASC")
    sales_rows = [dict(r) for r in cur.fetchall()]
    for s in sales_rows:
        cur.execute("SELECT * FROM sale_items WHERE sale_id=?", (s['id'],))
        s['items'] = [dict(r) for r in cur.fetchall()]

    # Purchases
    cur.execute("SELECT * FROM purchases ORDER BY bill_date ASC")
    pur_rows = [dict(r) for r in cur.fetchall()]
    for p in pur_rows:
        cur.execute("SELECT * FROM purchase_items WHERE purchase_id=?", (p['id'],))
        p['items'] = [dict(r) for r in cur.fetchall()]

    # Sales Returns (Credit Notes)
    cn_rows = []
    try:
        cur.execute("SELECT * FROM sales_returns ORDER BY return_date ASC")
        cn_rows = [dict(r) for r in cur.fetchall()]
        for cn in cn_rows:
            cur.execute("SELECT * FROM sales_return_items WHERE return_id=?", (cn['id'],))
            cn['items'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # Purchase Returns (Debit Notes)
    dn_rows = []
    try:
        cur.execute("SELECT * FROM purchase_returns ORDER BY return_date ASC")
        dn_rows = [dict(r) for r in cur.fetchall()]
        for dn in dn_rows:
            cur.execute("SELECT * FROM purchase_return_items WHERE return_id=?", (dn['id'],))
            dn['items'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    return company_name, sales_rows, pur_rows, cn_rows, dn_rows


def push_xml_to_tally(xml_data, tally_url=DEFAULT_TALLY_URL, timeout=12):
    """Sends XML envelope to Tally HTTP server and parses creation and error counts."""
    req = urllib.request.Request(
        tally_url,
        data=xml_data.encode('utf-8'),
        headers={'Content-Type': 'application/xml; charset=utf-8'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        tally_resp = resp.read().decode('utf-8', errors='ignore')
        created_matches = re.findall(r'<CREATED>(\d+)</CREATED>', tally_resp)
        altered_matches = re.findall(r'<ALTERED>(\d+)</ALTERED>', tally_resp)
        errors_matches = re.findall(r'<ERRORS>(\d+)</ERRORS>', tally_resp)

        created = sum(int(c) for c in created_matches) if created_matches else 0
        altered = sum(int(a) for a in altered_matches) if altered_matches else 0
        errors = sum(int(e) for e in errors_matches) if errors_matches else 0

        # Check for error descriptions
        err_lines = re.findall(r'<LINEERROR>(.*?)</LINEERROR>', tally_resp, re.DOTALL)
        return {
            "success": errors == 0,
            "created": created,
            "altered": altered,
            "errors": errors,
            "error_details": [e.strip() for e in err_lines[:5]],
            "raw": tally_resp
        }


def sync_now(tally_url=DEFAULT_TALLY_URL, category='all'):
    """Performs an immediate sync to Tally."""
    print(f"\n[⚡ LIVE SYNC] Checking connection to Tally on {tally_url}...")
    is_ok, msg = check_tally_connection(tally_url)
    if not is_ok:
        print(f"❌ Connection failed: {msg}")
        print("\n💡 HOW TO ENABLE TALLY HTTP XML SERVER:")
        print("  1. In TallyPrime, press F1 (Help) -> Settings -> Connectivity")
        print("  2. Client/Server Configuration -> Tally is acting as: 'Both'")
        print("  3. Set Port to 9000, restart Tally, and load your Company.")
        print("  4. Alternatively, use 'Export XML' to import manually without opening ports!")
        return False

    print(f"✅ {msg}")
    print("[⚡ LIVE SYNC] Fetching tax invoices from BillZen database...")
    try:
        conn = get_db_connection()
        company_name, sales, purchases, credit_notes, debit_notes = get_unsynced_data(conn)
        conn.close()
    except Exception as e:
        print(f"❌ Failed to read BillZen database: {e}")
        return False

    print(f"📊 Found {len(sales)} Sales Invoices, {len(purchases)} Purchases, {len(credit_notes)} Credit Notes, {len(debit_notes)} Debit Notes for company '{company_name}'.")
    if not sales and not purchases and not credit_notes and not debit_notes:
        print("ℹ️ No vouchers to sync.")
        return True

    print("[⚡ LIVE SYNC] Generating Tally V2 Item Invoice XML with rate-wise ledgers...")
    res = generate_tally_v2_xml(company_name, sales, purchases, category=category, credit_notes_list=credit_notes, debit_notes_list=debit_notes)
    xml_data, s_cnt, p_cnt = res[0], res[1], res[2]
    print(f"📦 Vouchers prepared: {s_cnt} Sales, {p_cnt} Purchases, {len(credit_notes)} Credit Notes, {len(debit_notes)} Debit Notes.")

    print(f"[⚡ LIVE SYNC] Pushing directly into TallyPrime ({tally_url})...")
    try:
        result = push_xml_to_tally(xml_data, tally_url)
        if result["created"] > 0 or result["altered"] > 0:
            print(f"\n🎉 SUCCESS: Synced to TallyPrime!")
            print(f"   • Vouchers Created : {result['created']}")
            print(f"   • Vouchers Altered : {result['altered']}")
            print(f"   • Errors           : {result['errors']}")
            if result['errors'] > 0 and result['error_details']:
                print(f"   ⚠️ Error notices: {result['error_details']}")
            print("\n💡 Open your Tally Daybook to verify the newly imported vouchers!")
            return True
        else:
            print(f"\n⚠️ Tally response received, but 0 vouchers created.")
            print(f"   • Errors: {result['errors']}")
            if result['error_details']:
                print(f"   • Details: {result['error_details']}")
            return False
    except Exception as e:
        print(f"❌ HTTP request to Tally failed: {e}")
        return False


def run_daemon(tally_url=DEFAULT_TALLY_URL, interval=30):
    """Runs a continuous background sync service polling every N seconds."""
    print("=" * 65)
    print(f" 🚀 BillZen Live Tally Sync Daemon Started")
    print(f" Target: {tally_url} | Poll Interval: {interval}s")
    print(f" Press Ctrl + C to stop the daemon at any time.")
    print("=" * 65)

    while True:
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now_str}] Polling for new vouchers...")
            is_ok, _ = check_tally_connection(tally_url, timeout=3)
            if is_ok:
                sync_now(tally_url)
            else:
                print(f"⚠️ Tally not detected on {tally_url}. Retrying in {interval}s...")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n🛑 Live Tally Sync Daemon stopped by user.")
            break
        except Exception as e:
            print(f"⚠️ Unexpected error in daemon: {e}")
            time.sleep(interval)


def export_xml_file(output_path=None, category='all'):
    """Exports Tally V2 Item Invoice XML file to disk."""
    try:
        conn = get_db_connection()
        company_name, sales, purchases, credit_notes, debit_notes = get_unsynced_data(conn)
        conn.close()
    except Exception as e:
        print(f"❌ Failed to read BillZen database: {e}")
        return

    if not output_path:
        today_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"BillZen_Tally_ItemInvoice_{today_str}.xml"

    res = generate_tally_v2_xml(company_name, sales, purchases, category=category, credit_notes_list=credit_notes, debit_notes_list=debit_notes)
    xml_data, s_cnt, p_cnt = res[0], res[1], res[2]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_data)

    print(f"\n✅ Tally XML exported successfully!")
    print(f"   • File: {os.path.abspath(output_path)}")
    print(f"   • Sales Vouchers: {s_cnt}")
    print(f"   • Purchase Vouchers: {p_cnt}")
    print(f"   • Credit Notes (Returns): {len(credit_notes)}")
    print(f"   • Debit Notes (Returns): {len(debit_notes)}")
    print("\n💡 To Import in Tally:")
    print("   1. Open TallyPrime -> Alt + O (Import) -> Vouchers")
    print(f"   2. Select file: {output_path}")


def interactive_menu():
    print("""
╔═══════════════════════════════════════════════════════════════════╗
║                   BillZen AI ↔ Tally Connector                    ║
║         Senior CA & Accountant Automated Integration Suite         ║
╚═══════════════════════════════════════════════════════════════════╝
    1. 🔍 Test Connection to Tally (Port 9000)
    2. ⚡ Live Sync Bills to Tally Now (Option 2)
    3. 📥 Export Tally Item Invoice XML File (Option 1)
    4. 🔄 Start Auto-Sync Background Daemon (Livekeeping Mode)
    5. 🚪 Exit
""")
    try:
        choice = input("Select an option [1-5]: ").strip()
    except EOFError:
        choice = '5'
    if choice == '1':
        print(f"\nPinging Tally on {DEFAULT_TALLY_URL}...")
        ok, msg = check_tally_connection()
        if ok:
            print(f"✅ {msg}")
        else:
            print(f"❌ {msg}")
    elif choice == '2':
        sync_now()
    elif choice == '3':
        export_xml_file()
    elif choice == '4':
        try:
            interval = input("Enter polling interval in seconds [default 30]: ").strip()
        except EOFError:
            interval = '30'
        sec = int(interval) if interval.isdigit() else 30
        run_daemon(interval=sec)
    elif choice == '5':
        print("Goodbye!")
        sys.exit(0)
    else:
        print("Invalid option!")


def sync_from_cloud(server_url="http://127.0.0.1:5000", token="", tally_url=DEFAULT_TALLY_URL, max_retries=3):
    """
    SECURE CONNECTOR ARCHITECTURE:
    1. Authenticates securely to BillZen Cloud using pairing token (X-Connector-Token).
    2. Downloads tenant-scoped vouchers.
    3. Pushes locally to customer's PC TallyPrime at localhost:9000.
    4. Reports sync execution status back to BillZen Cloud.
    BillZen cloud never touches local customer network addresses.
    """
    import json, socket
    headers = {
        'X-Connector-Token': token,
        'Content-Type': 'application/json'
    }
    machine_name = socket.gethostname() or 'Windows Client'
    
    # 1. Check local Tally connection first
    is_tally_ok, msg = check_tally_connection(tally_url, timeout=3)
    if not is_tally_ok:
        print(f"❌ Local TallyPrime unreachable on {tally_url}: {msg}")
        return False
        
    # 2. Fetch vouchers from Cloud with retries
    endpoint = f"{server_url.rstrip('/')}/api/tally/connector/vouchers"
    data = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(endpoint, headers=headers, method='GET')
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                break
        except Exception as e:
            print(f"⚠️ Attempt {attempt}/{max_retries}: Failed to connect to BillZen Cloud: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
            else:
                print("❌ Max retries reached. Cannot connect to BillZen Cloud.")
                return False

    if not data or not data.get('success'):
        print(f"❌ Failed to retrieve vouchers: {data.get('error') if data else 'Unknown error'}")
        return False

    xml_data = data.get('xml_data', '')
    if not xml_data:
        print("ℹ️ No vouchers pending in BillZen.")
        return True

    print(f"📦 Received {data.get('sales_count', 0)} sales, {data.get('purchases_count', 0)} purchases from '{data.get('company_name')}'.")
    print(f"⚡ Pushing vouchers to local TallyPrime ({tally_url})...")
    res = push_xml_to_tally(xml_data, tally_url)
    
    # Report back to Cloud
    report_endpoint = f"{server_url.rstrip('/')}/api/tally/connector/report"
    try:
        report_payload = json.dumps({
            "machine_name": machine_name,
            "created": res.get("created", 0),
            "errors": res.get("errors", 0),
            "status": "success" if res.get("created", 0) > 0 else "completed"
        }).encode('utf-8')
        r_req = urllib.request.Request(report_endpoint, data=report_payload, headers=headers, method='POST')
        urllib.request.urlopen(r_req, timeout=8)
    except Exception as e:
        print(f"⚠️ Note: Could not report heartbeat to cloud: {e}")

    print(f"🎉 Sync Complete! Created: {res.get('created', 0)}, Errors: {res.get('errors', 0)}")
    return True


def main():
    parser = argparse.ArgumentParser(description="BillZen AI Tally Desktop Connector")
    parser.add_argument("--test", action="store_true", help="Test connectivity to TallyPrime port 9000")
    parser.add_argument("--sync", action="store_true", help="Sync all bills to Tally now")
    parser.add_argument("--daemon", action="store_true", help="Run background daemon like Livekeeping")
    parser.add_argument("--interval", type=int, default=30, help="Daemon poll interval in seconds")
    parser.add_argument("--export", action="store_true", help="Export Tally XML file")
    parser.add_argument("--tally-url", default=DEFAULT_TALLY_URL, help="Tally server URL")
    parser.add_argument("--category", default="all", help="GST Category filter (local_18, local_5, igst_18, etc.)")
    parser.add_argument("--server", default="http://127.0.0.1:5000", help="BillZen Cloud Server URL")
    parser.add_argument("--token", default=None, help="BillZen Desktop Connector Pairing Token")

    args = parser.parse_args()

    if args.test:
        ok, msg = check_tally_connection(args.tally_url)
        print(f"{'✅' if ok else '❌'} {msg}")
    elif args.sync:
        if args.token:
            sync_from_cloud(args.server, args.token, args.tally_url)
        else:
            sync_now(args.tally_url, args.category)
    elif args.daemon:
        if args.token:
            print("=" * 65)
            print(f" 🚀 BillZen Cloud-to-Tally Connector Daemon Started")
            print(f" Server: {args.server} | Target: {args.tally_url} | Poll: {args.interval}s")
            print("=" * 65)
            while True:
                try:
                    sync_from_cloud(args.server, args.token, args.tally_url)
                    time.sleep(args.interval)
                except KeyboardInterrupt:
                    print("\n🛑 Stopped by user.")
                    break
                except Exception as e:
                    print(f"⚠️ Error: {e}")
                    time.sleep(args.interval)
        else:
            run_daemon(args.tally_url, args.interval)
    elif args.export:
        export_xml_file(category=args.category)
    else:
        interactive_menu()


if __name__ == "__main__":
    main()
