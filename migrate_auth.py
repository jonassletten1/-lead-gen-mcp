#!/usr/bin/env python3
"""
Migrate existing users to Supabase Auth.
Run ONCE after adding SUPABASE_SERVICE_KEY to .env.
Safe to re-run — skips users already in Supabase Auth.
"""

import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_SERVICE_KEY:
    print("\nERROR: SUPABASE_SERVICE_KEY not set in .env")
    print("Get it from: Supabase Dashboard → Project Settings → API → service_role key")
    exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Known passwords for the seeded users
KNOWN_PASSWORDS = {
    "admin@leadflow.io":  "admin123",
    "sarah@leadflow.io":  "rep123",
    "marcus@leadflow.io": "rep123",
    "priya@leadflow.io":  "rep123",
    "tom@leadflow.io":    "rep123",
}

print("\n── Migrating users to Supabase Auth ───────────────────────────────")
users = sb.table("users").select("email").execute().data

for user in users:
    email = user["email"]
    password = KNOWN_PASSWORDS.get(email)
    if not password:
        print(f"  skip  {email} — password unknown, ask them to use Forgot Password")
        continue
    try:
        sb.auth.admin.create_user({
            "email":         email,
            "password":      password,
            "email_confirm": True,   # skip confirmation for existing users
        })
        print(f"  created  {email}")
    except Exception as e:
        msg = str(e)
        if "already been registered" in msg or "already exists" in msg or "already registered" in msg:
            print(f"  skip     {email} — already in Supabase Auth")
        else:
            print(f"  ERROR    {email}: {msg}")

print("\nDone!")
print("All users can now log in. Passwords can be changed via Forgot Password.")
