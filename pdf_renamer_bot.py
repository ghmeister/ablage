#!/usr/bin/env python3
"""
PDF Renamer Bot
Main orchestrator that combines PDF extraction, AI naming, and folder monitoring.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from pdf_extractor import PDFExtractor
from ai_renamer import AIRenamer
from folder_monitor import OneDriveFolderMonitor


class PDFRenamerBot:
    """Main bot that orchestrates PDF detection and renaming."""
    
    def __init__(self):
        """Initialize the PDF renamer bot."""
        # Load environment variables
        load_dotenv()
        
        # Initialize components
        self.pdf_extractor = PDFExtractor(max_pages=10)
        
        try:
            self.ai_renamer = AIRenamer()
        except ValueError as e:
            print(f"Error initializing AI renamer: {e}")
            print("Please set your OPENAI_API_KEY in a .env file")
            sys.exit(1)
        
        # Get folder path from environment
        self.folder_path = os.getenv('ONEDRIVE_FOLDER_PATH')
        if not self.folder_path:
            print("Error: ONEDRIVE_FOLDER_PATH not set in .env file")
            print("Please copy config.example.env to .env and configure it")
            sys.exit(1)
        
        # Initialize folder monitor
        try:
            self.monitor = OneDriveFolderMonitor(self.folder_path, self.process_pdf)
        except ValueError as e:
            print(f"Error initializing folder monitor: {e}")
            sys.exit(1)
    
    def process_pdf(self, pdf_path: str):
        """
        Process a PDF file: extract text, generate new name, and rename.
        
        Args:
            pdf_path: Path to the PDF file
        """
        print(f"\n{'='*60}")
        print(f"Processing: {Path(pdf_path).name}")
        print(f"{'='*60}")
        
        # Extract text from PDF
        print("Extracting text from PDF...")
        pdf_content = self.pdf_extractor.extract_text(pdf_path)
        
        if not pdf_content:
            print("❌ Failed to extract text from PDF. Skipping rename.")
            return
        
        print(f"✓ Extracted {len(pdf_content)} characters from PDF")
        
        # Get PDF info
        pdf_info = self.pdf_extractor.get_pdf_info(pdf_path)
        print(f"✓ PDF has {pdf_info.get('num_pages', 'unknown')} pages")
        
        # Generate new filename using AI
        print("\nGenerating intelligent filename using AI...")
        original_filename = Path(pdf_path).stem
        new_filename = self.ai_renamer.generate_filename(pdf_content, original_filename)
        
        if not new_filename:
            print("❌ Failed to generate new filename. Keeping original name.")
            return
        
        print(f"✓ Generated filename: {new_filename}.pdf")
        
        # Rename the file
        new_path = Path(pdf_path).parent / f"{new_filename}.pdf"
        
        # Check if file already exists
        if new_path.exists() and new_path != Path(pdf_path):
            print(f"⚠️  File already exists: {new_filename}.pdf")
            # Add number suffix
            counter = 1
            while new_path.exists():
                new_path = Path(pdf_path).parent / f"{new_filename}_{counter}.pdf"
                counter += 1
            print(f"   Using: {new_path.name}")
        
        # Perform rename
        try:
            if new_path != Path(pdf_path):
                os.rename(pdf_path, new_path)
                print(f"✓ Successfully renamed to: {new_path.name}")
            else:
                print("ℹ️  Generated name is same as original. No rename needed.")
        except Exception as e:
            print(f"❌ Error renaming file: {e}")
    
    def run(self):
        """Run the bot."""
        print("""
╔════════════════════════════════════════════════════════════╗
║              PDF Renamer Bot                               ║
║  AI-powered automatic PDF renaming for OneDrive            ║
╚════════════════════════════════════════════════════════════╝
""")
        
        print(f"Monitoring folder: {self.folder_path}\n")
        
        # Scan existing files first
        self.monitor.scan_existing_files()
        
        print()
        
        # Start monitoring
        self.monitor.start()


def main():
    """Main entry point."""
    try:
        bot = PDFRenamerBot()
        bot.run()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
