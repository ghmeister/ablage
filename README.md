# PDF Renamer Bot 🤖📄

An AI-powered bot that automatically detects new PDF files in your OneDrive folder and renames them intelligently based on their content using OpenAI's GPT.

## Features

- 🔍 **Automatic Detection**: Monitors your OneDrive folder for new PDF files
- 🧠 **AI-Powered Naming**: Uses OpenAI GPT to generate descriptive filenames based on PDF content
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

3. Configure the bot:
```bash
cp config.example.env .env
```

4. Edit `.env` and add your configuration:
```env
OPENAI_API_KEY=your_openai_api_key_here
ONEDRIVE_FOLDER_PATH=/path/to/your/OneDrive/PDFs
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

## How It Works

1. **Detection**: The bot monitors your specified folder using filesystem events
2. **Extraction**: When a new PDF is detected, it extracts text content from the first 10 pages
3. **AI Analysis**: The extracted text is sent to OpenAI's GPT model to generate a descriptive filename
4. **Renaming**: The PDF is renamed with the AI-generated name, handling duplicates automatically

## Configuration Options

Edit your `.env` file to customize behavior:

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | Your OpenAI API key (required) | - |
| `ONEDRIVE_FOLDER_PATH` | Path to folder to monitor (required) | - |
| `AI_NAMING_PROMPT` | Custom prompt for AI naming | Built-in prompt |
| `MAX_FILENAME_LENGTH` | Maximum filename length | 100 |

## Example

**Before**: `document_scan_001.pdf`

**After**: `Invoice_Microsoft_Azure_December_2023.pdf`

The bot analyzes the PDF content and generates a meaningful, descriptive filename.

## Project Structure

```
pdf-renamer/
├── pdf_renamer_bot.py    # Main bot orchestrator
├── pdf_extractor.py      # PDF text extraction module
├── ai_renamer.py         # AI-powered naming module
├── folder_monitor.py     # OneDrive folder monitoring module
├── requirements.txt      # Python dependencies
├── config.example.env    # Example configuration
├── .gitignore           # Git ignore rules
└── README.md            # This file
```

## Dependencies

- `pypdf2` - PDF text extraction
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

- OpenAI for the GPT API
- PyPDF2 for PDF processing
- Watchdog for filesystem monitoring