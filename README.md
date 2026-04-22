# Ablage

> AI-powered personal document archive — scan, classify, search, and retrieve any document in seconds.

Ablage watches an OneDrive folder for incoming PDFs, extracts their text, asks GPT-4o to classify and name them, files them into the right archive folder, and indexes everything for full-text and natural-language search. A web UI and Telegram bot let you find any document from anywhere.

---

## What it does

```
📄 PDF dropped into OneDrive
          │
          ▼
  Graph API delta poll
          │
          ▼
  Text extraction (PyPDF2)
          │
          ▼
  GPT-4o analysis
  ┌───────┴────────────────────┐
  │  • document type           │
  │  • structured filename     │
  │  • date, sender, company   │
  │  • keywords                │
  └───────┬────────────────────┘
          │
          ▼
  Classification rules (YAML)
          │
          ▼
  Move + rename on OneDrive          ┐
  Index in SQLite (FTS5 + vectors)   ├─ all atomic, no local sync needed
  Store chunk embeddings             ┘
          │
          ├──► Telegram notification + deep link
          └──► Home Assistant push notification
```

---

## Features

### Automatic archiving
Drop a PDF anywhere into the watch folder. Ablage picks it up within 30 seconds, renames it to a structured filename (`Praemienrechnung_Allianz_Manuel_V955415027_20260301.pdf`), and files it into the right subfolder (`Versicherung/2026/`). No manual sorting, ever.

### AI classification
GPT-4o reads the document and determines:
- **Type** — invoice, insurance, tax, payslip, donation receipt, medical report, bank statement, contract, certificate, letter, quote, or ID document
- **Filename** — structured, consistent, human-readable
- **Metadata** — document date, sender, company, recipient, keywords

### Smart routing
A YAML rule file maps document types to archive folders. Fully editable from the web UI — no restarts needed.

### Full-text + semantic search
Every document is indexed three ways:
- **FTS5** — exact keyword search over filenames, senders, companies, and extracted text
- **Document embeddings** — find documents by meaning, not just keywords
- **Chunk embeddings** — answer questions about document *content* ("how much did Anthropic invoice me in total?")

### Natural-language queries
Ask in any language, from Telegram or the web UI:

> *"Welche Versicherungsunterlagen habe ich von Allianz?"*
> *"What was the last invoice I received?"*
> *"How much did I pay for electricity in 2025?"*

GPT-4o-mini extracts the intent, queries the database, reads the relevant document chunks, and answers — with clickable links to each source document.

### Web UI
A clean, mobile-friendly interface to search, filter, view metadata, reclassify, rename, and open documents. Runs behind your existing reverse proxy with session authentication.

![Web UI](https://raw.githubusercontent.com/ghmeister/ablage/main/docs/screenshot.png)

### Telegram bot
Get notified the moment a document is filed. Ask questions. Forward a PDF to archive it instantly.

| Command | Description |
|---|---|
| `/suche Begriff` | Keyword search |
| `/statistik` | Archive overview |
| `/hilfe` | Command reference |
| Send a PDF | Upload directly to the archive |
| Any message | Natural-language document query |

### Email integration
Works alongside [email-pdf-extractor](https://github.com/MinenMaster/email-pdf-extractor): PDF attachments from Gmail arrive in the drop zone with a `.meta.json` sidecar carrying sender, subject, and message ID. The web UI shows the original email context and a direct Gmail deep link.

### Duplicate detection
SHA-256 hash of extracted text. If a duplicate arrives, you get a Telegram alert with a one-tap option to keep or discard it before it's filed.

---

## Architecture

| Component | File | Role |
|---|---|---|
| Bot | `bot.py` | Orchestration, delta poll loop, notifications |
| Graph client | `graph_client.py` | OneDrive via Microsoft Graph API |
| AI renamer | `ai_renamer.py` | GPT-4o document analysis |
| PDF extractor | `pdf_extractor.py` | Text extraction |
| Folder classifier | `folder_classifier.py` | YAML rule engine |
| Database | `db.py` | SQLite — FTS5, sqlite-vec, CRUD |
| Embeddings | `embed.py` | OpenAI text-embedding-3-small, retry logic |
| NL search | `nl_search.py` | Two-step GPT intent extraction + RAG |
| Telegram | `telegram_bot.py` | Commands, NL queries, PDF upload |
| Web UI | `web/app.py` | Flask + gunicorn, search, document management |

---

## Quick start

### 1. Azure App Registration

1. [portal.azure.com](https://portal.azure.com) → **App registrations** → **New registration**
2. Note the **Application (client) ID** and **Directory (tenant) ID**
3. Add API permission: **Microsoft Graph → Files.ReadWrite** (delegated)
4. **Personal OneDrive** — no client secret needed; device-code login on first run, token cached permanently
5. **Work/school OneDrive** — create a client secret and set `CLIENT_SECRET` + `USER_ID`

### 2. Find your drop zone folder ID

```
GET https://graph.microsoft.com/v1.0/me/drive/root/children
```

Use [Graph Explorer](https://aka.ms/ge) and copy the `id` of the folder you want Ablage to watch.

### 3. Deploy with Docker Compose

```yaml
name: ablage

services:
  bot:
    image: ghcr.io/ghmeister/ablage:latest
    restart: unless-stopped
    user: "1000:1000"
    environment:
      - OPENAI_API_KEY=sk-...
      - TENANT_ID=...
      - CLIENT_ID=...
      - SOURCE_FOLDER_ID=...
      - OUTPUT_BASE_FOLDER=Archive
      - DB_PATH=/data/documents.db
      - TOKEN_CACHE_PATH=/data/token_cache.json
      # Optional
      # - TELEGRAM_BOT_TOKEN=...
      # - TELEGRAM_CHAT_ID=...
      # - ABLAGE_URL=https://ablage.example.com
      # - HA_URL=http://homeassistant:8123
      # - HA_TOKEN=...
      # - HA_NOTIFY_SERVICE=mobile_app_my_iphone
    volumes:
      - doc_data:/data

  web:
    image: ghcr.io/ghmeister/ablage:latest
    restart: unless-stopped
    user: "1000:1000"
    command: ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2",
              "--timeout", "120", "--worker-tmp-dir", "/tmp", "web.app:app"]
    environment:
      - HOME=/tmp
      - OPENAI_API_KEY=sk-...
      - DB_PATH=/data/documents.db
      - TENANT_ID=...
      - CLIENT_ID=...
      - OUTPUT_BASE_FOLDER=Archive
      - UI_USER=admin
      - UI_PASSWORD=...
      - SECRET_KEY=...        # any long random string
      - ARCHIVE_ROOT=/archive # optional: serve PDFs locally
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

```bash
docker compose up -d

# First run: check logs for the one-time OneDrive login URL
docker compose logs -f bot
```

### 4. Backfill embeddings (first deployment only)

```bash
docker exec ablage-bot-1 python backfill_chunks.py
```

This embeds all existing documents for semantic and NL search (~$0.01 per 500 documents).

---

## Environment variables

### Bot

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✓ | OpenAI API key |
| `TENANT_ID` | ✓ | Azure AD tenant ID |
| `CLIENT_ID` | ✓ | Azure app client ID |
| `SOURCE_FOLDER_ID` | ✓ | OneDrive folder item ID to watch |
| `OUTPUT_BASE_FOLDER` | ✓ | Archive root path in OneDrive (e.g. `Archive`) |
| `CLIENT_SECRET` | — | App-only auth for work/school accounts |
| `USER_ID` | — | UPN for app-only auth |
| `POLL_INTERVAL_SECONDS` | — | Delta poll interval (default: `30`) |
| `MAX_FILENAME_LENGTH` | — | Filename character limit (default: `100`) |
| `DB_PATH` | — | SQLite path (default: `/data/documents.db`) |
| `TOKEN_CACHE_PATH` | — | MSAL token cache (default: `/data/token_cache.json`) |
| `NL_MAX_DISTANCE` | — | Vector search threshold (default: `1.05`) |
| `TELEGRAM_BOT_TOKEN` | — | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |
| `ABLAGE_URL` | — | Public URL of the web UI (for Telegram deep links) |
| `HA_URL` | — | Home Assistant base URL |
| `HA_TOKEN` | — | Home Assistant long-lived access token |
| `HA_NOTIFY_SERVICE` | — | HA notify service (e.g. `mobile_app_my_iphone`) |

### Web UI

| Variable | Required | Description |
|---|---|---|
| `DB_PATH` | ✓ | SQLite path |
| `UI_USER` | ✓ | Login username |
| `UI_PASSWORD` | ✓ | Login password |
| `SECRET_KEY` | ✓ | Flask session secret — any long random string |
| `OPENAI_API_KEY` | ✓ | Required for NL search in the web UI |
| `TENANT_ID` / `CLIENT_ID` | — | Required for reclassify and rename features |
| `OUTPUT_BASE_FOLDER` | — | Must match bot config |
| `ARCHIVE_ROOT` | — | Local mount path for PDFs — skips Graph API round-trip |

---

## Document types

| Type | Folder | Examples |
|---|---|---|
| `invoice` | `Rechnungen/` | Bills, receipts, payment requests |
| `insurance` | `Versicherung/` | Policies, premium invoices |
| `tax` | `Steuer/` | Tax assessments, BVG/pension statements |
| `payslip` | `Lohnausweise/` | Salary slips, payroll documents |
| `donation_receipt` | `Spenden/` | Spendenbescheinigungen |
| `bank_statement` | `Kontoauszüge/` | Account and credit card statements |
| `medical_report` | `Arzt/` | Doctor reports, lab results |
| `certificate` | `Zeugnisse/` | School reports, diplomas, work references |
| `contract` | `Verträge/` | Contracts, warranties, agreements |
| `letter` | `Briefe/` | Official correspondence |
| `quote` | `Offerten/` | Offers, cost estimates |
| `id_document` | `Ausweise/` | Passports, ID cards, permits |
| `other` | `Diverses/` | Everything else |

All folders get a year subfolder automatically: `Rechnungen/2026/`.

Classification rules are fully editable in the web UI under **Rules** — changes take effect immediately without restarting.

---

## Classification rules

```yaml
# classification_rules.yaml
unmatched_folder: "Diverses"

type_to_folder:
  invoice:          "Rechnungen"
  insurance:        "Versicherung"
  tax:              "Steuer"
  payslip:          "Lohnausweise"
  donation_receipt: "Spenden"
  bank_statement:   "Kontoauszüge"
  medical_report:   "Arzt"
  certificate:      "Zeugnisse"
  contract:         "Verträge"
  letter:           "Briefe"
  quote:            "Offerten"
  id_document:      "Ausweise"
```

---

## Development

```bash
pip install -r requirements.txt

# Bot
cp config.example.env .env
python bot.py

# Web UI
DB_PATH=./documents.db UI_USER=admin UI_PASSWORD=secret SECRET_KEY=dev python web/app.py
```

The Docker image is built and pushed to `ghcr.io/ghmeister/ablage` automatically on every push to `main` via GitHub Actions. Versions follow semantic versioning driven by commit message conventions (`feat:` → minor, `fix:` → patch, `BREAKING CHANGE` → major).

---

## Tech stack

- **Python 3.11** — bot, web UI, all processing
- **OpenAI GPT-4o** — document classification and filename generation
- **OpenAI text-embedding-3-small** — semantic search vectors
- **SQLite** with **FTS5** (full-text) and **sqlite-vec** (vector search)
- **Microsoft Graph API** + **MSAL** — OneDrive access without local sync
- **Flask** + **Gunicorn** — web UI
- **Docker** — single image, two containers (bot + web)
