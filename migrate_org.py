#!/usr/bin/env python3
"""
migrate_org.py — Create Jonas's org and assign all existing users to it.
Run ONCE after the SQL schema migration in Supabase.
"""
import os, secrets
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GOOGLE_API_KEY       = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_SEARCH_CX     = os.getenv("GOOGLE_SEARCH_CX", "")
ADMIN_EMAIL          = "admin@leadflow.io"

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# 1. Find admin user
users = sb.table("users").select("*").execute().data
admin = next((u for u in users if u["email"] == ADMIN_EMAIL), None)
if not admin:
    print(f"ERROR: {ADMIN_EMAIL} not found in users table")
    raise SystemExit(1)

print(f"Found admin: {admin['name']} ({admin['id']})")

# 2. Check if org already exists for this admin
existing = sb.table("organizations").select("*").eq("owner_id", admin["id"]).execute().data
if existing:
    org = existing[0]
    print(f"Org already exists: {org['name']} (id={org['id']})")
else:
    # Create the org
    invite_code = secrets.token_hex(4).upper()
    org = sb.table("organizations").insert({
        "name":             "LeadFlow HQ",
        "owner_id":         admin["id"],
        "google_api_key":   GOOGLE_API_KEY,
        "google_search_cx": GOOGLE_SEARCH_CX,
        "invite_code":      invite_code,
        "subscription_tier": "pro",
        "monthly_scrape_limit": 500,
    }).execute().data[0]
    print(f"Created org: {org['name']} (id={org['id']}, invite_code={org['invite_code']})")

# 3. Assign ALL existing users to this org
org_id = org["id"]
updated = 0
for u in users:
    if not u.get("organization_id"):
        sb.table("users").update({"organization_id": org_id}).eq("id", u["id"]).execute()
        print(f"  Assigned {u['email']} → org")
        updated += 1
    else:
        print(f"  Skipped {u['email']} (already in org {u['organization_id']})")

print(f"\nDone. {updated} users assigned to org '{org['name']}'.")
print(f"Invite code: {org['invite_code']}")
