# PDF Renamer Bot 🤖📄

An AI-powered bot that automatically detects new PDF files in your OneDrive folder and renames them intelligently based on their content using OpenAI's GPT-4o models.

## Features

- 🔍 **Automatic Detection**: Monitors your OneDrive folder for new PDF files
- 🧠 **AI-Powered Naming**: Uses OpenAI GPT-4o to generate descriptive filenames based on PDF content
- 📝 **Smart Text Extraction**: Extracts and analyzes text from PDF documents
- 🔄 **Real-time Monitoring**: Continuously watches for new files with debouncing to ensure files are fully written
- ⚙️ **Configurable**: Easy configuration through environment variables
- 🛡️ **Safe**: Handles duplicate names and sanitizes filenames for filesystem compatibility

## Prerequisites

- Python 3.8 or higher
- OpenAI API key
- OneDrive folder (or any local folder to monitor)

## Installation

1. Clone the repository:

```bash
git clone https://github.com/MinenMaster/pdf-renamer.git
cd pdf-renamer
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the setup wizard (recommended):

```bash
python setup.py
```

Or manually configure:

```bash
cp config.example.env .env
# Edit .env and add your configuration
```

4. (Optional) Try the demo:

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

1. Ask if you want to process existing PDF files in the folder
2. Start monitoring the folder for new PDFs
3. Automatically rename any new PDF files based on their content

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

1. **Detection**: The bot monitors your specified folder using filesystem events
2. **Extraction**: When a new PDF is detected, it extracts text content from the first 10 pages
3. **AI Analysis**: The extracted text is sent to OpenAI's GPT-4o-mini model to generate a descriptive filename
4. **Renaming**: The PDF is renamed with the AI-generated name, handling duplicates automatically

## Configuration Options

Edit your `.env` file to customize behavior:

| Variable               | Description                          | Default         |
| ---------------------- | ------------------------------------ | --------------- |
| `OPENAI_API_KEY`       | Your OpenAI API key (required)       | -               |
| `ONEDRIVE_FOLDER_PATH` | Path to folder to monitor (required) | -               |
| `AI_NAMING_PROMPT`     | Custom prompt for AI naming          | Built-in prompt |
| `MAX_FILENAME_LENGTH`  | Maximum filename length              | 100             |

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
- `watchdog` - Filesystem monitoring
- `python-dotenv` - Environment variable management
- `requests` - HTTP client

## Troubleshooting

**Issue**: "OpenAI API key not provided"

- **Solution**: Make sure you've created a `.env` file and added your `OPENAI_API_KEY`

**Issue**: "Folder does not exist"

- **Solution**: Verify the `ONEDRIVE_FOLDER_PATH` in your `.env` file points to a valid directory

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
