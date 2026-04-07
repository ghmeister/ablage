# Ablage

An AI-powered document archiving system for OneDrive. Watches a drop-zone folder, renames incoming PDFs based on their content using GPT-4o-mini, files them into categorised subfolders, and provides a searchable web archive.

---

## How it works

```
Scanbot app → OneDrive drop zone
                     ↓  (Graph delta polling, every 30s)
              Ablage bot
                     ↓
              GPT-4o-mini analysis
              → structured filename  (e.g. Rechnung_Swisscard_Manuel_20260128.pdf)
              → document type        (invoice, insurance, tax, …)
                     ↓
              moved to archive folder (e.g. Rechnungen/2026/)
                     ↓
              indexed in SQLite DB
                     ↓
              searchable via web UI
```

---

## Features

- **Cloud-native** — uses Microsoft Graph delta queries; no local sync client required
- **AI classification** — GPT-4o-mini determines document type, date, sender, and generates a structured filename in one API call
- **Automatic filing** — files documents into type/year subfolders (`Rechnungen/2026/`, `Versicherung/2025/`, etc.)
- **Full-text search** — SQLite FTS5 index over filename, sender, keywords, and extracted text
- **Web archive UI** — browse, search, filter, and sort all documents; open PDFs directly
- **In-browser editing** — reclassify a document or rename it; the file is physically moved in OneDrive instantly via Graph
- **Mobile-friendly** — card layout on small screens, installable as a home screen app on iPhone
- **Fault-tolerant** — polling loop recovers from transient Graph errors with exponential backoff; duplicate filenames auto-suffixed

---

## Authentication modes

| Mode | When to use | What to set |
|---|---|---|
| **Delegated / Device code** | Personal OneDrive (Microsoft account) | Leave `CLIENT_SECRET` unset. On first run, check the logs for a login URL + code. Token is cached to disk and silently refreshed. |
| **App-only / Client credentials** | Work or school OneDrive with M365 licence | Set `CLIENT_SECRET` and `USER_ID`. No interactive login needed. |

---

## Quick start (Docker / Portainer)

### 1. Register a Microsoft Entra app

1. Go to [portal.azure.com](https://portal.azure.com) → **App registrations → New registration**
2. Note the **Application (client) ID** and **Directory (tenant) ID**
3. Under **API permissions**, add **Microsoft Graph → Files.ReadWrite** (delegated, for personal OneDrive) or **Files.ReadWrite.All** (application, for work OneDrive)
4. For personal OneDrive: leave client secret empty. For work OneDrive: create a client secret under **Certificates & secrets**

### 2. Find your drop-zone folder ID

The easiest way: open OneDrive in a browser, navigate to your drop-zone folder, and grab the `id` parameter from the URL. It looks like `15E1B4900D9A4063!s055cd26479014b18a902a226ec4e13a2`.

### 3. Deploy with Docker Compose

```yaml
version: "3.9"

services:
  ablage:
    image: ghcr.io/minenmaster/pdf-renamer:latest
    restart: unless-stopped
    environment:
      - OPENAI_API_KEY=sk-...
      - TENANT_ID=your-tenant-id
      - CLIENT_ID=your-client-id
      # Personal OneDrive: leave CLIENT_SECRET and USER_ID commented out.
      # Work OneDrive: uncomment both.
      # - CLIENT_SECRET=your-secret
      # - USER_ID=user@example.com
      - SOURCE_FOLDER_ID=your-folder-id
      - OUTPUT_BASE_FOLDER=Scanbot/Ablage   # archive root inside OneDrive
      - CLASSIFICATION_RULES_FILE=/app/classification_rules.yaml
      - POLL_INTERVAL_SECONDS=30
      - MAX_FILENAME_LENGTH=100
      - TOKEN_CACHE_PATH=/data/.token_cache.json
      - DB_PATH=/data/documents.db
    volumes:
      - doc_data:/data

  ablage-web:
    image: ghcr.io/minenmaster/pdf-renamer:latest
    restart: unless-stopped
    command: ["python", "web/app.py"]
    ports:
      - "5000:5000"
    environment:
      - DB_PATH=/data/documents.db
      - TENANT_ID=your-tenant-id
      - CLIENT_ID=your-client-id
      # - CLIENT_SECRET=your-secret
      # - USER_ID=user@example.com
      - TOKEN_CACHE_PATH=/data/.token_cache.json
      - OUTPUT_BASE_FOLDER=Scanbot/Ablage
      - CLASSIFICATION_RULES_FILE=/app/classification_rules.yaml
      # Optional: mount your local OneDrive folder to enable the "PDF öffnen" button
      # - ARCHIVE_ROOT=/archive
    volumes:
      - doc_data:/data
      # - /home/user/OneDrive/Scanbot/Ablage:/archive:ro

volumes:
  doc_data:
```

On first run with a personal account, check the logs for the device-code login prompt. After logging in once, the token is cached and refreshed automatically.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | ✓ | — | OpenAI API key |
| `TENANT_ID` | ✓ | — | Microsoft Entra tenant ID (use `consumers` for personal accounts) |
| `CLIENT_ID` | ✓ | — | App registration client ID |
| `CLIENT_SECRET` | — | — | Client secret (app-only/work OneDrive only) |
| `USER_ID` | — | — | User UPN or object ID (app-only mode only) |
| `SOURCE_FOLDER_ID` | ✓ | — | OneDrive driveItem ID of the drop-zone folder |
| `OUTPUT_BASE_FOLDER` | — | — | Archive root path inside OneDrive (e.g. `Scanbot/Ablage`) |
| `CLASSIFICATION_RULES_FILE` | — | `classification_rules.yaml` | Path to the classification rules YAML |
| `POLL_INTERVAL_SECONDS` | — | `30` | How often to check OneDrive for new files |
| `MAX_FILENAME_LENGTH` | — | `100` | Maximum generated filename length |
| `TOKEN_CACHE_PATH` | — | `/data/.token_cache.json` | Where to persist the MSAL token cache |
| `DB_PATH` | — | `/data/documents.db` | Path to the SQLite document index |
| `ARCHIVE_ROOT` | — | — | Local mount path of the archive (web UI only, enables PDF preview) |

---

## Filename convention

Generated filenames follow a structured pattern:

```
<Type>_<Company>_<Person>_<Details>_<YYYYMMDD>.pdf
```

| Part | Example |
|---|---|
| Type (German) | `Rechnung`, `Praemienrechnung`, `Lohnausweis`, `Kontoauszug` |
| Company | `Swisscard`, `Allianz`, `BKW`, `Visana` |
| Person | `Manuel`, `Judith` (known family members) |
| Details | Policy number, account number, subject |
| Date | `20260204` |

Examples:
```
Rechnung_Swisscard_Manuel_20260128.pdf
Praemienrechnung_Allianz_V955415027_Motorfahrzeug_20251001.pdf
Lohnausweis_Accenture_Judith_20241231.pdf
Steuerrechnung_Bern_2025_20250615.pdf
```

---

## Document types and folder mapping

| Type | Folder |
|---|---|
| `invoice` | `Rechnungen/` |
| `insurance` | `Versicherung/` |
| `tax` | `Steuer/` |
| `medical_report` | `Dokumente/Arzt/` |
| `bank_statement` | `Dokumente/Kontoauszüge/` |
| `contract` | `Dokumente/Verträge/` |
| `warranty` | `Dokumente/Garantienachweise/` |
| `id_document` | `Dokumente/Ausweise/` |
| `certificate` | `Dokumente/Zeugnisse/` |
| `letter` | `Dokumente/Briefe/` |
| `quote` | `Dokumente/Offerten/` |
| `other` | `Dokumente/Misc/` |

A year subfolder is always appended (e.g. `Rechnungen/2026/`). Folder mapping and display labels are configured in `classification_rules.yaml`.

---

## Web UI

The web UI runs as a separate container on port 5000.

**Search & filter** — full-text search across filename, sender, keywords, and extracted text; filter by type, year, and sender.

**Document detail** — view metadata, open the PDF, share it (iOS share sheet for banking apps), rename, or reclassify. Reclassification physically moves the file in OneDrive instantly.

**Mobile** — card layout on small screens, collapsible filters, sortable results. Installable as a home screen app on iPhone via Safari → Share → Add to Home Screen.

---

## Backfilling an existing archive

To index PDFs that were filed before the bot was set up:

```bash
python index_existing.py /path/to/local/archive \
  --onedrive-prefix Scanbot/Ablage \
  --db /path/to/documents.db
```

Use `--no-ai` to skip AI analysis and parse metadata from the structured filename instead (much faster).

---

## Project structure

```
ablage/
├── pdf_renamer_bot.py        # Main bot — orchestrates polling, AI, filing
├── folder_monitor.py         # Graph delta polling loop
├── graph_client.py           # Microsoft Graph API wrapper
├── ai_renamer.py             # GPT-4o-mini document analysis
├── pdf_extractor.py          # PDF text extraction
├── folder_classifier.py      # document_type → archive folder mapping
├── db.py                     # SQLite index + FTS5 search
├── index_existing.py         # Backfill script for existing archives
├── classification_rules.yaml # Folder mapping, labels, and badge colours
├── web/
│   ├── app.py                # Flask web UI
│   └── templates/            # Jinja2 templates
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
