#!/usr/bin/env python3
"""
Demo script to show PDF Renamer Bot functionality.
Creates a sample PDF and demonstrates the renaming process.
"""
import os
import sys
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def create_sample_pdf(output_path: str, content: str, title: str = "Sample Document"):
    """
    Create a simple PDF with text content for testing.
    
    Args:
        output_path: Where to save the PDF
        content: Text content to include
        title: Document title
    """
    try:
        c = canvas.Canvas(output_path, pagesize=letter)
        c.setTitle(title)
        
        # Add title
        c.setFont("Helvetica-Bold", 16)
        c.drawString(100, 750, title)
        
        # Add content
        c.setFont("Helvetica", 12)
        y_position = 700
        
        # Split content into lines
        lines = content.split('\n')
        for line in lines:
            if y_position < 50:  # New page if needed
                c.showPage()
                c.setFont("Helvetica", 12)
                y_position = 750
            c.drawString(100, y_position, line[:80])  # Max 80 chars per line
            y_position -= 20
        
        c.save()
        print(f"✓ Created sample PDF: {output_path}")
        return True
    except ImportError:
        print("⚠️  reportlab not installed. Skipping PDF creation.")
        print("   Install with: pip install reportlab")
        return False
    except Exception as e:
        print(f"❌ Failed to create PDF: {e}")
        return False


def main():
    """Run demo."""
    print("""
╔════════════════════════════════════════════════════════════╗
║         PDF Renamer Bot - Demo                             ║
╚════════════════════════════════════════════════════════════╝
""")
    
    # Create test directory
    test_dir = Path("/tmp/pdf_renamer_demo")
    test_dir.mkdir(exist_ok=True)
    print(f"Demo directory: {test_dir}\n")
    
    # Sample documents to create
    samples = [
        {
            "filename": "document_001.pdf",
            "title": "Monthly Sales Report",
            "content": """Monthly Sales Report - December 2023
            
Company: TechCorp Industries
Department: Sales

Executive Summary:
Total Revenue: $1,250,000
New Customers: 45
Customer Retention: 94%

This report summarizes the sales performance for December 2023.
We exceeded our quarterly targets by 15% and secured several
major enterprise clients."""
        },
        {
            "filename": "scan_002.pdf",
            "title": "Meeting Minutes",
            "content": """Meeting Minutes - Product Strategy Session

Date: January 15, 2024
Attendees: Product Team, Engineering, Marketing

Agenda:
1. Q1 Product Roadmap Review
2. New Feature Prioritization  
3. Customer Feedback Discussion

Key Decisions:
- Launch mobile app in Q2
- Implement AI-powered recommendations
- Increase focus on user experience improvements"""
        },
        {
            "filename": "file_003.pdf",
            "title": "Invoice",
            "content": """INVOICE

Invoice Number: INV-2024-0234
Date: February 1, 2024

Bill To:
Acme Corporation
123 Business St
New York, NY 10001

Services Rendered:
- Web Development Services: $5,000
- Cloud Hosting (Annual): $1,200
- Support & Maintenance: $800

Total Amount Due: $7,000
Payment Terms: Net 30"""
        }
    ]
    
    # Create sample PDFs
    print("Creating sample PDF files...\n")
    created_files = []
    
    for sample in samples:
        file_path = test_dir / sample["filename"]
        if create_sample_pdf(str(file_path), sample["content"], sample["title"]):
            created_files.append(file_path)
    
    if not created_files:
        print("\n❌ No PDFs were created. Please install reportlab:")
        print("   pip install reportlab")
        sys.exit(1)
    
    print(f"\n✓ Created {len(created_files)} sample PDFs")
    
    # Show what would happen
    print("\n" + "="*60)
    print("DEMO: What the bot would do")
    print("="*60)
    
    print("\nThe PDF Renamer Bot would:")
    print("1. Monitor the folder for new PDF files")
    print("2. Extract text from each PDF")
    print("3. Send the content to OpenAI for intelligent naming")
    print("4. Rename the files based on AI analysis\n")
    
    print("Expected transformations:")
    print("  document_001.pdf → Monthly_Sales_Report_December_2023.pdf")
    print("  scan_002.pdf → Product_Strategy_Meeting_Minutes_Jan_2024.pdf")
    print("  file_003.pdf → Invoice_Acme_Corporation_INV-2024-0234.pdf")
    
    print(f"\n📁 Sample files are located in: {test_dir}")
    print("\nTo test the bot with these files:")
    print(f"1. Set ONEDRIVE_FOLDER_PATH={test_dir} in your .env")
    print("2. Run: python pdf_renamer_bot.py")
    print("3. Choose 'y' to process existing files")
    
    print("\n💡 Tip: You can copy these PDFs to your actual OneDrive folder")
    print("   to test the bot with real monitoring.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDemo cancelled.")
        sys.exit(0)
