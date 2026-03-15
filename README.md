# Lead Gen MCP — Sales Rep Dashboard

An MCP (Model Context Protocol) server that turns Claude into a lead generation agent for your whole sales team. Each sales rep gets their own Google Sheet tab. Claude searches for leads, scrapes contact info, and tracks outreach status per rep.

---

## Features

- Search the web for leads by niche + location
- Scrape websites for emails and phone numbers
- Each sales rep gets their own sheet tab
- Track outreach: Not Contacted → Contacted → Responded → Converted
- Move leads between reps
- Performance summary per rep

---

## Google Sheet layout

One tab per sales rep. Each tab has these columns:

| Company/Name | City | Email | Phone | Website | Status | Notes | Date Added | Assigned To |
|---|---|---|---|---|---|---|---|---|

**Status options:** Not Contacted / Contacted / Responded / Converted / Not Interested

---

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable the **Google Sheets API**
4. Go to **IAM & Admin → Service Accounts** → Create a service account
5. Create a key (JSON) → download and save as `credentials.json` in this folder
6. Create a new Google Sheet
7. Share the sheet with the service account email (give it **Editor** access)
8. Copy the Sheet ID from the URL: `docs.google.com/spreadsheets/d/**THIS_PART**/edit`

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```
GOOGLE_SHEET_ID=your_sheet_id_here
GOOGLE_CREDENTIALS_FILE=credentials.json
```

### 4. Add to Claude Code

```bash
claude mcp add lead-gen -- python "C:/path/to/server.py"
```

Or for Claude Desktop, add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lead-gen": {
      "command": "python",
      "args": ["C:/path/to/server.py"]
    }
  }
}
```

---

## Example usage

```
# Set up reps
"Create a sheet for John Smith"
"Create a sheet for Maria Garcia"

# Find and assign leads
"Find 10 leads for roofing companies in Dallas and assign them to John"
"Find dental clinics in Chicago for Maria"

# Manage leads
"Show me all leads for John Smith"
"Mark Apex Roofing as Contacted in John's sheet"
"Move Apex Roofing from John to Maria"

# Dashboard
"Give me a summary of all reps"
```

---

## Project structure

```
lead-gen-mcp/
├── server.py          # MCP server with all tools
├── requirements.txt   # Python dependencies
├── .env.example       # Environment variable template
├── CLAUDE.md          # Claude agent instructions
└── README.md          # This file
```
