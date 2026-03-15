#!/usr/bin/env python3
"""
Lead Generation MCP Server for Claude
Multi-rep sales dashboard — each sales rep gets their own sheet tab.
"""

import asyncio
import json
import re
import os
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
from duckduckgo_search import DDGS
from dotenv import load_dotenv

import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

load_dotenv()

app = Server("lead-gen")

# ── Config ─────────────────────────────────────────────────────────────────────

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")

HEADERS = ["Company/Name", "City", "Email", "Phone", "Website", "Status", "Notes", "Date Added", "Assigned To"]
STATUSES = ["Not Contacted", "Contacted", "Responded", "Converted", "Not Interested"]


# ── Google Sheets helpers ──────────────────────────────────────────────────────

def get_spreadsheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def get_rep_sheet(rep_name: str):
    """Get the worksheet tab for a sales rep, creating it if it doesn't exist."""
    spreadsheet = get_spreadsheet()
    # Normalize name: strip and title-case for consistent tab naming
    tab_name = rep_name.strip().title()
    try:
        sheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=20)
        sheet.append_row(HEADERS)
        _format_header_row(sheet)
    return sheet, tab_name


def _format_header_row(sheet):
    """Bold the header row."""
    try:
        sheet.format("A1:I1", {"textFormat": {"bold": True}})
    except Exception:
        pass  # formatting is optional, don't fail if it errors


def ensure_headers(sheet):
    first_row = sheet.row_values(1)
    if not first_row or first_row[0] != HEADERS[0]:
        sheet.insert_row(HEADERS, 1)
        _format_header_row(sheet)


def list_rep_tabs() -> list[str]:
    """Return all worksheet tab names (each = one sales rep)."""
    spreadsheet = get_spreadsheet()
    return [ws.title for ws in spreadsheet.worksheets()]


# ── Tool definitions ───────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_leads",
            description=(
                "Search the web for potential leads based on a niche, industry, or keyword + location. "
                "Returns a list of businesses with their URLs and snippets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query e.g. 'roofing companies in Dallas Texas'"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return (default 10)",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="scrape_contact_info",
            description="Visit a website URL and extract contact info: emails, phone numbers, page title.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The website URL to scrape"}
                },
                "required": ["url"]
            }
        ),
        types.Tool(
            name="create_rep",
            description=(
                "Create a new sales rep tab in the Google Sheet. "
                "Each rep gets their own sheet with all the lead columns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rep_name": {"type": "string", "description": "Full name of the sales rep (e.g. 'John Smith')"}
                },
                "required": ["rep_name"]
            }
        ),
        types.Tool(
            name="list_reps",
            description="List all sales rep tabs currently in the Google Sheet.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="save_lead",
            description="Save a lead to a specific sales rep's sheet tab.",
            inputSchema={
                "type": "object",
                "properties": {
                    "rep_name": {
                        "type": "string",
                        "description": "Which sales rep to assign this lead to"
                    },
                    "company_name": {"type": "string", "description": "Business or contact name"},
                    "city": {"type": "string", "description": "City or location"},
                    "email": {"type": "string", "description": "Email address"},
                    "phone": {"type": "string", "description": "Phone number"},
                    "website": {"type": "string", "description": "Website URL"},
                    "status": {
                        "type": "string",
                        "enum": STATUSES,
                        "description": "Outreach status",
                        "default": "Not Contacted"
                    },
                    "notes": {"type": "string", "description": "Any notes about this lead"}
                },
                "required": ["rep_name", "company_name"]
            }
        ),
        types.Tool(
            name="get_leads",
            description="Get all leads for a specific sales rep (or all reps if none specified).",
            inputSchema={
                "type": "object",
                "properties": {
                    "rep_name": {
                        "type": "string",
                        "description": "Sales rep name. Leave empty to get leads from ALL reps."
                    }
                }
            }
        ),
        types.Tool(
            name="update_lead_status",
            description="Update the outreach status of a lead in a rep's sheet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "rep_name": {
                        "type": "string",
                        "description": "The sales rep whose sheet to update"
                    },
                    "company_name": {
                        "type": "string",
                        "description": "Company/lead name to update"
                    },
                    "status": {
                        "type": "string",
                        "enum": STATUSES
                    },
                    "notes": {"type": "string", "description": "Optional notes to append"}
                },
                "required": ["rep_name", "company_name", "status"]
            }
        ),
        types.Tool(
            name="move_lead",
            description="Move a lead from one sales rep's sheet to another.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_rep": {"type": "string", "description": "Current rep's name"},
                    "to_rep": {"type": "string", "description": "New rep's name"},
                    "company_name": {"type": "string", "description": "Lead to move"}
                },
                "required": ["from_rep", "to_rep", "company_name"]
            }
        ),
        types.Tool(
            name="get_rep_summary",
            description="Get a performance summary for each sales rep: total leads, contacted, converted, etc.",
            inputSchema={"type": "object", "properties": {}}
        )
    ]


# ── Tool handlers ──────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "search_leads":
        results = await _search_leads(arguments["query"], arguments.get("max_results", 10))
        return [types.TextContent(type="text", text=json.dumps(results, indent=2))]

    elif name == "scrape_contact_info":
        info = await _scrape_contact_info(arguments["url"])
        return [types.TextContent(type="text", text=json.dumps(info, indent=2))]

    elif name == "create_rep":
        result = _create_rep(arguments["rep_name"])
        return [types.TextContent(type="text", text=result)]

    elif name == "list_reps":
        tabs = list_rep_tabs()
        return [types.TextContent(type="text", text=json.dumps(tabs, indent=2))]

    elif name == "save_lead":
        result = _save_lead(arguments)
        return [types.TextContent(type="text", text=result)]

    elif name == "get_leads":
        rep = arguments.get("rep_name", "").strip()
        if rep:
            leads = _get_leads_for_rep(rep)
        else:
            leads = _get_all_leads()
        return [types.TextContent(type="text", text=json.dumps(leads, indent=2))]

    elif name == "update_lead_status":
        result = _update_status(
            arguments["rep_name"],
            arguments["company_name"],
            arguments["status"],
            arguments.get("notes", "")
        )
        return [types.TextContent(type="text", text=result)]

    elif name == "move_lead":
        result = _move_lead(arguments["from_rep"], arguments["to_rep"], arguments["company_name"])
        return [types.TextContent(type="text", text=result)]

    elif name == "get_rep_summary":
        summary = _get_rep_summary()
        return [types.TextContent(type="text", text=json.dumps(summary, indent=2))]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ── Core logic ─────────────────────────────────────────────────────────────────

async def _search_leads(query: str, max_results: int = 10) -> list[dict]:
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", "")
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


def _create_rep(rep_name: str) -> str:
    try:
        tabs = list_rep_tabs()
        tab_name = rep_name.strip().title()
        if tab_name in tabs:
            return f"Sheet for '{tab_name}' already exists."
        _, created_name = get_rep_sheet(rep_name)
        return f"✓ Created sheet tab for sales rep '{created_name}'."
    except Exception as e:
        return f"Error creating rep sheet: {e}"


def _save_lead(data: dict) -> str:
    try:
        sheet, tab_name = get_rep_sheet(data["rep_name"])
        ensure_headers(sheet)
        row = [
            data.get("company_name", ""),
            data.get("city", ""),
            data.get("email", ""),
            data.get("phone", ""),
            data.get("website", ""),
            data.get("status", "Not Contacted"),
            data.get("notes", ""),
            datetime.now().strftime("%Y-%m-%d"),
            tab_name
        ]
        sheet.append_row(row)
        return f"✓ Lead '{data.get('company_name')}' saved to {tab_name}'s sheet."
    except Exception as e:
        return f"Error saving lead: {e}"


def _get_leads_for_rep(rep_name: str) -> dict:
    try:
        sheet, tab_name = get_rep_sheet(rep_name)
        records = sheet.get_all_records()
        return {"rep": tab_name, "leads": records, "total": len(records)}
    except Exception as e:
        return {"error": str(e)}


def _get_all_leads() -> list[dict]:
    try:
        tabs = list_rep_tabs()
        all_data = []
        for tab in tabs:
            try:
                sheet, _ = get_rep_sheet(tab)
                records = sheet.get_all_records()
                all_data.append({"rep": tab, "leads": records, "total": len(records)})
            except Exception:
                continue
        return all_data
    except Exception as e:
        return [{"error": str(e)}]


def _update_status(rep_name: str, company_name: str, status: str, notes: str = "") -> str:
    try:
        sheet, tab_name = get_rep_sheet(rep_name)
        records = sheet.get_all_records()
        for i, record in enumerate(records, start=2):
            if record.get("Company/Name", "").strip().lower() == company_name.strip().lower():
                sheet.update_cell(i, 6, status)
                if notes:
                    existing = record.get("Notes", "")
                    combined = f"{existing}; {notes}".strip("; ") if existing else notes
                    sheet.update_cell(i, 7, combined)
                return f"✓ Updated '{company_name}' in {tab_name}'s sheet → '{status}'."
        return f"Lead '{company_name}' not found in {tab_name}'s sheet."
    except Exception as e:
        return f"Error updating lead: {e}"


def _move_lead(from_rep: str, to_rep: str, company_name: str) -> str:
    try:
        from_sheet, from_name = get_rep_sheet(from_rep)
        records = from_sheet.get_all_records()
        target_row = None
        target_index = None

        for i, record in enumerate(records, start=2):
            if record.get("Company/Name", "").strip().lower() == company_name.strip().lower():
                target_row = record
                target_index = i
                break

        if not target_row:
            return f"Lead '{company_name}' not found in {from_name}'s sheet."

        # Save to new rep's sheet
        to_sheet, to_name = get_rep_sheet(to_rep)
        ensure_headers(to_sheet)
        row = [
            target_row.get("Company/Name", ""),
            target_row.get("City", ""),
            target_row.get("Email", ""),
            target_row.get("Phone", ""),
            target_row.get("Website", ""),
            target_row.get("Status", "Not Contacted"),
            target_row.get("Notes", ""),
            target_row.get("Date Added", datetime.now().strftime("%Y-%m-%d")),
            to_name
        ]
        to_sheet.append_row(row)

        # Delete from old rep's sheet
        from_sheet.delete_rows(target_index)

        return f"✓ Moved '{company_name}' from {from_name} → {to_name}."
    except Exception as e:
        return f"Error moving lead: {e}"


def _get_rep_summary() -> list[dict]:
    try:
        tabs = list_rep_tabs()
        summary = []
        for tab in tabs:
            try:
                sheet, _ = get_rep_sheet(tab)
                records = sheet.get_all_records()
                counts = {s: 0 for s in STATUSES}
                for r in records:
                    s = r.get("Status", "Not Contacted")
                    if s in counts:
                        counts[s] += 1
                summary.append({
                    "rep": tab,
                    "total_leads": len(records),
                    **counts
                })
            except Exception:
                continue
        return summary
    except Exception as e:
        return [{"error": str(e)}]


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="lead-gen",
                server_version="1.0.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={}
                )
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
