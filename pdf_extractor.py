"""
PDF text extraction module.
Handles extraction of text content from PDF files.
Falls back to GPT-4o Vision OCR for scanned PDFs that yield little or no text.
"""
import base64
import io
from typing import Optional

import PyPDF2

OCR_THRESHOLD = 100  # characters — below this, assume scanned and trigger OCR


class PDFExtractor:
    """Extracts text content from PDF files."""

    def __init__(self, max_pages: int = 10):
        self.max_pages = max_pages

    def extract_text(self, pdf_path: str) -> Optional[str]:
        """Backward-compatible path-based extractor."""
        try:
            with open(pdf_path, "rb") as file:
                return self.extract_text_from_bytes(file.read())
        except Exception as e:
            print(f"Error extracting text from {pdf_path}: {e}")
            return None

    def extract_text_from_bytes(self, data: bytes, api_key: str = "") -> Optional[str]:
        """
        Extract text from PDF bytes.
        If PyPDF2 yields fewer than OCR_THRESHOLD characters, falls back to
        GPT-4o Vision OCR (requires api_key).
        """
        text = self._pypdf2_extract(data)
        if text and len(text.strip()) >= OCR_THRESHOLD:
            return text

        if api_key:
            page_count = self._page_count(data)
            print(f"OCR       : PyPDF2 yielded {len((text or '').strip())} chars "
                  f"({page_count} pages) — falling back to GPT-4o Vision")
            ocr_text = self._ocr_with_gpt4v(data, api_key)
            if ocr_text and len(ocr_text.strip()) > len((text or "").strip()):
                print(f"OCR       : extracted {len(ocr_text)} characters via Vision")
                return ocr_text

        return text or None

    # ── private helpers ───────────────────────────────────────────────────────

    def _pypdf2_extract(self, data: bytes) -> str:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            pages = min(len(reader.pages), self.max_pages)
            parts = []
            for i in range(pages):
                t = reader.pages[i].extract_text()
                if t:
                    parts.append(t)
            return "\n".join(parts)
        except Exception as e:
            print(f"PyPDF2    : extraction error: {e}")
            return ""

    def _page_count(self, data: bytes) -> int:
        try:
            return len(PyPDF2.PdfReader(io.BytesIO(data)).pages)
        except Exception:
            return 0

    def _ocr_with_gpt4v(self, data: bytes, api_key: str) -> str:
        """Render PDF pages with PyMuPDF and send to GPT-4o Vision."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            print("OCR       : pymupdf not installed — skipping Vision OCR")
            return ""

        try:
            from openai import OpenAI
            doc = fitz.open(stream=data, filetype="pdf")
            pages_to_ocr = min(len(doc), self.max_pages)

            # Render each page at 150 DPI (good quality / cost balance)
            matrix = fitz.Matrix(150 / 72, 150 / 72)
            images: list[str] = []
            for i in range(pages_to_ocr):
                pix = doc[i].get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
                png = pix.tobytes("png")
                images.append(base64.b64encode(png).decode())

            if not images:
                return ""

            content: list[dict] = [
                {
                    "type": "text",
                    "text": (
                        "Extract all text from this document exactly as it appears. "
                        "Preserve line breaks and structure. "
                        "Return only the raw text — no commentary, no markdown."
                    ),
                }
            ]
            for img_b64 in images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    },
                })

            import cost_tracker
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": content}],
                max_tokens=4000,
            )
            cost_tracker.log("gpt-4o", "ocr", resp.usage)
            return resp.choices[0].message.content or ""

        except Exception as e:
            print(f"OCR       : Vision OCR failed: {e}")
            return ""

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
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            return {
                "num_pages": len(reader.pages),
                "metadata": reader.metadata if reader.metadata else {},
            }
        except Exception as e:
            print(f"Error getting PDF info from bytes: {e}")
            return {}
