#!/usr/bin/env python3
"""
PDF Renamer Bot
Main orchestrator that combines PDF extraction, AI naming, folder classification,
and folder monitoring.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from pdf_extractor import PDFExtractor
from ai_renamer import AIRenamer
from folder_monitor import OneDriveFolderMonitor
from folder_classifier import FolderClassifier


class PDFRenamerBot:
    """Main bot that orchestrates PDF detection, renaming, and filing."""

    def __init__(self):
        """Initialize the PDF renamer bot."""
        load_dotenv()

        self.pdf_extractor = PDFExtractor(max_pages=10)

        try:
            self.ai_renamer = AIRenamer()
        except ValueError as e:
            print(f"Error initializing AI renamer: {e}")
            print("Please set your ANTHROPIC_API_KEY in a .env file")
            sys.exit(1)

        self.folder_path = os.getenv('ONEDRIVE_FOLDER_PATH')
        if not self.folder_path:
            print("Error: ONEDRIVE_FOLDER_PATH not set in .env file")
            print("Please copy config.example.env to .env and configure it")
            sys.exit(1)

        try:
            self.monitor = OneDriveFolderMonitor(self.folder_path, self.process_pdf)
        except ValueError as e:
            print(f"Error initializing folder monitor: {e}")
            sys.exit(1)

        # Optional: folder classifier (requires OUTPUT_BASE_FOLDER in .env)
        self.classifier = None
        output_base = os.getenv('OUTPUT_BASE_FOLDER')
        if output_base:
            rules_file = os.getenv('CLASSIFICATION_RULES_FILE', 'classification_rules.yaml')
            try:
                self.classifier = FolderClassifier(rules_file, output_base)
                print(f"Folder classifier enabled → output base: {output_base}")
            except Exception as e:
                print(f"Warning: Could not initialize folder classifier: {e}")
                print("Files will be renamed in-place without moving.")

    def process_pdf(self, pdf_path: str):
        """
        Process a PDF file: extract text, analyze with AI, then rename and move.

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
            print("Failed to extract text from PDF. Skipping.")
            return

        print(f"Extracted {len(pdf_content)} characters from PDF")

        pdf_info = self.pdf_extractor.get_pdf_info(pdf_path)
        print(f"PDF has {pdf_info.get('num_pages', 'unknown')} pages")

        # Analyze document with AI (filename + metadata in one call)
        print("\nAnalyzing document with AI...")
        metadata = self.ai_renamer.analyze_document(pdf_content, Path(pdf_path).stem)

        new_filename = metadata["filename"]
        doc_type = metadata["document_type"]
        doc_date = metadata["date"]
        company = metadata["company"]

        print(f"Suggested filename : {new_filename}.pdf")
        print(f"Document type      : {doc_type}")
        print(f"Date               : {doc_date or 'not detected'}")
        print(f"Company/sender     : {company or 'not detected'}")

        # Move (and rename) to classified destination, or fall back to in-place rename
        if self.classifier:
            try:
                final_path = self.classifier.move_file(Path(pdf_path), new_filename, metadata)
                folder, matched_rule = self.classifier.classify(metadata)
                year = self.classifier._get_year(metadata)
                print(f"\nFiled to : {folder}/{year}/{final_path.name}")
                print(f"Rule     : {matched_rule}")
            except Exception as e:
                print(f"Error moving file: {e}. Falling back to in-place rename.")
                self._rename_in_place(pdf_path, new_filename)
        else:
            self._rename_in_place(pdf_path, new_filename)

    def _rename_in_place(self, pdf_path: str, new_filename: str):
        """Rename a file within its current directory."""
        new_path = Path(pdf_path).parent / f"{new_filename}.pdf"

        if new_path.exists() and new_path != Path(pdf_path):
            print(f"File already exists: {new_filename}.pdf")
            counter = 1
            while new_path.exists():
                new_path = Path(pdf_path).parent / f"{new_filename}_{counter}.pdf"
                counter += 1
            print(f"Using: {new_path.name}")

        try:
            if new_path != Path(pdf_path):
                os.rename(pdf_path, new_path)
                print(f"Renamed to: {new_path.name}")
            else:
                print("Generated name is same as original. No rename needed.")
        except Exception as e:
            print(f"Error renaming file: {e}")

    def run(self):
        """Run the bot."""
        print("""
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551              PDF Renamer Bot                               \u2551
\u2551  AI-powered automatic PDF renaming and filing              \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d
""")

        print(f"Monitoring folder: {self.folder_path}\n")

        self.monitor.scan_existing_files()
        print()
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
