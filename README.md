# PDF Renamer Bot 🤖📄

An AI-powered bot that watches a OneDrive drop zone via Microsoft Graph delta queries and renames PDFs intelligently based on their content using OpenAI's GPT-4o models.

## Features

- 🔍 **Automatic Detection**: Polls the OneDrive folder with Graph `/delta` to detect new PDFs
- 🧠 **AI-Powered Naming**: Uses OpenAI GPT-4o to generate descriptive filenames based on PDF content
- 📝 **Smart Text Extraction**: Extracts and analyzes text from PDF documents
- 🔄 **Cloud-native**: No local sync client needed; downloads and processes files in-memory
- ⚙️ **Configurable**: Easy configuration through environment variables
- 🛡️ **Safe**: Handles duplicate names and sanitizes filenames for filesystem compatibility

## Prerequisites

- Python 3.8 or higher
- OpenAI API key
- Microsoft Entra app registration with Graph permissions (Files.ReadWrite.All for application permissions)
- OneDrive folder to act as the drop zone (folder driveItem ID)

## Installation

1. Clone the repository:

```bash
git clone https://github.com/MinenMaster/pdf-renamer.git
cd pdf-renamer
```

1. Install dependencies:

```bash
pip install -r requirements.txt
```

1. Run the setup wizard (recommended):

```bash
python setup.py
```

Or manually configure:

```bash
cp config.example.env .env
# Edit .env and add your configuration
```

1. (Optional) Try the demo:

```bash
pip install reportlab  # Only needed for demo
python demo.py
```

## Usage

Run the bot:

```bash
python pdf_renamer_bot.py
```

The bot will:

1. Start a Graph `/delta` subscription against your drop-zone folder
2. Poll for new/changed PDF files
3. Download each PDF in-memory, analyze it, rename it, and (optionally) move it into an archive path according to your classification rules

To stop the bot, press `Ctrl+C`.

## Run with Docker

Image (GHCR): `ghcr.io/minenmaster/pdf-renamer:latest`

1. Start with Docker Compose:

```bash
docker compose up -d
```

1. Verify logs:

```bash
docker compose logs -f
```

### Portainer (Stacks) deployment

1. Create host folders that will hold your PDFs and output (adjust paths as needed):
    - `./data/input` → bind-mounted to `/data/input` (ONEDRIVE_FOLDER_PATH)
    - `./data/output` → bind-mounted to `/data/output` (OUTPUT_BASE_FOLDER)

2. In Portainer: **Stacks → Add stack → Web editor**
    - Paste the contents of `docker-compose.yml` from this repo
    - In the Environment section, add `OPENAI_API_KEY` (as a secret or plain env var). You can also set `ONEDRIVE_FOLDER_PATH`, `OUTPUT_BASE_FOLDER`, `CLASSIFICATION_RULES_FILE`, `MAX_FILENAME_LENGTH` here.
    - Confirm the volume bind paths match your host folders
    - Deploy the stack

3. After deploy, open the container logs in Portainer to confirm it’s watching the folder.

Notes:

- If you want custom classification rules, mount your `classification_rules.yaml` into `/app/classification_rules.yaml` (see the commented line in `docker-compose.yml`).
- On Windows with Docker Desktop, use paths accessible to the Docker VM (e.g., `C:/Users/you/data/input`).
- The container runs `python pdf_renamer_bot.py` by default; override the command if you need a different entrypoint.

## How It Works

1. **Detection**: The bot polls Microsoft Graph `/delta` for the drop-zone folder
2. **Extraction**: Each new PDF is downloaded to memory and the first N pages are extracted
3. **AI Analysis**: The extracted text is sent to OpenAI's GPT-4o-mini model to generate a descriptive filename and metadata
4. **Renaming/Move**: The file is renamed (and optionally moved) via Graph `PATCH` into your archive hierarchy

## Configuration Options

Edit your `.env` file to customize behavior:

| Variable                    | Description                               | Default                   |
| --------------------------- | ----------------------------------------- | ------------------------- |
| `OPENAI_API_KEY`            | Your OpenAI API key (required)            | -                         |
| `TENANT_ID`                 | Microsoft Entra tenant ID (required)      | -                         |
| `CLIENT_ID`                 | App registration client ID (required)     | -                         |
| `CLIENT_SECRET`             | App registration client secret (required) | -                         |
| `SOURCE_FOLDER_ID`          | OneDrive drop-zone folder driveItem ID    | -                         |
| `OUTPUT_BASE_FOLDER`        | Archive root path in OneDrive (optional)  | -                         |
| `CLASSIFICATION_RULES_FILE` | Path to YAML rules (optional)             | classification_rules.yaml |
| `POLL_INTERVAL_SECONDS`     | Graph delta poll interval                 | 30                        |
| `AI_NAMING_PROMPT`          | Custom prompt for AI naming               | Built-in prompt           |
| `MAX_FILENAME_LENGTH`       | Maximum filename length                   | 100                       |

## Example

**Before**: `document_scan_001.pdf`

**After**: `Invoice_Microsoft_Azure_December_2023.pdf`

The bot analyzes the PDF content and generates a meaningful, descriptive filename.

## Project Structure

```text
pdf-renamer/
├── pdf_renamer_bot.py    # Main bot orchestrator
├── pdf_extractor.py      # PDF text extraction module
├── ai_renamer.py         # AI-powered naming module
├── folder_monitor.py     # OneDrive folder monitoring module
├── setup.py              # Interactive setup wizard
├── demo.py               # Demo script with sample PDFs
├── test_basic.py         # Basic functionality tests
├── requirements.txt      # Python dependencies
├── config.example.env    # Example configuration
├── .gitignore            # Git ignore rules
└── README.md             # This file
```

## Dependencies

- `PyPDF2` - PDF text extraction
- `openai` - OpenAI API client
- `msal` - Microsoft Authentication Library for token acquisition
- `python-dotenv` - Environment variable management
- `requests` - HTTP client

## Troubleshooting

**Issue**: "OpenAI API key not provided"

- **Solution**: Make sure you've created a `.env` file and added your `OPENAI_API_KEY`

**Issue**: "Missing required environment variables"

- **Solution**: Ensure `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, and `SOURCE_FOLDER_ID` are set in `.env`

**Issue**: "Failed to extract text from PDF"

- **Solution**: Some PDFs may be scanned images without text. Consider adding OCR support for such files.

## Security Notes

- Never commit your `.env` file with API keys
- Keep your OpenAI API key secure
- Monitor your OpenAI API usage to avoid unexpected charges

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - feel free to use this project for personal or commercial purposes.

## Acknowledgments

- OpenAI for the GPT-4o API
- PyPDF2 for PDF processing
- Watchdog for filesystem monitoring
