#!/usr/bin/env python3
"""
Seed script for LeadFlow — Supabase backend.
Run AFTER creating the tables in Supabase SQL Editor.
Safe to re-run: skips records that already exist.
"""

import os
from dotenv import load_dotenv
from passlib.context import CryptContext
from supabase import create_client

load_dotenv()

sb  = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def upsert_user(email, password, name, role):
    existing = sb.table("users").select("id").eq("email", email).execute().data
    if existing:
        print(f"  skip  {email} (already exists)")
        return existing[0]["id"]
    row = sb.table("users").insert({
        "email":         email,
        "password_hash": pwd.hash(password),
        "name":          name,
        "role":          role,
    }).execute().data[0]
    print(f"  created {role}: {email}")
    return row["id"]


def upsert_lead(data):
    existing = sb.table("leads").select("id") \
        .eq("company_name", data["company_name"]) \
        .eq("assigned_to", data["assigned_to"]).execute().data
    if existing:
        print(f"  skip  lead: {data['company_name']}")
        return
    sb.table("leads").insert(data).execute()
    print(f"  created lead: {data['company_name']}")


print("\n── Users ───────────────────────────────────")
admin_id  = upsert_user("admin@leadflow.io",  "admin123", "Admin",       "admin")
sarah_id  = upsert_user("sarah@leadflow.io",  "rep123",   "Sarah Chen",  "sales_rep")
marcus_id = upsert_user("marcus@leadflow.io", "rep123",   "Marcus Webb", "sales_rep")
priya_id  = upsert_user("priya@leadflow.io",  "rep123",   "Priya Nair",  "sales_rep")
tom_id    = upsert_user("tom@leadflow.io",    "rep123",   "Tom Reilly",  "sales_rep")

print("\n── Leads ───────────────────────────────────")
leads = [
    # Sarah Chen (6 leads)
    dict(company_name="Frost HVAC Services",      contact_name="Mike Frost",   email="mike@frosthvac.com",        phone="713-555-0192", website="frosthvac.com",        city="Houston, TX",   industry="HVAC",         status="Contacted",      notes="Owner answered. Call back Thursday.", weaknesses=["No online booking","Slow website","Missing reviews"], company_size="Small",  source_search="HVAC companies in Houston TX",        assigned_to=sarah_id),
    dict(company_name="AirCool Pro",              contact_name="",             email="",                          phone="832-555-0347", website="aircoolpro.com",        city="Houston, TX",   industry="HVAC",         status="Not Contacted",  notes="",                                    weaknesses=["No email found","Outdated website"],                  company_size="Small",  source_search="HVAC companies in Houston TX",        assigned_to=sarah_id),
    dict(company_name="Gulf Coast Heating & Air", contact_name="James Tran",   email="james@gcheating.com",       phone="713-555-0481", website="gcheating.com",        city="Houston, TX",   industry="HVAC",         status="Responded",      notes="Interested in social media package.", weaknesses=["No social media","Weak SEO"],                         company_size="Medium", source_search="HVAC companies in Houston TX",        assigned_to=sarah_id),
    dict(company_name="Bright Future Solar",      contact_name="Leo Kim",      email="leo@bfsolar.io",            phone="619-555-1108", website="bfsolar.io",           city="San Diego, CA", industry="Solar",        status="Responded",      notes="",                                    weaknesses=["No reviews","No booking"],                            company_size="Small",  source_search="Solar companies in San Diego CA",     assigned_to=sarah_id),
    dict(company_name="BlueSky Plumbing Co",      contact_name="Dana Cruz",    email="dana@blueskyplumb.com",     phone="713-555-0234", website="blueskyplumb.com",     city="Houston, TX",   industry="Plumbing",     status="Converted",      notes="Closed deal — monthly SEO retainer.", weaknesses=["Poor SEO","No Google Ads"],                           company_size="Small",  source_search="Plumbing companies in Houston TX",    assigned_to=sarah_id),
    dict(company_name="Apex HVAC Solutions",      contact_name="Robert Mills", email="r.mills@apexhvac.com",      phone="713-555-0182", website="apexhvac.com",         city="Houston, TX",   industry="HVAC",         status="Not Interested", notes="Not interested at this time.",         weaknesses=[],                                                     company_size="Large",  source_search="HVAC companies in Houston TX",        assigned_to=sarah_id),
    # Marcus Webb (3 leads)
    dict(company_name="Premier Roofing Group",    contact_name="Kevin Hart",   email="k.hart@premieroof.com",     phone="214-555-0311", website="premieroof.com",       city="Dallas, TX",    industry="Roofing",      status="Contacted",      notes="",                                    weaknesses=["No online booking","Low reviews"],                    company_size="Medium", source_search="Roofing companies in Dallas TX",      assigned_to=marcus_id),
    dict(company_name="Summit Garage Doors",      contact_name="Carol White",  email="carol@summitgd.com",        phone="720-555-1221", website="summitgd.com",         city="Denver, CO",    industry="Construction", status="Not Contacted",  notes="",                                    weaknesses=["Bad website","No social media"],                      company_size="Small",  source_search="Garage door companies in Denver CO",  assigned_to=marcus_id),
    dict(company_name="TechBuild Contractors",    contact_name="Alan Patel",   email="alan@techbuild.co",         phone="312-555-0712", website="techbuild.co",         city="Chicago, IL",   industry="Construction", status="Not Interested", notes="",                                    weaknesses=[],                                                     company_size="Large",  source_search="Construction companies in Chicago IL", assigned_to=marcus_id),
    # Priya Nair (3 leads)
    dict(company_name="GreenLeaf Landscaping",    contact_name="Susan Park",   email="susan@greenleaf.io",        phone="512-555-0447", website="greenleaf.io",         city="Austin, TX",    industry="Landscaping",  status="Converted",      notes="Signed up for website redesign.",      weaknesses=["No SEO","No booking"],                                company_size="Small",  source_search="Landscaping companies in Austin TX",  assigned_to=priya_id),
    dict(company_name="Lone Star Electric",       contact_name="Bill Torres",  email="bill@lonestarelectric.com", phone="832-555-0523", website="lonestarelectric.com", city="Houston, TX",   industry="Electrical",   status="Not Contacted",  notes="",                                    weaknesses=["No Google Ads","Weak website"],                       company_size="Medium", source_search="Electrical companies in Houston TX",  assigned_to=priya_id),
    dict(company_name="Pacific Northwest HVAC",   contact_name="Trevor Nash",  email="tnash@pacnwhvac.com",       phone="206-555-1319", website="pacnwhvac.com",        city="Seattle, WA",   industry="HVAC",         status="Converted",      notes="",                                    weaknesses=["No reviews","No social media"],                       company_size="Medium", source_search="HVAC companies in Seattle WA",        assigned_to=priya_id),
    # Tom Reilly (3 leads)
    dict(company_name="Harvest Pest Control",     contact_name="Nina Briggs",  email="nina@harvestpc.com",        phone="602-555-1012", website="harvestpc.com",        city="Phoenix, AZ",   industry="Pest Control", status="Contacted",      notes="",                                    weaknesses=["No booking system","Low reviews"],                    company_size="Small",  source_search="Pest control companies in Phoenix AZ", assigned_to=tom_id),
    dict(company_name="Desert Air Conditioning",  contact_name="Rosa Jimenez", email="rosa@desertac.com",         phone="520-555-1425", website="desertac.com",         city="Tucson, AZ",    industry="HVAC",         status="Contacted",      notes="",                                    weaknesses=["Bad website","No online booking"],                    company_size="Small",  source_search="HVAC companies in Tucson AZ",         assigned_to=tom_id),
    dict(company_name="Allied Security Systems",  contact_name="Frank Owens",  email="fowens@alliedsec.io",       phone="404-555-1511", website="alliedsec.io",         city="Atlanta, GA",   industry="IT Services",  status="Responded",      notes="",                                    weaknesses=["No social media","Poor SEO"],                         company_size="Medium", source_search="Security companies in Atlanta GA",     assigned_to=tom_id),
]

for lead in leads:
    upsert_lead(lead)

print("\nDone!")
print("\nLogin credentials:")
print("  Admin:     admin@leadflow.io / admin123")
print("  Sales rep: sarah@leadflow.io / rep123  (also marcus, priya, tom)")
