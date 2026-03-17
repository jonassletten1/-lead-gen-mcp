#!/usr/bin/env python3
"""
LeadFlow Dashboard Backend — FastAPI
Web dashboard API for the lead generation platform.
"""

import asyncio
import json
import re
import os
from datetime import datetime, timedelta
from typing import Any, Optional, List

import httpx
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./leadgen.db")
SECRET_KEY   = os.getenv("JWT_SECRET", "leadflow-dev-secret-change-in-production")
ALGORITHM    = "HS256"
TOKEN_EXPIRE_DAYS = 7
STATUSES = ["Not Contacted", "Contacted", "Responded", "Converted", "Not Interested"]

# ── Database ────────────────────────────────────────────────────────────────────
engine       = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base         = declarative_base()


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    email         = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name          = Column(String, nullable=False)
    role          = Column(String, default="sales_rep")   # "admin" | "sales_rep"
    created_at    = Column(DateTime, default=datetime.utcnow)
    leads         = relationship("Lead", back_populates="assignee", foreign_keys="Lead.assigned_to")


class Lead(Base):
    __tablename__  = "leads"
    id             = Column(Integer, primary_key=True, index=True)
    company_name   = Column(String, nullable=False)
    contact_name   = Column(String, default="")
    email          = Column(String, default="")
    phone          = Column(String, default="")
    website        = Column(String, default="")
    city           = Column(String, default="")
    industry       = Column(String, default="")
    status         = Column(String, default="Not Contacted")
    notes          = Column(Text, default="")
    weaknesses     = Column(Text, default="[]")   # JSON array
    company_size   = Column(String, default="")
    source_search  = Column(String, default="")
    assigned_to    = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    assignee       = relationship("User", back_populates="leads", foreign_keys=[assigned_to])


class SearchSession(Base):
    __tablename__  = "search_sessions"
    id             = Column(Integer, primary_key=True, index=True)
    query          = Column(String, nullable=False)
    location       = Column(String, default="")
    industry       = Column(String, default="")
    criteria       = Column(Text, default="{}")   # JSON
    results_count  = Column(Integer, default=0)
    created_by     = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    creator        = relationship("User", foreign_keys=[created_by])


Base.metadata.create_all(bind=engine)

# ── Auth helpers ────────────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer      = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: int, role: str) -> str:
    payload = {
        "sub":  str(user_id),
        "role": role,
        "exp":  datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Pydantic schemas ─────────────────────────────────────────────────────────────
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
    assigned_to:   Optional[int] = None


class LeadUpdate(BaseModel):
    status:       Optional[str] = None
    notes:        Optional[str] = None
    contact_name: Optional[str] = None
    email:        Optional[str] = None
    phone:        Optional[str] = None
    assigned_to:  Optional[int] = None


class ScrapeRequest(BaseModel):
    industry:    str
    location:    str
    radius:      str = "25km"
    criteria:    dict = {}
    phone_req:   str = "preferred"
    email_req:   str = "preferred"
    quantity:    int = 25
    company_size: str = "any"


class RepCreate(BaseModel):
    email:    str
    password: str
    name:     str


# ── FastAPI app ─────────────────────────────────────────────────────────────────
app = FastAPI(title="LeadFlow API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth routes ─────────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email.lower().strip()).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token(user.id, user.role)
    return {
        "token": token,
        "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role},
    }


@app.post("/api/auth/register", status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email.lower().strip()).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=req.email.lower().strip(),
        password_hash=hash_password(req.password),
        name=req.name,
        role=req.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_token(user.id, user.role)
    return {
        "token": token,
        "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role},
    }


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


# ── Lead helpers ─────────────────────────────────────────────────────────────────
def lead_to_dict(lead: Lead) -> dict:
    return {
        "id":               lead.id,
        "company_name":     lead.company_name,
        "contact_name":     lead.contact_name,
        "email":            lead.email,
        "phone":            lead.phone,
        "website":          lead.website,
        "city":             lead.city,
        "industry":         lead.industry,
        "status":           lead.status,
        "notes":            lead.notes,
        "weaknesses":       json.loads(lead.weaknesses or "[]"),
        "company_size":     lead.company_size,
        "source_search":    lead.source_search,
        "assigned_to":      lead.assigned_to,
        "assigned_to_name": lead.assignee.name if lead.assignee else None,
        "created_at":       lead.created_at.isoformat() if lead.created_at else None,
        "updated_at":       lead.updated_at.isoformat() if lead.updated_at else None,
    }


# ── Lead routes ─────────────────────────────────────────────────────────────────
@app.get("/api/leads")
def get_leads(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Lead)
    if user.role != "admin":
        q = q.filter(Lead.assigned_to == user.id)
    leads = q.order_by(Lead.created_at.desc()).all()
    return [lead_to_dict(l) for l in leads]


@app.post("/api/leads", status_code=201)
def create_lead(req: LeadCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead = Lead(
        company_name=req.company_name,
        contact_name=req.contact_name,
        email=req.email,
        phone=req.phone,
        website=req.website,
        city=req.city,
        industry=req.industry,
        status=req.status,
        notes=req.notes,
        weaknesses=json.dumps(req.weaknesses),
        company_size=req.company_size,
        source_search=req.source_search,
        assigned_to=req.assigned_to if req.assigned_to else user.id,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead_to_dict(lead)


@app.put("/api/leads/{lead_id}")
def update_lead(
    lead_id: int, req: LeadUpdate,
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if user.role != "admin" and lead.assigned_to != user.id:
        raise HTTPException(status_code=403, detail="Not your lead")
    if req.status       is not None: lead.status       = req.status
    if req.notes        is not None: lead.notes        = req.notes
    if req.contact_name is not None: lead.contact_name = req.contact_name
    if req.email        is not None: lead.email        = req.email
    if req.phone        is not None: lead.phone        = req.phone
    if req.assigned_to  is not None and user.role == "admin":
        lead.assigned_to = req.assigned_to
    lead.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    return lead_to_dict(lead)


@app.delete("/api/leads/{lead_id}", status_code=204)
def delete_lead(
    lead_id: int,
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if user.role != "admin" and lead.assigned_to != user.id:
        raise HTTPException(status_code=403, detail="Not your lead")
    db.delete(lead)
    db.commit()


@app.get("/api/leads/stats")
def lead_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Lead)
    if user.role != "admin":
        q = q.filter(Lead.assigned_to == user.id)
    leads = q.all()
    counts = {s: 0 for s in STATUSES}
    for l in leads:
        if l.status in counts:
            counts[l.status] += 1
    return {"total": len(leads), **counts}


@app.get("/api/leads/by-rep")
def leads_by_rep(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    reps = db.query(User).filter(User.role == "sales_rep").all()
    result = []
    for rep in reps:
        rep_leads = db.query(Lead).filter(Lead.assigned_to == rep.id).all()
        counts = {s: 0 for s in STATUSES}
        for l in rep_leads:
            if l.status in counts:
                counts[l.status] += 1
        result.append({
            "id":    rep.id,
            "name":  rep.name,
            "email": rep.email,
            "total": len(rep_leads),
            **counts,
            "leads": [lead_to_dict(l) for l in rep_leads],
        })
    return result


# ── Scraping logic (mirrored from server.py) ────────────────────────────────────
async def _search_leads(query: str, max_results: int = 10) -> list[dict]:
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        results.append({"error": str(e)})
    return results


async def _scrape_contact_info(url: str) -> dict:
    info = {"url": url, "page_title": "", "emails": [], "phones": []}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = await client.get(url, headers=headers)
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(" ", strip=True)

            title_tag = soup.find("title")
            info["page_title"] = title_tag.get_text(strip=True) if title_tag else ""

            raw_emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
            info["emails"] = list({
                e for e in raw_emails
                if not re.search(r'\.(png|jpg|jpeg|gif|svg|webp|ico)$', e, re.I)
            })[:5]

            raw_phones = re.findall(r'(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})', text)
            info["phones"] = list(set(raw_phones))[:5]
    except Exception as e:
        info["error"] = str(e)
    return info


# ── Scrape routes ────────────────────────────────────────────────────────────────
@app.post("/api/scrape")
async def scrape(
    req: ScrapeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = f"{req.industry} companies in {req.location}"
    search_results = await _search_leads(query, max_results=min(req.quantity, 30))

    async def scrape_one(r: dict):
        if not r.get("url") or r.get("error"):
            return None
        contact = await _scrape_contact_info(r["url"])
        # Clean company name from page title
        raw_title = r.get("title", "")
        company = raw_title.split(" - ")[0].split(" | ")[0].split(" – ")[0][:80].strip()
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

    tasks = [scrape_one(r) for r in search_results[:req.quantity]]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    results = [r for r in raw if r and not isinstance(r, Exception)]

    # Apply contact filters
    if req.phone_req == "required":
        results = [r for r in results if r["has_phone"]]
    if req.email_req == "required":
        results = [r for r in results if r["has_email"]]

    # Save search session
    session = SearchSession(
        query=query,
        location=req.location,
        industry=req.industry,
        criteria=json.dumps(req.criteria),
        results_count=len(results),
        created_by=user.id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    return {
        "session_id": session.id,
        "query":      query,
        "results":    results,
        "total":      len(results),
    }


@app.get("/api/scrape/sessions")
def scrape_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(SearchSession)
    if user.role != "admin":
        q = q.filter(SearchSession.created_by == user.id)
    sessions = q.order_by(SearchSession.created_at.desc()).limit(50).all()
    return [
        {
            "id":            s.id,
            "query":         s.query,
            "location":      s.location,
            "industry":      s.industry,
            "results_count": s.results_count,
            "created_at":    s.created_at.isoformat() if s.created_at else None,
        }
        for s in sessions
    ]


# ── Rep routes ───────────────────────────────────────────────────────────────────
@app.get("/api/reps")
def get_reps(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    reps = db.query(User).filter(User.role == "sales_rep").all()
    result = []
    for rep in reps:
        leads = db.query(Lead).filter(Lead.assigned_to == rep.id).all()
        counts = {s: 0 for s in STATUSES}
        for l in leads:
            if l.status in counts:
                counts[l.status] += 1
        result.append({
            "id":          rep.id,
            "name":        rep.name,
            "email":       rep.email,
            "role":        rep.role,
            "created_at":  rep.created_at.isoformat() if rep.created_at else None,
            "total_leads": len(leads),
            **counts,
        })
    return result


@app.post("/api/reps", status_code=201)
def create_rep(req: RepCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email.lower().strip()).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=req.email.lower().strip(),
        password_hash=hash_password(req.password),
        name=req.name,
        role="sales_rep",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


@app.put("/api/reps/{rep_id}/assign")
def assign_lead(
    rep_id: int,
    body: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lead_id = body.get("lead_id")
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    rep = db.query(User).filter(User.id == rep_id, User.role == "sales_rep").first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")
    lead.assigned_to = rep_id
    lead.updated_at  = datetime.utcnow()
    db.commit()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
