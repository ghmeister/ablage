#!/usr/bin/env python3
"""
Setup helper script for PDF Renamer Bot.
Guides users through initial configuration.
"""
import os
import sys
from pathlib import Path


def main():
    """Run setup wizard."""
    print("""
╔════════════════════════════════════════════════════════════╗
║         PDF Renamer Bot - Setup Wizard                     ║
╚════════════════════════════════════════════════════════════╝
""")
    
    # Check if .env already exists
    env_file = Path(".env")
    if env_file.exists():
        print("⚠️  .env file already exists!")
        response = input("Do you want to overwrite it? (y/n): ").strip().lower()
        if response != 'y':
            print("Setup cancelled.")
            return
    
    print("\nLet's configure your PDF Renamer Bot.\n")
    
    # Get OpenAI API key
    print("1. OpenAI API Key")
    print("   Get your API key from: https://platform.openai.com/api-keys")
    api_key = input("   Enter your OpenAI API key: ").strip()
    
    if not api_key:
        print("❌ API key is required!")
        sys.exit(1)
    
    # Get folder path
    print("\n2. OneDrive Folder Path")
    print("   This is the folder where the bot will monitor for new PDFs.")
    print("   Example: /Users/yourname/OneDrive/PDFs")
    print("            C:\\Users\\yourname\\OneDrive\\PDFs")
    
    folder_path = input("   Enter folder path: ").strip()
    
    if not folder_path:
        print("❌ Folder path is required!")
        sys.exit(1)
    
    # Validate folder exists
    if not Path(folder_path).exists():
        print(f"\n⚠️  Warning: Folder does not exist: {folder_path}")
        response = input("   Do you want to create it? (y/n): ").strip().lower()
        if response == 'y':
            try:
                Path(folder_path).mkdir(parents=True, exist_ok=True)
                print(f"✓ Created folder: {folder_path}")
            except Exception as e:
                print(f"❌ Failed to create folder: {e}")
                sys.exit(1)
        else:
            print("Please create the folder manually and run setup again.")
            sys.exit(1)
    
    # Output / classification settings
    print("\n3. Folder Classification (optional — press Enter to skip)")
    print("   If configured, renamed PDFs will be moved to an organized archive.")
    print("   Example: C:\\Users\\yourname\\OneDrive\\Scanbot\\Ablage")

    output_base = input("   Enter output base folder path (or leave blank to skip): ").strip()

    monitor_recursive = "false"
    if output_base:
        if not Path(output_base).exists():
            print(f"\n   Warning: Output folder does not exist: {output_base}")
            response = input("   Do you want to create it? (y/n): ").strip().lower()
            if response == 'y':
                try:
                    Path(output_base).mkdir(parents=True, exist_ok=True)
                    print(f"   Created folder: {output_base}")
                except Exception as e:
                    print(f"   Failed to create folder: {e}. Skipping.")
                    output_base = ""

        if output_base:
            rec = input("   Watch subfolders of the monitored path too? (y/n) [y]: ").strip().lower()
            monitor_recursive = "false" if rec == "n" else "true"

    # Optional settings
    print("\n4. Optional Settings (press Enter to use defaults)")

    max_length = input("   Max filename length [100]: ").strip() or "100"

    # Build .env content
    classification_block = ""
    if output_base:
        classification_block = f"""
# Output archive folder (where classified PDFs are moved to)
OUTPUT_BASE_FOLDER={output_base}

# Path to classification rules YAML (relative to project folder or absolute)
CLASSIFICATION_RULES_FILE=classification_rules.yaml

# Watch subdirectories of ONEDRIVE_FOLDER_PATH as well
MONITOR_RECURSIVE={monitor_recursive}
"""

    env_content = f"""# OpenAI API Configuration
OPENAI_API_KEY={api_key}

# OneDrive Folder Path to Monitor (drop zone for new PDFs)
ONEDRIVE_FOLDER_PATH={folder_path}
{classification_block}
# Optional: Maximum filename length
MAX_FILENAME_LENGTH={max_length}

# Optional: Custom prompt for AI naming (uncomment to use)
# AI_NAMING_PROMPT=Based on the content of this PDF, suggest a concise and descriptive filename (without extension)
"""
    
    try:
        with open(".env", "w") as f:
            f.write(env_content)
        print("\n✅ Configuration saved to .env")
    except Exception as e:
        print(f"\n❌ Failed to write .env file: {e}")
        sys.exit(1)
    
    # Final instructions
    print("""
╔════════════════════════════════════════════════════════════╗
║                   Setup Complete! ✨                        ║
╚════════════════════════════════════════════════════════════╝

Your PDF Renamer Bot is now configured and ready to use!

To start the bot:
    python pdf_renamer_bot.py

The bot will:
1. Monitor your folder for new PDFs
2. Extract text from each PDF
3. Use AI to generate a descriptive filename and classify the document
4. Move the PDF to the correct archive subfolder (e.g. Rechnungen/2026/)

Press Ctrl+C to stop the bot at any time.

Happy renaming! 🎉
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        sys.exit(0)
