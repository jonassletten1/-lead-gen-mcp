#!/usr/bin/env python3
"""
Seed the database with a default admin user and sample data.
Usage: python seed.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from app import SessionLocal, User, Lead, SearchSession, hash_password

db = SessionLocal()

# ── Admin user ──────────────────────────────────────────────────────────────────
ADMIN_EMAIL = "admin@leadflow.io"
if not db.query(User).filter(User.email == ADMIN_EMAIL).first():
    admin = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password("admin123"),
        name="Admin User",
        role="admin",
    )
    db.add(admin)
    db.commit()
    print(f"✓ Created admin: {ADMIN_EMAIL} / admin123")
else:
    print(f"  Admin {ADMIN_EMAIL} already exists")

# ── Sales reps ──────────────────────────────────────────────────────────────────
REPS_DATA = [
    {"email": "sarah@leadflow.io",  "name": "Sarah Chen",      "password": "rep123"},
    {"email": "marcus@leadflow.io", "name": "Marcus Johnson",  "password": "rep123"},
    {"email": "priya@leadflow.io",  "name": "Priya Patel",     "password": "rep123"},
    {"email": "tom@leadflow.io",    "name": "Tom Williams",    "password": "rep123"},
]

reps = []
for r in REPS_DATA:
    existing = db.query(User).filter(User.email == r["email"]).first()
    if not existing:
        rep = User(
            email=r["email"],
            password_hash=hash_password(r["password"]),
            name=r["name"],
            role="sales_rep",
        )
        db.add(rep)
        db.commit()
        db.refresh(rep)
        reps.append(rep)
        print(f"✓ Created rep: {r['email']} / {r['password']}")
    else:
        reps.append(existing)
        print(f"  Rep {r['email']} already exists")

# ── Sample leads ─────────────────────────────────────────────────────────────────
SAMPLE_LEADS = [
    # Sarah Chen (idx 0) — HVAC + Solar + Plumbing
    {"company_name": "Frost HVAC Services",       "contact_name": "Mike Frost",    "email": "mike@frosthvac.com",           "phone": "713-555-0192", "website": "frosthvac.com",           "city": "Houston, TX",      "industry": "HVAC",      "status": "Contacted",     "weaknesses": ["No online booking", "Slow website", "Missing reviews"], "company_size": "Small",  "source_search": "HVAC companies in Houston, TX",         "rep_idx": 0},
    {"company_name": "AirCool Pro",               "contact_name": "",              "email": "",                             "phone": "832-555-0347", "website": "aircoolpro.com",           "city": "Houston, TX",      "industry": "HVAC",      "status": "Not Contacted", "weaknesses": ["No email found", "Outdated website"],            "company_size": "Small",  "source_search": "HVAC companies in Houston, TX",         "rep_idx": 0},
    {"company_name": "Gulf Coast Heating & Air",  "contact_name": "James Tran",    "email": "james@gcheating.com",          "phone": "713-555-0481", "website": "gcheating.com",           "city": "Houston, TX",      "industry": "HVAC",      "status": "Responded",     "weaknesses": ["No SEO presence", "Poor Google Maps listing"],   "company_size": "Medium", "source_search": "HVAC companies in Houston, TX",         "rep_idx": 0},
    {"company_name": "SunPower Solutions",        "contact_name": "Rachel Kim",    "email": "rkim@sunpowersd.com",          "phone": "",             "website": "sunpowersd.com",          "city": "San Diego, CA",    "industry": "Solar",     "status": "Not Contacted", "weaknesses": ["No phone number listed", "Weak social proof"],   "company_size": "Large",  "source_search": "Solar companies in San Diego, CA",      "rep_idx": 0},
    {"company_name": "QuickFix Plumbing",         "contact_name": "Dave Moreno",   "email": "dave@quickfixplumbing.com",    "phone": "713-555-0039", "website": "quickfixplumbing.com",    "city": "Houston, TX",      "industry": "Plumbing",  "status": "Converted",     "weaknesses": ["No booking page", "No testimonials"],            "company_size": "Small",  "source_search": "Plumbing companies in Houston, TX",     "rep_idx": 0},
    {"company_name": "All Seasons Plumbing",      "contact_name": "",              "email": "",                             "phone": "832-555-0762", "website": "",                        "city": "Houston, TX",      "industry": "Plumbing",  "status": "Not Interested","weaknesses": ["No website", "No email"],                        "company_size": "Small",  "source_search": "Plumbing companies in Houston, TX",     "rep_idx": 0},
    # Marcus Johnson (idx 1)
    {"company_name": "Texas Roofing Co.",         "contact_name": "Bill Sanders",  "email": "bill@texasroofing.com",        "phone": "214-555-0284", "website": "texasroofing.com",        "city": "Dallas, TX",       "industry": "Roofing",   "status": "Contacted",     "weaknesses": ["No reviews", "Old website"],                    "company_size": "Medium", "source_search": "Roofing companies in Dallas, TX",       "rep_idx": 1},
    {"company_name": "Prime Electric",            "contact_name": "Ana Torres",    "email": "ana@primeelectric.com",        "phone": "972-555-0156", "website": "primeelectric.com",       "city": "Dallas, TX",       "industry": "Electrical","status": "Responded",     "weaknesses": ["Missing contact page"],                         "company_size": "Small",  "source_search": "Electrical contractors in Dallas, TX", "rep_idx": 1},
    {"company_name": "Green Lawn Care",           "contact_name": "Kevin Wu",      "email": "kevin@greenlawn.com",          "phone": "469-555-0391", "website": "greenlawn.com",           "city": "Dallas, TX",       "industry": "Landscaping","status": "Not Contacted","weaknesses": ["No online presence"],                            "company_size": "Small",  "source_search": "Lawn care companies in Dallas, TX",    "rep_idx": 1},
    # Priya Patel (idx 2)
    {"company_name": "Pacific Window Cleaning",   "contact_name": "Nadia Costa",   "email": "nadia@pacificwindows.com",     "phone": "310-555-0227", "website": "pacificwindows.com",      "city": "Los Angeles, CA",  "industry": "Cleaning",  "status": "Converted",     "weaknesses": ["Poor mobile site"],                             "company_size": "Small",  "source_search": "Window cleaning in Los Angeles, CA",   "rep_idx": 2},
    {"company_name": "Bay Area Landscaping",      "contact_name": "Raj Singh",     "email": "raj@balscaping.com",           "phone": "415-555-0483", "website": "balscaping.com",          "city": "San Francisco, CA","industry": "Landscaping","status": "Contacted",    "weaknesses": ["No Google Business"],                           "company_size": "Medium", "source_search": "Landscaping in San Francisco, CA",     "rep_idx": 2},
    {"company_name": "Coastal Pest Control",      "contact_name": "",              "email": "info@coastalpest.com",         "phone": "858-555-0149", "website": "coastalpest.com",         "city": "San Diego, CA",    "industry": "Pest Control","status": "Not Contacted","weaknesses": ["Old website", "Low reviews"],                   "company_size": "Small",  "source_search": "Pest control in San Diego, CA",        "rep_idx": 2},
    # Tom Williams (idx 3)
    {"company_name": "Metro Painting Pro",        "contact_name": "John Dawson",   "email": "john@metropainting.com",       "phone": "212-555-0318", "website": "metropainting.com",       "city": "New York, NY",     "industry": "Painting",  "status": "Responded",     "weaknesses": ["No before/after photos"],                       "company_size": "Small",  "source_search": "Painters in New York, NY",             "rep_idx": 3},
    {"company_name": "Brooklyn Flooring",         "contact_name": "Maria Lopez",   "email": "maria@brooklynflooring.com",   "phone": "718-555-0462", "website": "brooklynflooring.com",    "city": "Brooklyn, NY",     "industry": "Flooring",  "status": "Contacted",     "weaknesses": ["Slow website", "No pricing page"],              "company_size": "Medium", "source_search": "Flooring companies in Brooklyn, NY",   "rep_idx": 3},
    {"company_name": "Empire Garage Doors",       "contact_name": "Steve Kim",     "email": "steve@empiregarage.com",       "phone": "917-555-0574", "website": "empiregarage.com",        "city": "New York, NY",     "industry": "Garage Doors","status": "Not Contacted","weaknesses": ["No online booking"],                            "company_size": "Small",  "source_search": "Garage door repair in New York, NY",   "rep_idx": 3},
]

if db.query(Lead).count() == 0:
    for ld in SAMPLE_LEADS:
        rep = reps[ld["rep_idx"]] if ld["rep_idx"] < len(reps) else reps[0]
        lead = Lead(
            company_name=ld["company_name"],
            contact_name=ld.get("contact_name", ""),
            email=ld.get("email", ""),
            phone=ld.get("phone", ""),
            website=ld.get("website", ""),
            city=ld.get("city", ""),
            industry=ld.get("industry", ""),
            status=ld.get("status", "Not Contacted"),
            notes="",
            weaknesses=json.dumps(ld.get("weaknesses", [])),
            company_size=ld.get("company_size", ""),
            source_search=ld.get("source_search", ""),
            assigned_to=rep.id,
        )
        db.add(lead)
    db.commit()
    print(f"✓ Created {len(SAMPLE_LEADS)} sample leads")
else:
    print(f"  Leads already exist ({db.query(Lead).count()} total), skipping")

db.close()

print("\n─────────────────────────────")
print("Seed complete! Login credentials:")
print("  Admin: admin@leadflow.io  / admin123")
print("  Reps:  sarah@leadflow.io  / rep123")
print("         marcus@leadflow.io / rep123")
print("         priya@leadflow.io  / rep123")
print("         tom@leadflow.io    / rep123")
print("─────────────────────────────")
