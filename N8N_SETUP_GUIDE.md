# n8n Setup Guide

## Overview

Two n8n workflows work together with the Python agent:

```
[Google Drive]                         [Python Agent]
     │                                      │
     ▼                                      ▼
Workflow 1: Download files          Workflow 2: Receive alert
to watched folder on server    ←→   POST missing drawings list
                                    → Send Gmail to user
```

---

## Prerequisites

Before importing the workflows:

1. **Google Drive OAuth2 credential** – set up in n8n Settings → Credentials → New → Google Drive OAuth2
2. **Gmail OAuth2 credential** – set up in n8n Settings → Credentials → New → Gmail OAuth2
3. **Python agent running on the server** – see agent README (run `python agent.py`)

---

## Workflow 1 – Google Drive Downloader

**File:** `n8n_workflow_1_downloader.json`

### Import
1. In n8n, go to **Workflows → Import from file**
2. Select `n8n_workflow_1_downloader.json`

### Configure (3 things to change)

| Node | What to change |
|---|---|
| **Google Drive Trigger** | Select your **Google Drive credential** and pick the **folder** to watch |
| **Compute Save Path** | Change `WATCH_DIR` constant to match `WATCH_DIR` in your `.env` |
| **Download File** | Select the same **Google Drive credential** |

### How it works
1. Triggers when a new PDF is created in your chosen Google Drive folder
2. Extracts the project number from the filename (e.g. `210-12345-01.pdf` → subfolder `210-12345-01`)
3. Downloads the file and saves it to `WATCH_DIR/<project>/<filename>.pdf`
4. The Python agent detects the new file and runs its analysis

---

## Workflow 2 – Missing Drawing Alert → Gmail

**File:** `n8n_workflow_2_alert.json`

### Import
1. In n8n, go to **Workflows → Import from file**
2. Select `n8n_workflow_2_alert.json`

### Configure (2 things to change)

| Node | What to change |
|---|---|
| **Send Gmail Alert** | Change `CHANGE_TO_RECIPIENT_EMAIL@example.com` to the actual recipient |
| **Send Gmail Alert** | Select your **Gmail credential** |

### Get the Webhook URL

After importing and saving:
1. Click the **Receive Alert** webhook node
2. Copy the **Production URL** – it looks like:
   ```
   https://your-n8n-host/webhook/drawing-alert
   ```
3. Paste this URL into your `.env` as `N8N_WEBHOOK_URL`

Since the Python agent runs on the **same server** as n8n, you can also use:
```
http://localhost:5678/webhook/drawing-alert
```
(Replace `5678` with your actual n8n port if different)

### How it works
1. Python agent POSTs a JSON payload to this webhook after its analysis
2. n8n builds a formatted HTML email with the missing drawing table
3. If `missing_count > 0` → sends Gmail alert
4. If all drawings are present → silently passes (no email)
5. Always responds HTTP 200 so the agent knows the notification was received

---

## Email Preview

When drawings are missing, the recipient receives:

```
Subject: [MISSING DRAWINGS] 210-12345-01 – 2 drawing(s) missing

⚠️ Missing Drawing Alert

Project 210-12345-01 found 2 missing fabrication drawing(s).

┌─────────────────┬──────────────────┬─────┬──────────────────────┐
│ Part No         │ Description      │ Qty │ Type                 │
├─────────────────┼──────────────────┼─────┼──────────────────────┤
│ 290-1234-01     │ BRACKET LH       │  2  │ Sheet Metal (290-)   │
│ 300-5678-02     │ SHAFT MAIN       │  1  │ Machining (300-)     │
└─────────────────┴──────────────────┴─────┴──────────────────────┘

Total fabrication parts: 5
Present: 3
Missing: 2

Please upload the missing drawings to the project folder on Google Drive.
```

---

## Full System Flow

```
Google Drive (new PDF uploaded)
        │
        ▼
n8n Workflow 1: Download all PDFs into:
  /opt/agenticdocument/downloads/<project_folder>/

        │  (files detected by watchdog)
        ▼

Python agent (agent.py):
  1. Finds 210-xxxxx-xx.pdf  ← main weldment
  2. Extracts part list table
  3. Filters 290-* (sheet metal) and 300-* (machining) parts
  4. Checks which ones have a matching PDF in the same folder
  5. POSTs results to n8n webhook

        │
        ▼
n8n Workflow 2:
  • missing_count > 0 → Send Gmail with missing drawing table
  • missing_count = 0 → No action (all drawings present)
```

---

## Testing

### Test the Python agent manually

```bash
# Create a test project folder
mkdir -p ~/test_downloads/210-99999-01

# Copy a real weldment PDF into it (with a part list table)
cp your_weldment.pdf ~/test_downloads/210-99999-01/210-99999-01.pdf

# Copy some fabrication drawings (leave one out to test missing detection)
cp your_sheet_metal.pdf ~/test_downloads/210-99999-01/290-1111-01.pdf
# 300-2222-02.pdf intentionally NOT copied

# Set WATCH_DIR to ~/test_downloads in .env
# Then run the agent and drop the folder in:
python agent.py
```

### Test the n8n webhook

Use a tool like [webhook.site](https://webhook.site) or curl:

```bash
curl -X POST http://localhost:5678/webhook/drawing-alert \
  -H "Content-Type: application/json" \
  -d '{
    "project": "210-99999-01",
    "folder": "/test/path",
    "timestamp": "2026-02-20T10:00:00Z",
    "total_fabrication_parts": 3,
    "missing_count": 1,
    "all_ok": false,
    "missing_drawings": [
      {"part_no": "300-2222-02", "description": "TEST SHAFT", "qty": 1, "type": "machining"}
    ]
  }'
```

This should trigger a Gmail alert to your configured recipient.
