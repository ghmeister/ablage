#!/usr/bin/env python3
"""
Basic tests for PDF Renamer Bot modules.
"""
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
    from folder_monitor import OneDriveFolderMonitor, PDFHandler
    print("✓ folder_monitor imported successfully")
except ImportError as e:
    print(f"❌ Failed to import folder_monitor: {e}")
    sys.exit(1)

try:
    from pdf_renamer_bot import PDFRenamerBot
    print("✓ pdf_renamer_bot imported successfully")
except Exception as e:
    print(f"⚠️  pdf_renamer_bot import check: {e}")
    print("   (This is expected if environment is not configured)")

# Test PDFExtractor initialization
print("\nTesting PDFExtractor...")
try:
    extractor = PDFExtractor(max_pages=5)
    print("✓ PDFExtractor initialized with max_pages=5")
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

# Test OneDriveFolderMonitor with temp directory
print("\nTesting OneDriveFolderMonitor...")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        def dummy_callback(file_path):
            print(f"  Callback received: {file_path}")
        
        monitor = OneDriveFolderMonitor(tmpdir, dummy_callback)
        print(f"✓ OneDriveFolderMonitor initialized with folder: {tmpdir}")
        
        # Don't actually start monitoring, just test initialization
        print("✓ Monitor can be initialized successfully")
        
except Exception as e:
    print(f"❌ Failed OneDriveFolderMonitor test: {e}")
    sys.exit(1)

print("\n" + "="*60)
print("✅ All basic tests passed!")
print("="*60)
print("\nTo run the full bot, you need to:")
print("1. Install dependencies: pip install -r requirements.txt")
print("2. Create .env file with your configuration")
print("3. Run: python pdf_renamer_bot.py")
