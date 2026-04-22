# Ablage

AI-powered document archiving for OneDrive. Drop a PDF into a watch folder — Ablage picks it up, extracts the text, asks an AI to classify and name it, moves it to the right archive folder, and indexes it for instant search.

---

## Features

- **Automatic archiving** — monitors an OneDrive folder via Microsoft Graph delta polling; no local sync required
- **AI classification** — GPT-4o extracts document type, date, sender, recipient, keywords, and suggests a structured filename
- **Smart routing** — configurable rules map document types to archive subfolders (e.g. invoices → `Archive/Rechnungen/2026`)
- **Full-text search** — SQLite FTS5 index over filenames, senders, companies, keywords, and extracted text
- **Semantic search** — sqlite-vec embeddings (`text-embedding-3-small`) for natural-language queries that survive plurals, synonyms, and cross-language phrasing
- **Web UI** — search, filter, view metadata, reclassify, rename, and open PDFs directly from the browser
- **Telegram bot** — new-document notifications, `/suche`, `/statistik`, natural-language questions, PDF forwarding, and duplicate alerts with one-tap delete
- **Email integration** — works alongside [email-pdf-extractor](https://github.com/MinenMaster/email-pdf-extractor): attachments arrive with a `.meta.json` sidecar carrying sender, subject, and message ID for richer metadata
- **Duplicate detection** — SHA-256 hash of extracted text; duplicates trigger a Telegram alert before filing
- **Home Assistant notifications** — optional push notification to any HA notify service on new documents

---

## Architecture

```
OneDrive Drop Zone
       │
       ▼
 Graph delta poll  ──►  PDFExtractor  ──►  AIRenamer (GPT-4o)
                                                  │
                              ┌───────────────────┤
                              ▼                   ▼
                       FolderClassifier     SQLite index
                              │            (FTS5 + vectors)
                              ▼
                     OneDrive Archive
                     (move + rename)
```

| Component | File | Purpose |
|---|---|---|
| Bot | `bot.py` | Orchestration, polling loop, notifications |
| Graph client | `graph_client.py` | OneDrive API: download, move, rename, upload, delete |
| AI renamer | `ai_renamer.py` | GPT-4o document analysis → structured metadata |
| PDF extractor | `pdf_extractor.py` | Text extraction with PyPDF2 |
| Folder classifier | `folder_classifier.py` | YAML rule engine → archive path |
| Database | `db.py` | SQLite schema, FTS5, sqlite-vec, CRUD |
| Embeddings | `embed.py` | Vector generation and serialization |
| Telegram bot | `telegram_bot.py` | Polling, commands, NL queries, PDF upload |
| Web UI | `web/app.py` | Flask app with search, document view, rule editor |

---

## Quick Start

### 1. Azure App Registration

1. Go to [portal.azure.com](https://portal.azure.com) → **App registrations** → **New registration**
2. Note the **Application (client) ID** and **Directory (tenant) ID**
3. Add API permission: **Microsoft Graph → Files.ReadWrite** (delegated) or **Files.ReadWrite.All** (application)
4. **Mode A — Personal OneDrive:** no client secret needed — device-code login on first run, token cached for all future runs
5. **Mode B — Work/school OneDrive:** create a client secret under **Certificates & secrets**

### 2. Find your drop zone folder ID

Use [Graph Explorer](https://aka.ms/ge):

```
GET https://graph.microsoft.com/v1.0/me/drive/root/children
```

Copy the `id` of the folder you want Ablage to watch.

### 3. Configure

```bash
cp config.example.env .env
# Fill in OPENAI_API_KEY, TENANT_ID, CLIENT_ID, SOURCE_FOLDER_ID
```

### 4. Run with Docker Compose

```bash
docker compose up -d
```

On first run (Mode A), check the logs for the one-time login URL:

```bash
docker compose logs -f ablage
```

---

## Docker Compose

```yaml
services:
  ablage:
    image: ghcr.io/ghmeister/ablage:latest
    restart: unless-stopped
    environment:
      - OPENAI_API_KEY=sk-...
      - TENANT_ID=...
      - CLIENT_ID=...
      - SOURCE_FOLDER_ID=...
      - OUTPUT_BASE_FOLDER=Archive
      - POLL_INTERVAL_SECONDS=30
      - DB_PATH=/data/documents.db
      - TOKEN_CACHE_PATH=/data/token_cache.json
      # Optional: Telegram
      # - TELEGRAM_BOT_TOKEN=...
      # - TELEGRAM_CHAT_ID=...
      # - ABLAGE_URL=https://ablage.example.com
      # Optional: Home Assistant
      # - HA_URL=http://homeassistant:8123
      # - HA_TOKEN=...
      # - HA_NOTIFY_SERVICE=mobile_app_my_iphone
    volumes:
      - doc_data:/data

  ablage-web:
    image: ghcr.io/ghmeister/ablage:latest
    restart: unless-stopped
    command: ["python", "web/app.py"]
    environment:
      - DB_PATH=/data/documents.db
      - TENANT_ID=...
      - CLIENT_ID=...
      - OUTPUT_BASE_FOLDER=Archive
      - UI_USER=admin
      - UI_PASSWORD=changeme
      - SECRET_KEY=random-string-here
      # Optional: serve PDFs locally without Graph API round-trip
      # - ARCHIVE_ROOT=/archive
    volumes:
      - doc_data:/data
      # - /path/to/OneDrive/Archive:/archive:ro
    networks:
      - proxy

volumes:
  doc_data:

networks:
  proxy:
    external: true
```

---

## Environment Variables

### Bot (`ablage`)

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✓ | OpenAI API key |
| `TENANT_ID` | ✓ | Azure AD tenant ID |
| `CLIENT_ID` | ✓ | Azure App client ID |
| `SOURCE_FOLDER_ID` | ✓ | OneDrive folder item ID to watch |
| `CLIENT_SECRET` | — | App-only auth (work/school accounts with M365) |
| `USER_ID` | — | Target user UPN for app-only auth |
| `OUTPUT_BASE_FOLDER` | — | Archive root path in OneDrive (e.g. `Archive`) |
| `CLASSIFICATION_RULES_FILE` | — | Path to rules YAML (default: `classification_rules.yaml`) |
| `POLL_INTERVAL_SECONDS` | — | Delta poll interval in seconds (default: `30`) |
| `MAX_FILENAME_LENGTH` | — | Filename character limit (default: `100`) |
| `DB_PATH` | — | SQLite database path (default: `/data/documents.db`) |
| `TOKEN_CACHE_PATH` | — | MSAL token cache path (default: `/data/token_cache.json`) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |
| `ABLAGE_URL` | — | Public URL of the web UI (for Telegram deep links) |
| `HA_URL` | — | Home Assistant base URL |
| `HA_TOKEN` | — | Home Assistant long-lived access token |
| `HA_NOTIFY_SERVICE` | — | HA notify service name (e.g. `mobile_app_my_iphone`) |

### Web UI (`ablage-web`)

| Variable | Required | Description |
|---|---|---|
| `DB_PATH` | ✓ | SQLite database path |
| `UI_USER` | ✓ | Login username |
| `UI_PASSWORD` | ✓ | Login password |
| `SECRET_KEY` | ✓ | Flask session secret (any random string) |
| `TENANT_ID` / `CLIENT_ID` | — | Required for reclassify and rename features |
| `OUTPUT_BASE_FOLDER` | — | Archive root, must match bot config |
| `ARCHIVE_ROOT` | — | Local mount path for PDFs (faster serving, no Graph API call) |

---

## Classification Rules

Edit `classification_rules.yaml` to control where each document type is filed:

```yaml
unmatched_folder: "Misc"

type_to_folder:
  invoice:   "Rechnungen"
  insurance: "Versicherung"
  tax:       "Steuer"
  quote:     "Offerten"
  contract:  "Verträge"
```

Files are moved to `{OUTPUT_BASE_FOLDER}/{folder}/{year}/` — a 2026 invoice goes to `Archive/Rechnungen/2026/`. Rules can also be edited live in the web UI under **Rules**.

---

## Telegram Bot

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the bot's environment to enable.

| Command / Input | Description |
|---|---|
| `/suche Begriff` | Full-text keyword search |
| `/statistik` | Archive overview: totals, breakdown by type |
| `/hilfe` | Command reference |
| Send a PDF | Uploads directly to the drop zone and triggers processing |
| Any other message | Natural-language document query |

**Natural-language examples:**
- *"Wann habe ich zuletzt eine Rechnung von Swisscom erhalten?"*
- *"Zeige mir alle Spendenbescheinigungen"*
- *"Welche Versicherungsunterlagen habe ich von 2024?"*
- *"When did I receive the last offer from Ramseier?"*

Search uses a hybrid approach: FTS5 keyword matching (precise for proper nouns) combined with semantic vector search via sqlite-vec (handles plurals, synonyms, cross-language queries), merged with Reciprocal Rank Fusion. Results are answered by GPT-4o-mini in German with clickable deep links to each document.

When a duplicate is detected during archiving, a Telegram alert lets you keep or delete the new file with a single tap.

---

## Semantic Search: Backfill

After first deployment, run the backfill script once to embed existing documents:

```bash
docker exec <ablage-container-name> python backfill_embeddings.py
```

This calls the OpenAI embedding API for each document (~$0.01 for 500 docs, ~1 minute). New documents are embedded automatically on ingest.

---

## Email Integration

When used with [email-pdf-extractor](https://github.com/MinenMaster/email-pdf-extractor), PDF attachments arrive in the drop zone alongside a `.meta.json` sidecar:

```json
{
  "from": "billing@swisscom.ch",
  "subject": "Ihre Rechnung Januar 2026",
  "date": "2026-01-15",
  "message_id": "<abc123@swisscom.ch>"
}
```

Ablage reads the sidecar, enriches the document record with email context, then deletes it. The web UI and Telegram notifications include the original sender and a direct Gmail deep link.

---

## Development

```bash
pip install -r requirements.txt

# Run bot locally
cp config.example.env .env
python bot.py

# Run web UI locally
DB_PATH=./documents.db UI_USER=admin UI_PASSWORD=secret SECRET_KEY=dev python web/app.py
```

The Docker image is built and pushed to GHCR automatically on every push to `main` via GitHub Actions.
