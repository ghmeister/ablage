"""
PDF text extraction module.
Handles extraction of text content from PDF files.
"""
import PyPDF2
from typing import Optional


class PDFExtractor:
    """Extracts text content from PDF files."""
    
    def __init__(self, max_pages: int = 10):
        """
        Initialize PDF extractor.
        
        Args:
            max_pages: Maximum number of pages to extract (to limit token usage)
        """
        self.max_pages = max_pages
    
    def extract_text(self, pdf_path: str) -> Optional[str]:
        """
        Extract text from a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Extracted text or None if extraction fails
        """
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)
                pages_to_read = min(total_pages, self.max_pages)
                
                text_content = []
                for page_num in range(pages_to_read):
                    page = pdf_reader.pages[page_num]
                    text = page.extract_text()
                    if text:
                        text_content.append(text)
                
                return '\n'.join(text_content)
        except Exception as e:
            print(f"Error extracting text from {pdf_path}: {e}")
            return None
    
    def get_pdf_info(self, pdf_path: str) -> dict:
        """
        Get basic information about a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Dictionary with PDF metadata
        """
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                info = {
                    'num_pages': len(pdf_reader.pages),
                    'metadata': pdf_reader.metadata if pdf_reader.metadata else {}
                }
                return info
        except Exception as e:
            print(f"Error getting PDF info from {pdf_path}: {e}")
            return {}
