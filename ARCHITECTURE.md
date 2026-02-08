# PDF Renamer Bot - Architecture Overview

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PDF Renamer Bot                          │
│                  (pdf_renamer_bot.py)                       │
└──────────────┬─────────────┬──────────────┬─────────────────┘
               │             │              │
       ┌───────▼───────┐ ┌──▼────────┐ ┌───▼──────────────┐
       │ Folder Monitor│ │   PDF     │ │   AI Renamer     │
       │               │ │ Extractor │ │                  │
       │ (watchdog)    │ │ (PyPDF2)  │ │ (OpenAI GPT)     │
       └───────┬───────┘ └──┬────────┘ └───┬──────────────┘
               │            │               │
       ┌───────▼────────────▼───────────────▼──────────────┐
       │           OneDrive / Local Folder                 │
       │              (*.pdf files)                        │
       └───────────────────────────────────────────────────┘
```

## Workflow

1. **Monitor** → Folder Monitor detects new PDF file
2. **Extract** → PDF Extractor reads text content (first 10 pages)
3. **Analyze** → AI Renamer sends content to OpenAI GPT
4. **Generate** → GPT generates intelligent filename
5. **Rename** → Bot renames the file with sanitized name

## Component Details

### Folder Monitor (folder_monitor.py)
- Uses `watchdog` library for filesystem events
- Implements debouncing (2s default) to ensure files are fully written
- Monitors for `.pdf` file creation and modification events
- Can scan existing files on startup

### PDF Extractor (pdf_extractor.py)
- Extracts text using `PyPDF2`
- Configurable page limit (default: 10 pages)
- Returns metadata (page count, document info)
- Handles extraction errors gracefully

### AI Renamer (ai_renamer.py)
- Integrates with OpenAI API (gpt-3.5-turbo)
- Truncates content to 3000 chars to manage API costs
- Sanitizes filenames for filesystem compatibility
- Enforces length limits (default: 100 chars)
- Handles duplicate filenames with numeric suffixes

### Main Bot (pdf_renamer_bot.py)
- Orchestrates all components
- Manages configuration via .env file
- Provides user-friendly console output
- Handles errors and edge cases

## Configuration

All configuration is managed through environment variables in `.env`:

```
OPENAI_API_KEY           → Required: Your OpenAI API key
ONEDRIVE_FOLDER_PATH     → Required: Folder to monitor
MAX_FILENAME_LENGTH      → Optional: Max filename chars (default: 100)
AI_NAMING_PROMPT         → Optional: Custom AI prompt
```

## Example Transformations

| Original                | AI-Generated                                |
|------------------------|---------------------------------------------|
| scan_001.pdf           | Monthly_Sales_Report_Q4_2023.pdf           |
| document.pdf           | Project_Proposal_Website_Redesign.pdf      |
| file_20240101.pdf      | Invoice_Acme_Corp_INV-2024-001.pdf         |
| untitled.pdf           | Meeting_Minutes_Board_Meeting_Jan_2024.pdf |

## Security Features

- API keys stored in .env (never committed)
- Filename sanitization prevents path traversal
- Input validation on all user inputs
- No code execution from PDF content
- Read-only access to PDF files during extraction

## Error Handling

- Network failures → Graceful retry or skip
- PDF extraction errors → Skip file, log error
- Duplicate filenames → Add numeric suffix
- Invalid API key → Fail fast with clear message
- Missing folder → Create or fail with guidance

## Performance Considerations

- Only first 10 pages extracted (configurable)
- Content truncated to 3000 chars for API
- Debouncing prevents multiple processing attempts
- Efficient filesystem watching (event-based, not polling)
- Minimal memory footprint

## Future Enhancements

Possible improvements:
- OCR support for scanned PDFs (pytesseract)
- Batch processing mode
- Custom naming rules/templates
- Multiple folder monitoring
- Web interface
- Database for rename history
- Undo functionality
- Cloud storage integration (Dropbox, Google Drive)
