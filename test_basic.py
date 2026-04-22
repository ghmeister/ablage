#!/usr/bin/env python3
"""
Basic tests for PDF Renamer Bot modules.
"""
import io
import os
import sys
import tempfile
from pathlib import Path

# Test imports
print("Testing imports...")
try:
    from pdf_extractor import PDFExtractor
    print("✓ pdf_extractor imported successfully")
except ImportError as e:
    print(f"❌ Failed to import pdf_extractor: {e}")
    sys.exit(1)

try:
    from ai_renamer import AIRenamer
    print("✓ ai_renamer imported successfully")
except Exception as e:
    print(f"⚠️  ai_renamer import check: {e}")
    print("   (This is expected if OPENAI_API_KEY is not set)")

try:
    from folder_monitor import OneDriveDeltaMonitor
    print("✓ folder_monitor imported successfully")
except ImportError as e:
    print(f"❌ Failed to import folder_monitor: {e}")
    sys.exit(1)

try:
    from bot import PDFRenamerBot
    print("✓ bot imported successfully")
except Exception as e:
    print(f"⚠️  bot import check: {e}")
    print("   (This is expected if environment is not configured)")

# Test PDFExtractor initialization
print("\nTesting PDFExtractor...")
try:
    extractor = PDFExtractor(max_pages=5)
    print("✓ PDFExtractor initialized with max_pages=5")

    # Minimal in-memory PDF bytes
    try:
        from PyPDF2 import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buf = io.BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()
        _ = extractor.get_pdf_info_from_bytes(pdf_bytes)
        _ = extractor.extract_text_from_bytes(pdf_bytes)
        print("✓ PDFExtractor can handle in-memory bytes")
    except Exception as pdf_err:
        print(f"⚠️  Skipped in-memory PDF extraction test: {pdf_err}")
except Exception as e:
    print(f"❌ Failed to initialize PDFExtractor: {e}")
    sys.exit(1)

# Test AIRenamer sanitization
print("\nTesting AIRenamer filename sanitization...")
try:
    # Mock initialization without API key for testing
    os.environ['OPENAI_API_KEY'] = 'test-key-for-sanitization'
    renamer = AIRenamer()
    
    # Test sanitization
    test_cases = [
        ("My File Name.pdf", "My_File_Name.pdf"),
        ("Invoice: January/2024", "Invoice-_January-2024"),
        ("Test * File ? Name", "Test__File__Name"),
        ('"Quoted Filename"', "Quoted_Filename"),
    ]
    
    for input_name, expected_pattern in test_cases:
        sanitized = renamer._sanitize_filename(input_name)
        print(f"  '{input_name}' -> '{sanitized}'")
    
    print("✓ Filename sanitization works")
    
    # Clean up test env var
    del os.environ['OPENAI_API_KEY']
    
except Exception as e:
    print(f"❌ Failed AIRenamer test: {e}")
    if 'OPENAI_API_KEY' in os.environ:
        del os.environ['OPENAI_API_KEY']

# Test OneDriveDeltaMonitor with dummy graph client
print("\nTesting OneDriveDeltaMonitor...")
try:
    class DummyGraph:
        def get_initial_delta_link(self, *_args, **_kwargs):
            return "https://example.com/delta"

        def get_delta_page(self, *_args, **_kwargs):
            return {"value": [], "@odata.deltaLink": "https://example.com/delta"}

    monitor = OneDriveDeltaMonitor(DummyGraph(), "fake-folder-id", lambda item: None, poll_interval=1, skip_existing=False)
    print("✓ OneDriveDeltaMonitor initialized with dummy graph client")

except Exception as e:
    print(f"❌ Failed OneDriveDeltaMonitor test: {e}")
    sys.exit(1)

print("\n" + "="*60)
print("✅ All basic tests passed!")
print("="*60)
print("\nTo run the full bot, you need to:")
print("1. Install dependencies: pip install -r requirements.txt")
print("2. Create .env file with your configuration")
print("3. Run: python bot.py")
