#!/usr/bin/env python3
"""
LeadFlow Dashboard Backend — FastAPI + Supabase Auth + Organizations
"""

import asyncio
import collections
import json
import re
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, List

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from supabase import create_client, Client

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY         = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
STATUSES = ["Not Contacted", "Contacted", "Responded", "Converted", "Not Interested"]

# Allowed CORS origins — set ALLOWED_ORIGINS in .env as comma-separated list
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000")
ALLOWED_ORIGINS: List[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Rate limiting ─────────────────────────────────────────────────────────────────
# { (endpoint_key, ip): [timestamp, ...] }
_rate_store: dict = collections.defaultdict(list)


def _check_rate(key: str, ip: str, max_calls: int, window_seconds: int) -> None:
    """Raise 429 if ip exceeds max_calls in the last window_seconds for key."""
    bucket = f"{key}:{ip}"
    now = time.time()
    cutoff = now - window_seconds
    _rate_store[bucket] = [t for t in _rate_store[bucket] if t > cutoff]
    if len(_rate_store[bucket]) >= max_calls:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait before trying again.",
        )
    _rate_store[bucket].append(now)

# ── Supabase clients ──────────────────────────────────────────────────────────────
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
sb_admin: Optional[Client] = (
    create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else None
)

# ── Auth ──────────────────────────────────────────────────────────────────────────
bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user_res = sb.auth.get_user(credentials.credentials)
        email = user_res.user.email
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    res = sb.table("users").select("*").eq("email", email).execute()
    if not res.data:
        # Auto-create for Google OAuth sign-ins (no org assigned yet)
        meta = getattr(user_res.user, "user_metadata", {}) or {}
        name = meta.get("full_name") or meta.get("name") or email.split("@")[0]
        new_user = sb.table("users").insert({
            "email":         email,
            "password_hash": "",
            "name":          name,
            "role":          "sales_rep",
            "status":        "active",
        }).execute().data[0]
        return new_user
    user = res.data[0]
    if user.get("status") == "pending":
        raise HTTPException(status_code=403, detail="PENDING_APPROVAL")
    if user.get("status") == "rejected":
        raise HTTPException(status_code=403, detail="REJECTED")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_user_org(user: dict) -> Optional[dict]:
    """Return the organization record for a user, or None."""
    org_id = user.get("organization_id")
    if not org_id:
        return None
    rows = sb.table("organizations").select("*").eq("id", org_id).execute().data
    return rows[0] if rows else None


# ── Pydantic schemas ──────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str


class LeadCreate(BaseModel):
    company_name:  str
    contact_name:  str = ""
    email:         str = ""
    phone:         str = ""
    website:       str = ""
    city:          str = ""
    industry:      str = ""
    status:        str = "Not Contacted"
    notes:         str = ""
    weaknesses:    List[str] = []
    company_size:  str = ""
    source_search: str = ""
    assigned_to:   Optional[str] = None


class LeadUpdate(BaseModel):
    status:       Optional[str] = None
    notes:        Optional[str] = None
    contact_name: Optional[str] = None
    email:        Optional[str] = None
    phone:        Optional[str] = None
    assigned_to:  Optional[str] = None


class ScrapeRequest(BaseModel):
    industry:     str
    location:     str
    radius:       str = "25km"
    criteria:     dict = {}
    phone_req:    str = "preferred"
    email_req:    str = "preferred"
    quantity:     int = 25
    company_size: str = "any"


class RepCreate(BaseModel):
    email:    str
    password: str
    name:     str


# ── Lead helper ───────────────────────────────────────────────────────────────────
LEADS_Q = "*, assignee:assigned_to(name)"


def enrich_lead(raw: dict) -> dict:
    lead = dict(raw)
    assignee = lead.pop("assignee", None)
    lead["assigned_to_name"] = (assignee or {}).get("name") if isinstance(assignee, dict) else None
    if isinstance(lead.get("weaknesses"), str):
        lead["weaknesses"] = json.loads(lead["weaknesses"])
    lead.setdefault("weaknesses", [])
    return lead


# ── FastAPI app ───────────────────────────────────────────────────────────────────
app = FastAPI(title="LeadFlow API", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["Cache-Control"] = "no-store"
    return response


# ── Dashboard ─────────────────────────────────────────────────────────────────────
@app.get("/")
def serve_dashboard():
    return FileResponse(Path(__file__).parent / "dashboard" / "index.html")


# ── Auth routes ───────────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    _check_rate("login", request.client.host, max_calls=10, window_seconds=300)
    if len(req.email) > 254 or len(req.password) > 128:
        raise HTTPException(status_code=400, detail="Invalid input")
    try:
        res = sb.auth.sign_in_with_password({"email": req.email.lower().strip(), "password": req.password})
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user_res = sb.table("users").select("*").eq("email", req.email.lower().strip()).execute()
    if not user_res.data:
        raise HTTPException(status_code=401, detail="User not found")
    u = user_res.data[0]
    if u.get("status") == "pending":
        raise HTTPException(status_code=403, detail="PENDING_APPROVAL")
    if u.get("status") == "rejected":
        raise HTTPException(status_code=403, detail="REJECTED")
    return {
        "token": res.session.access_token,
        "user":  {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]},
    }


@app.post("/api/auth/register", status_code=201)
def register(request: Request, body: dict = Body(...)):
    _check_rate("register", request.client.host, max_calls=5, window_seconds=3600)
    email       = str(body.get("email", "")).lower().strip()[:254]
    password    = str(body.get("password", ""))[:128]
    name        = str(body.get("name", "")).strip()[:100]
    invite_code = str(body.get("invite_code", "")).strip().upper()[:20]
    org_name    = str(body.get("org_name", "")).strip()[:100]

    if not email or not password or not name:
        raise HTTPException(status_code=400, detail="name, email and password are required")
    if sb.table("users").select("id").eq("email", email).execute().data:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Temporarily lock new org creation — invite-only only
    if not invite_code:
        raise HTTPException(status_code=403, detail="New organization registration is currently closed. Use an invite code to join an existing team.")

    # Determine role + org
    if invite_code:
        org_data = sb.table("organizations").select("*").eq("invite_code", invite_code).execute().data
        if not org_data:
            raise HTTPException(status_code=400, detail="Invalid invite code")
        org   = org_data[0]
        role  = "sales_rep"
        org_id = org["id"]
    else:
        if not org_name:
            raise HTTPException(status_code=400, detail="Organization name is required")
        role   = "admin"
        org_id = None  # set after user + org created

    try:
        sb.auth.sign_up({"email": email, "password": password})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Registration failed: {e}")

    # Reps joining via invite code are pending until admin approves
    user_status = "pending" if invite_code else "active"

    user = sb.table("users").insert({
        "email":           email,
        "password_hash":   "",
        "name":            name,
        "role":            role,
        "organization_id": org_id,
        "status":          user_status,
    }).execute().data[0]

    if not invite_code:
        # Create the org and link it back to the user
        code = secrets.token_hex(4).upper()
        org  = sb.table("organizations").insert({
            "name":       org_name,
            "owner_id":   user["id"],
            "invite_code": code,
        }).execute().data[0]
        sb.table("users").update({"organization_id": org["id"]}).eq("id", user["id"]).execute()
        return {"message": "Account created. Check your email to confirm before signing in."}

    return {"message": "Request submitted. You will be able to log in once the admin approves your account."}


@app.post("/api/auth/forgot-password")
def forgot_password(request: Request, body: dict = Body(...)):
    _check_rate("forgot", request.client.host, max_calls=3, window_seconds=3600)
    email = str(body.get("email", "")).lower().strip()[:254]
    if email:
        try:
            sb.auth.reset_password_for_email(email, {"redirect_to": "http://localhost:8000"})
        except Exception:
            pass
    return {"message": "If that email is registered, a reset link has been sent"}


@app.post("/api/auth/reset-password")
def reset_password(body: dict = Body(...)):
    access_token = body.get("access_token", "")
    new_password  = body.get("new_password", "")
    if not access_token or not new_password:
        raise HTTPException(status_code=400, detail="access_token and new_password required")
    if not sb_admin:
        raise HTTPException(status_code=500, detail="SUPABASE_SERVICE_KEY not configured")
    try:
        user_res = sb_admin.auth.get_user(access_token)
        user_id  = user_res.user.id
        sb_admin.auth.admin.update_user_by_id(user_id, {"password": new_password})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    return {"message": "Password updated successfully"}


@app.get("/api/geocode")
async def geocode(q: str, request: Request):
    """Proxy Nominatim geocoding to avoid browser CORS issues."""
    _check_rate("geocode", request.client.host, max_calls=30, window_seconds=60)
    if not q or len(q.strip()) < 2:
        return []
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q.strip(), "format": "json", "limit": 6, "addressdetails": "1"},
                headers={"User-Agent": "LeadFlow/1.0 sales-dashboard"},
            )
            return resp.json()
        except Exception:
            return []


@app.get("/api/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}


@app.get("/api/auth/oauth/google-url")
def google_oauth_url(request: Request):
    origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
    return {"url": f"{SUPABASE_URL}/auth/v1/authorize?provider=google&redirect_to={origin}"}


# ── Organization routes ───────────────────────────────────────────────────────────
@app.get("/api/org")
def get_org(user: dict = Depends(get_current_user)):
    org = get_user_org(user)
    if not org:
        return None
    is_owner = user["role"] == "admin"
    return {
        "id":                     org["id"],
        "name":                   org["name"],
        "invite_code":            org["invite_code"] if is_owner else None,
        "subscription_tier":      org["subscription_tier"],
        "monthly_scrape_limit":   org["monthly_scrape_limit"],
        "scrapes_used_this_month": org["scrapes_used_this_month"],
        "has_google_api":         bool(org.get("google_api_key")),
        "google_api_key":         org.get("google_api_key", "") if is_owner else None,
        "google_search_cx":       org.get("google_search_cx", "") if is_owner else None,
        "owner_id":               org["owner_id"],
        "logo_url":               org.get("logo_url", ""),
        "location":               org.get("location", ""),
        "country":                org.get("country", ""),
        "website":                org.get("website", ""),
        "industry":               org.get("industry", ""),
        "phone":                  org.get("phone", ""),
        "email":                  org.get("email", ""),
        "description":            org.get("description", ""),
        "primary_color":          org.get("primary_color", "#2563eb"),
        "timezone":               org.get("timezone", "UTC"),
    }


@app.put("/api/org")
def update_org(body: dict = Body(...), user: dict = Depends(require_admin)):
    org = get_user_org(user)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the organization owner can update settings")
    patch: dict = {}
    if body.get("name"):               patch["name"]               = body["name"]
    if "google_api_key" in body:       patch["google_api_key"]     = body["google_api_key"]
    if "google_search_cx" in body:     patch["google_search_cx"]   = body["google_search_cx"]
    if "monthly_scrape_limit" in body: patch["monthly_scrape_limit"] = int(body["monthly_scrape_limit"])
    for field in ("logo_url", "location", "country", "website", "industry",
                  "phone", "email", "description", "primary_color", "timezone"):
        if field in body:
            patch[field] = str(body[field])[:500]
    if patch:
        sb.table("organizations").update(patch).eq("id", org["id"]).execute()
    return get_org(user)


@app.post("/api/org/reset-usage")
def reset_usage(user: dict = Depends(require_admin)):
    org = get_user_org(user)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the organization owner can reset usage")
    sb.table("organizations").update({"scrapes_used_this_month": 0}).eq("id", org["id"]).execute()
    return {"ok": True}


@app.post("/api/org/join")
def join_org(request: Request, body: dict = Body(...), user: dict = Depends(get_current_user)):
    """Let an already-logged-in rep join an org via invite code."""
    _check_rate("join_org", request.client.host, max_calls=10, window_seconds=600)
    if user.get("organization_id"):
        raise HTTPException(status_code=400, detail="You are already in an organization")
    invite_code = str(body.get("invite_code", "")).strip().upper()[:20]
    if not invite_code:
        raise HTTPException(status_code=400, detail="Invite code is required")
    org_data = sb.table("organizations").select("id").eq("invite_code", invite_code).execute().data
    if not org_data:
        raise HTTPException(status_code=400, detail="Invalid invite code")
    org_id = org_data[0]["id"]
    sb.table("users").update({"organization_id": org_id, "status": "pending"}).eq("id", user["id"]).execute()
    return {"message": "Request submitted. You will be able to use the platform once the admin approves your account."}


@app.get("/api/org/members")
def org_members(user: dict = Depends(require_admin)):
    org = get_user_org(user)
    if not org:
        return []
    members = sb.table("users").select("id,name,email,role,created_at,status").eq("organization_id", org["id"]).execute().data
    return members


@app.get("/api/org/pending")
def pending_members(user: dict = Depends(require_admin)):
    org = get_user_org(user)
    if not org:
        return []
    return sb.table("users").select("id,name,email,created_at").eq("organization_id", org["id"]).eq("status", "pending").execute().data


@app.post("/api/org/members/{user_id}/approve")
def approve_member(user_id: str, admin: dict = Depends(require_admin)):
    org = get_user_org(admin)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    target = sb.table("users").select("id,organization_id").eq("id", user_id).execute().data
    if not target or target[0].get("organization_id") != org["id"]:
        raise HTTPException(status_code=404, detail="User not found in your organization")
    sb.table("users").update({"status": "active"}).eq("id", user_id).execute()
    return {"ok": True}


@app.post("/api/org/members/{user_id}/reject")
def reject_member(user_id: str, admin: dict = Depends(require_admin)):
    org = get_user_org(admin)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    target = sb.table("users").select("id,organization_id").eq("id", user_id).execute().data
    if not target or target[0].get("organization_id") != org["id"]:
        raise HTTPException(status_code=404, detail="User not found in your organization")
    # Delete from users table and Supabase Auth
    email = sb.table("users").select("email").eq("id", user_id).execute().data
    sb.table("users").delete().eq("id", user_id).execute()
    if email and sb_admin:
        try:
            auth_users = sb_admin.auth.admin.list_users()
            for u in auth_users:
                if u.email == email[0]["email"]:
                    sb_admin.auth.admin.delete_user(u.id)
        except Exception:
            pass
    return {"ok": True}


@app.delete("/api/org/members/{user_id}")
def remove_member(user_id: str, admin: dict = Depends(require_admin)):
    """Remove a rep from the organization (does not delete the user account)."""
    org = get_user_org(admin)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    target = sb.table("users").select("id,organization_id,role").eq("id", user_id).execute().data
    if not target or target[0].get("organization_id") != org["id"]:
        raise HTTPException(status_code=404, detail="User not found in your organization")
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    sb.table("users").update({"organization_id": None}).eq("id", user_id).execute()
    return {"ok": True}


@app.post("/api/org/new-invite")
def regenerate_invite(user: dict = Depends(require_admin)):
    """Generate a new invite code for the org (invalidates the old one)."""
    org = get_user_org(user)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can regenerate the invite code")
    new_code = secrets.token_hex(4).upper()
    sb.table("organizations").update({"invite_code": new_code}).eq("id", org["id"]).execute()
    return {"invite_code": new_code}


# ── Leads ─────────────────────────────────────────────────────────────────────────
@app.get("/api/leads")
def get_leads(user: dict = Depends(get_current_user)):
    q = sb.table("leads").select(LEADS_Q).order("created_at", desc=True)
    if user["role"] != "admin":
        q = q.eq("assigned_to", user["id"])
    return [enrich_lead(l) for l in q.execute().data]


@app.post("/api/leads", status_code=201)
def create_lead(req: LeadCreate, user: dict = Depends(get_current_user)):
    org = get_user_org(user)
    inserted = sb.table("leads").insert({
        "company_name":   req.company_name,
        "contact_name":   req.contact_name,
        "email":          req.email,
        "phone":          req.phone,
        "website":        req.website,
        "city":           req.city,
        "industry":       req.industry,
        "status":         req.status,
        "notes":          req.notes,
        "weaknesses":     req.weaknesses,
        "company_size":   req.company_size,
        "source_search":  req.source_search,
        "assigned_to":    req.assigned_to or user["id"],
        "organization_id": org["id"] if org else None,
    }).execute().data[0]
    full = sb.table("leads").select(LEADS_Q).eq("id", inserted["id"]).execute()
    return enrich_lead(full.data[0])


@app.put("/api/leads/{lead_id}")
def update_lead(lead_id: str, req: LeadUpdate, user: dict = Depends(get_current_user)):
    row = sb.table("leads").select("id,assigned_to").eq("id", lead_id).execute().data
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    if user["role"] != "admin" and row[0]["assigned_to"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your lead")
    patch: dict = {k: v for k, v in {
        "status":       req.status,
        "notes":        req.notes,
        "contact_name": req.contact_name,
        "email":        req.email,
        "phone":        req.phone,
        "assigned_to":  req.assigned_to if user["role"] == "admin" else None,
        "updated_at":   datetime.utcnow().isoformat(),
    }.items() if v is not None}
    sb.table("leads").update(patch).eq("id", lead_id).execute()
    full = sb.table("leads").select(LEADS_Q).eq("id", lead_id).execute()
    return enrich_lead(full.data[0])


@app.delete("/api/leads/{lead_id}", status_code=204)
def delete_lead(lead_id: str, user: dict = Depends(get_current_user)):
    row = sb.table("leads").select("id,assigned_to").eq("id", lead_id).execute().data
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    if user["role"] != "admin" and row[0]["assigned_to"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your lead")
    sb.table("leads").delete().eq("id", lead_id).execute()


@app.get("/api/leads/stats")
def lead_stats(user: dict = Depends(get_current_user)):
    q = sb.table("leads").select("status")
    if user["role"] != "admin":
        q = q.eq("assigned_to", user["id"])
    leads = q.execute().data
    counts: dict = {s: 0 for s in STATUSES}
    for l in leads:
        if l["status"] in counts:
            counts[l["status"]] += 1
    return {"total": len(leads), **counts}


@app.get("/api/leads/by-rep")
def leads_by_rep(user: dict = Depends(require_admin)):
    reps  = sb.table("users").select("*").eq("role", "sales_rep").execute().data
    leads = sb.table("leads").select(LEADS_Q).execute().data
    result = []
    for rep in reps:
        rl = [enrich_lead(l) for l in leads if l.get("assigned_to") == rep["id"]]
        counts: dict = {s: 0 for s in STATUSES}
        for l in rl:
            if l["status"] in counts:
                counts[l["status"]] += 1
        result.append({
            "id": rep["id"], "name": rep["name"], "email": rep["email"],
            "total": len(rl), **counts, "leads": rl,
        })
    return result


# ── Scraping ──────────────────────────────────────────────────────────────────────
async def _search_leads(query: str, api_key: str, search_cx: str, max_results: int = 10) -> List[Any]:
    if not api_key or not search_cx:
        return [{"error": "Google API key not configured. Ask your admin to add it in Settings."}]
    results: List[Any] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            start: int = 1
            while len(results) < max_results:
                num = min(10, max_results - len(results))
                resp = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"key": api_key, "cx": search_cx,
                            "q": query, "num": num, "start": start},
                )
                if resp.status_code == 429 or (resp.status_code == 403 and "rateLimitExceeded" in resp.text):
                    results.append({"error": "Google Search API daily quota reached (100 searches/day). Try again tomorrow."})
                    break
                if resp.status_code == 403 and "accessNotConfigured" in resp.text:
                    results.append({"error": "Custom Search API is not enabled. Enable it at: https://console.developers.google.com/apis/api/customsearch.googleapis.com/overview"})
                    break
                if resp.status_code == 403:
                    print(f"[SCRAPE] 403 error body: {resp.text[:500]}")
                    results.append({"error": f"Google API error 403: {resp.text[:300]}"})
                    break
                resp.raise_for_status()
                items: List[Any] = resp.json().get("items", [])
                if not items:
                    break
                for item in items:
                    results.append({
                        "title":   item.get("title", ""),
                        "url":     item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                    })
                start = start + len(items)
                if len(items) < num:
                    break
    except Exception as e:
        results.append({"error": str(e)})
    return results


async def _scrape_contact_info(url: str) -> dict:
    info: dict = {"url": url, "page_title": "", "emails": [], "phones": []}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(" ", strip=True)
            title_tag = soup.find("title")
            info["page_title"] = title_tag.get_text(strip=True) if title_tag else ""
            raw_emails: List[str] = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
            clean_emails: List[str] = [e for e in raw_emails
                                       if not re.search(r'\.(png|jpg|jpeg|gif|svg|webp|ico)$', e, re.I)]
            info["emails"] = list(dict.fromkeys(clean_emails))[:5]
            raw_phones: List[str] = re.findall(r'(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})', text)
            info["phones"] = list(dict.fromkeys(raw_phones))[:5]
    except Exception as e:
        info["error"] = str(e)
    return info


@app.post("/api/scrape")
async def scrape(req: ScrapeRequest, user: dict = Depends(get_current_user)):
    # Must belong to an org
    org = get_user_org(user)
    if not org:
        raise HTTPException(
            status_code=403,
            detail="You need to join an organization to search for leads. Ask your admin for an invite code."
        )

    # Check monthly limit
    if org["scrapes_used_this_month"] >= org["monthly_scrape_limit"]:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly scrape limit reached ({org['monthly_scrape_limit']} searches). Reset the counter in Admin Settings or upgrade your plan."
        )

    api_key   = org.get("google_api_key", "")
    search_cx = org.get("google_search_cx", "")
    # Debug: print key info so you can verify it's correct in server logs
    print(f"[SCRAPE] org={org.get('name')} key_len={len(api_key)} key_start={api_key[:8]!r} key_end={api_key[-4:]!r} cx={search_cx!r}")
    query     = f"{req.industry} companies in {req.location}"
    search_results = await _search_leads(query, api_key, search_cx, max_results=min(req.quantity, 30))

    errors = [r for r in search_results if r.get("error")]
    if errors and len(errors) == len(search_results):
        return {"session_id": None, "query": query, "results": errors[:1], "total": 0}

    async def scrape_one(r: dict):
        if not r.get("url") or r.get("error"):
            return r if r.get("error") else None
        contact = await _scrape_contact_info(r["url"])
        company = r.get("title", "").split(" - ")[0].split(" | ")[0].split(" – ")[0][:80].strip()
        return {
            "company_name":  company or r["url"],
            "website":       r["url"],
            "city":          req.location,
            "industry":      req.industry,
            "email":         contact["emails"][0] if contact["emails"] else "",
            "phone":         contact["phones"][0] if contact["phones"] else "",
            "has_email":     bool(contact["emails"]),
            "has_phone":     bool(contact["phones"]),
            "has_website":   True,
            "source_search": query,
            "snippet":       r.get("snippet", ""),
        }

    raw = await asyncio.gather(*[scrape_one(r) for r in search_results[:req.quantity]],
                               return_exceptions=True)
    results: List[Any] = [r for r in raw if r and not isinstance(r, Exception)]

    if req.phone_req == "required":
        results = [r for r in results if r.get("has_phone") and not r.get("error")]
    if req.email_req == "required":
        results = [r for r in results if r.get("has_email") and not r.get("error")]

    # Increment org scrape usage
    sb.table("organizations").update({
        "scrapes_used_this_month": org["scrapes_used_this_month"] + 1
    }).eq("id", org["id"]).execute()

    session = sb.table("search_sessions").insert({
        "query":           query,
        "location":        req.location,
        "industry":        req.industry,
        "criteria":        req.criteria,
        "results_count":   len(results),
        "created_by":      user["id"],
        "organization_id": org["id"],
    }).execute().data[0]

    return {"session_id": session["id"], "query": query, "results": results, "total": len(results)}


@app.get("/api/scrape/sessions")
def scrape_sessions(user: dict = Depends(get_current_user)):
    q = sb.table("search_sessions").select("*").order("created_at", desc=True).limit(50)
    if user["role"] != "admin":
        q = q.eq("created_by", user["id"])
    return [
        {"id": s["id"], "query": s["query"], "location": s.get("location", ""),
         "industry": s.get("industry", ""), "results_count": s.get("results_count", 0),
         "created_at": s.get("created_at")}
        for s in q.execute().data
    ]


# ── Reps ──────────────────────────────────────────────────────────────────────────
@app.get("/api/reps")
def get_reps(user: dict = Depends(require_admin)):
    reps  = sb.table("users").select("*").eq("role", "sales_rep").execute().data
    leads = sb.table("leads").select("id,status,assigned_to").execute().data
    result = []
    for rep in reps:
        rl = [l for l in leads if l.get("assigned_to") == rep["id"]]
        counts: dict = {s: 0 for s in STATUSES}
        for l in rl:
            if l["status"] in counts:
                counts[l["status"]] += 1
        result.append({
            "id": rep["id"], "name": rep["name"], "email": rep["email"],
            "role": rep["role"], "created_at": rep.get("created_at"),
            "total_leads": len(rl), **counts,
        })
    return result


@app.post("/api/reps", status_code=201)
def create_rep(req: RepCreate, admin: dict = Depends(require_admin)):
    email = req.email.lower().strip()
    if sb.table("users").select("id").eq("email", email).execute().data:
        raise HTTPException(status_code=400, detail="Email already registered")
    if not sb_admin:
        raise HTTPException(status_code=500, detail="SUPABASE_SERVICE_KEY not configured")
    try:
        sb_admin.auth.admin.create_user({"email": email, "password": req.password, "email_confirm": True})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not create auth user: {e}")
    org = get_user_org(admin)
    u = sb.table("users").insert({
        "email":           email,
        "password_hash":   "",
        "name":            req.name,
        "role":            "sales_rep",
        "organization_id": org["id"] if org else None,
    }).execute().data[0]
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}


@app.put("/api/reps/{rep_id}/assign")
def assign_lead(rep_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    lead_id = body.get("lead_id")
    if not sb.table("leads").select("id").eq("id", lead_id).execute().data:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not sb.table("users").select("id").eq("id", rep_id).eq("role", "sales_rep").execute().data:
        raise HTTPException(status_code=404, detail="Rep not found")
    sb.table("leads").update({
        "assigned_to": rep_id,
        "updated_at":  datetime.utcnow().isoformat(),
    }).eq("id", lead_id).execute()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
