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

    # Microsoft Graph (client credentials)
    print("\n2. Microsoft Entra / Graph app")
    tenant_id = input("   Tenant ID: ").strip()
    client_id = input("   Client ID: ").strip()
    client_secret = input("   Client Secret: ").strip()

    if not all([tenant_id, client_id, client_secret]):
        print("❌ Tenant ID, Client ID, and Client Secret are required!")
        sys.exit(1)

    # Source folder ID
    print("\n3. OneDrive Drop Zone")
    print("   Provide the driveItem ID of the folder to watch (SOURCE_FOLDER_ID).")
    print("   Tip: Use Graph Explorer → GET /me/drive/root/children to find it.")
    source_folder_id = input("   Source folder ID: ").strip()
    if not source_folder_id:
        print("❌ SOURCE_FOLDER_ID is required!")
        sys.exit(1)

    # Output / classification settings
    print("\n4. Folder Classification (optional — press Enter to skip)")
    print("   If configured, renamed PDFs will be moved to an organized archive inside OneDrive.")
    print("   Example: Archive")

    output_base = input("   Enter archive root path inside OneDrive (or leave blank): ").strip()

    # Optional settings
    print("\n5. Optional Settings (press Enter to use defaults)")

    poll_interval = input("   Graph poll interval seconds [30]: ").strip() or "30"
    max_length = input("   Max filename length [100]: ").strip() or "100"

    classification_block = ""
    if output_base:
        classification_block = f"""
# Archive root folder path inside OneDrive
OUTPUT_BASE_FOLDER={output_base}

# Path to classification rules YAML (relative to project folder or absolute)
CLASSIFICATION_RULES_FILE=classification_rules.yaml
"""

    env_content = f"""# OpenAI API Configuration
OPENAI_API_KEY={api_key}

# Microsoft Graph (Client Credentials)
TENANT_ID={tenant_id}
CLIENT_ID={client_id}
CLIENT_SECRET={client_secret}

# OneDrive drop-zone folder to watch (drive item ID)
SOURCE_FOLDER_ID={source_folder_id}
{classification_block}
# Polling interval (seconds)
POLL_INTERVAL_SECONDS={poll_interval}

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
    python bot.py

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
