"""
PDF text extraction module.
Handles extraction of text content from PDF files.
"""
import io
from typing import Optional

import PyPDF2


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
        """Backward-compatible path-based extractor."""
        try:
            with open(pdf_path, "rb") as file:
                return self.extract_text_from_bytes(file.read())
        except Exception as e:
            print(f"Error extracting text from {pdf_path}: {e}")
            return None

    def extract_text_from_bytes(self, data: bytes) -> Optional[str]:
        """Extract text from PDF bytes (preferred for cloud downloads)."""
        try:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(data))
            total_pages = len(pdf_reader.pages)
            pages_to_read = min(total_pages, self.max_pages)

            text_content = []
            for page_num in range(pages_to_read):
                page = pdf_reader.pages[page_num]
                text = page.extract_text()
                if text:
                    text_content.append(text)

            return "\n".join(text_content)
        except Exception as e:
            print(f"Error extracting text from PDF bytes: {e}")
            return None

    def get_pdf_info(self, pdf_path: str) -> dict:
        """Backward-compatible path-based metadata extractor."""
        try:
            with open(pdf_path, "rb") as file:
                return self.get_pdf_info_from_bytes(file.read())
        except Exception as e:
            print(f"Error getting PDF info from {pdf_path}: {e}")
            return {}

    def get_pdf_info_from_bytes(self, data: bytes) -> dict:
        """Get PDF metadata from bytes."""
        try:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(data))
            return {
                "num_pages": len(pdf_reader.pages),
                "metadata": pdf_reader.metadata if pdf_reader.metadata else {},
            }
        except Exception as e:
            print(f"Error getting PDF info from bytes: {e}")
            return {}
