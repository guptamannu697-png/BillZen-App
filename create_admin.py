#!/usr/bin/env python3
"""
BillZen - Secure Master Admin Provisioning Utility
Safely creates a dedicated Master Admin account on an existing database
without altering any schema, existing users, shops, sales, or business data.
"""
import os
import sys
import getpass

# Ensure we import from current directory
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import app as billzen

def main():
    print("=" * 60)
    print("  BillZen - Dedicated Master Admin Provisioning Utility")
    print("=" * 60)

    if len(sys.argv) >= 3:
        username = sys.argv[1].strip()
        password = sys.argv[2].strip()
        name = sys.argv[3].strip() if len(sys.argv) > 3 else "Master Administrator"
    else:
        try:
            username = input("Enter Master Admin Username: ").strip()
            if not username:
                print("Error: Username cannot be empty.", file=sys.stderr)
                sys.exit(1)
            password = getpass.getpass("Enter Master Admin Password (min 8 chars): ").strip()
            if len(password) < 8:
                print("Error: Password must be at least 8 characters.", file=sys.stderr)
                sys.exit(1)
            name = input("Enter Full Name [Master Administrator]: ").strip() or "Master Administrator"
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)

    if len(password) < 8:
        print("Error: Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    os.environ['BILLZEN_BOOTSTRAP_ADMIN_USERNAME'] = username
    os.environ['BILLZEN_BOOTSTRAP_ADMIN_PASSWORD'] = password
    os.environ['BILLZEN_BOOTSTRAP_ADMIN_NAME'] = name

    db_path = billzen.DATABASE
    print(f"Target Database: {db_path}")

    lock_path = db_path + ".init.lock"
    with billzen._DatabaseInitLock(lock_path, timeout=60):
        db = billzen.get_db()
        try:
            # Check if user already exists
            cur = db.execute("SELECT id, is_master_admin FROM users WHERE username = ?", (username,))
            existing = cur.fetchone()
            if existing:
                if existing['is_master_admin']:
                    print(f"Info: Master Admin '{username}' already exists.")
                    sys.exit(0)
                else:
                    print(f"Error: User '{username}' already exists as an ordinary account.", file=sys.stderr)
                    print("To prevent account takeover, existing ordinary accounts cannot be elevated.", file=sys.stderr)
                    print("Please choose a distinct dedicated username (e.g. 'founder_admin').", file=sys.stderr)
                    sys.exit(1)

            billzen.bootstrap_admin_account(db)
            print(f"Success! Master Admin '{username}' created successfully.")
            print("You can now log in and access the full Master Client Manager.")
        finally:
            db.close()

if __name__ == '__main__':
    main()
