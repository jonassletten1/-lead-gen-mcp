#!/usr/bin/env python3
"""
LeadFlow Dashboard Backend — FastAPI + Supabase
"""

import asyncio
import json
import re
import os
from datetime import datetime, timedelta
from typing import Any, Optional, List

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from supabase import create_client, Client

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────────
SUPABASE_URL      = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY", "")
SECRET_KEY        = os.getenv("JWT_SECRET", "leadflow-dev-secret-change-in-production")
GOOGLE_API_KEY    = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_SEARCH_CX  = os.getenv("GOOGLE_SEARCH_CX", "")
ALGORITHM         = "HS256"
TOKEN_EXPIRE_DAYS = 7
STATUSES = ["Not Contacted", "Contacted", "Responded", "Converted", "Not Interested"]

# ── Supabase client ──────────────────────────────────────────────────────────────
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Auth helpers ─────────────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer      = HTTPBearer(auto_error=False)


def hash_password(p: str) -> str:
    return pwd_context.hash(p)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: str, role: str) -> str:
    return jwt.encode(
        {"sub": str(user_id), "role": role,
         "exp": datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)},
        SECRET_KEY, algorithm=ALGORITHM,
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    res = sb.table("users").select("*").eq("id", payload["sub"]).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="User not found")
    return res.data[0]


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Pydantic schemas ──────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    role: str = "sales_rep"


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
    assigned_to:   Optional[str] = None   # UUID string


class LeadUpdate(BaseModel):
    status:       Optional[str] = None
    notes:        Optional[str] = None
    contact_name: Optional[str] = None
    email:        Optional[str] = None
    phone:        Optional[str] = None
    assigned_to:  Optional[str] = None   # UUID string


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
    """Return an enriched copy with assignee name flattened and weaknesses as list."""
    lead = dict(raw)
    assignee = lead.pop("assignee", None)
    lead["assigned_to_name"] = (assignee or {}).get("name") if isinstance(assignee, dict) else None
    if isinstance(lead.get("weaknesses"), str):
        lead["weaknesses"] = json.loads(lead["weaknesses"])
    lead.setdefault("weaknesses", [])
    return lead


# ── FastAPI app ───────────────────────────────────────────────────────────────────
app = FastAPI(title="LeadFlow API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth ──────────────────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
def login(req: LoginRequest):
    res = sb.table("users").select("*").eq("email", req.email.lower().strip()).execute()
    if not res.data or not verify_password(req.password, res.data[0]["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    u = res.data[0]
    return {
        "token": create_token(u["id"], u["role"]),
        "user":  {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]},
    }


@app.post("/api/auth/register", status_code=201)
def register(req: RegisterRequest):
    if sb.table("users").select("id").eq("email", req.email.lower().strip()).execute().data:
        raise HTTPException(status_code=400, detail="Email already registered")
    u = sb.table("users").insert({
        "email":         req.email.lower().strip(),
        "password_hash": hash_password(req.password),
        "name":          req.name,
        "role":          req.role,
    }).execute().data[0]
    return {
        "token": create_token(u["id"], u["role"]),
        "user":  {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]},
    }


@app.get("/api/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}


# ── Leads ─────────────────────────────────────────────────────────────────────────
@app.get("/api/leads")
def get_leads(user: dict = Depends(get_current_user)):
    q = sb.table("leads").select(LEADS_Q).order("created_at", desc=True)
    if user["role"] != "admin":
        q = q.eq("assigned_to", user["id"])
    return [enrich_lead(l) for l in q.execute().data]


@app.post("/api/leads", status_code=201)
def create_lead(req: LeadCreate, user: dict = Depends(get_current_user)):
    inserted = sb.table("leads").insert({
        "company_name":  req.company_name,
        "contact_name":  req.contact_name,
        "email":         req.email,
        "phone":         req.phone,
        "website":       req.website,
        "city":          req.city,
        "industry":      req.industry,
        "status":        req.status,
        "notes":         req.notes,
        "weaknesses":    req.weaknesses,
        "company_size":  req.company_size,
        "source_search": req.source_search,
        "assigned_to":   req.assigned_to or user["id"],
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
async def _search_leads(query: str, max_results: int = 10) -> List[Any]:
    if not GOOGLE_API_KEY or not GOOGLE_SEARCH_CX:
        return [{"error": "Google Search API credentials not configured"}]
    results: List[Any] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            start: int = 1
            while len(results) < max_results:
                num = min(10, max_results - len(results))
                resp = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"key": GOOGLE_API_KEY, "cx": GOOGLE_SEARCH_CX,
                            "q": query, "num": num, "start": start},
                )
                if resp.status_code == 429 or (resp.status_code == 403 and "rateLimitExceeded" in resp.text):
                    results.append({"error": "Google Search API daily quota reached (100 searches/day). Try again tomorrow."})
                    break
                if resp.status_code == 403 and "accessNotConfigured" in resp.text:
                    results.append({"error": "Custom Search API is not enabled. Enable it at: https://console.developers.google.com/apis/api/customsearch.googleapis.com/overview"})
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
    query = f"{req.industry} companies in {req.location}"
    search_results = await _search_leads(query, max_results=min(req.quantity, 30))

    # Bubble up API-level errors immediately
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

    session = sb.table("search_sessions").insert({
        "query":         query,
        "location":      req.location,
        "industry":      req.industry,
        "criteria":      req.criteria,
        "results_count": len(results),
        "created_by":    user["id"],
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
    if sb.table("users").select("id").eq("email", req.email.lower().strip()).execute().data:
        raise HTTPException(status_code=400, detail="Email already registered")
    u = sb.table("users").insert({
        "email":         req.email.lower().strip(),
        "password_hash": hash_password(req.password),
        "name":          req.name,
        "role":          "sales_rep",
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
