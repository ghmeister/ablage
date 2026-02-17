"""
AI-powered naming module using Anthropic Claude API.
Generates intelligent filenames based on PDF content.
"""
import json
import os
from typing import Optional
from anthropic import Anthropic


class AIRenamer:
    """Uses AI to generate intelligent filenames based on PDF content."""
    
    def __init__(self, api_key: Optional[str] = None, custom_prompt: Optional[str] = None):
        """
        Initialize AI renamer.
        
        Args:
            api_key: Anthropic API key (if not provided, reads from environment)
            custom_prompt: Custom prompt template for naming
        """
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("Anthropic API key not provided")
        
        self.client = Anthropic(api_key=self.api_key)
        self.custom_prompt = custom_prompt or os.getenv('AI_NAMING_PROMPT')
        self.max_filename_length = int(os.getenv('MAX_FILENAME_LENGTH', '100'))
    
    def generate_filename(self, pdf_content: str, original_filename: str) -> Optional[str]:
        """
        Generate an intelligent filename based on PDF content.
        
        Args:
            pdf_content: Extracted text from the PDF
            original_filename: Original filename for reference
            
        Returns:
            Suggested filename (without extension) or None if generation fails
        """
        if not pdf_content or not pdf_content.strip():
            print("No content provided for filename generation")
            return None
        
        # Truncate content if too long (to save on API costs)
        max_content_length = 3000
        truncated_content = pdf_content[:max_content_length]
        if len(pdf_content) > max_content_length:
            truncated_content += "...[content truncated]"
        
        # Build the prompt
        if self.custom_prompt:
            prompt = f"{self.custom_prompt}\n\nContent:\n{truncated_content}"
        else:
            prompt = f"""Based on the following PDF content, suggest a concise and descriptive filename.
The filename should:
- Be descriptive and meaningful
- Use underscores or hyphens instead of spaces
- Be concise (max {self.max_filename_length} characters)
- Not include the file extension
- Use only alphanumeric characters, underscores, and hyphens

Original filename: {original_filename}

PDF Content:
{truncated_content}

Respond with ONLY the suggested filename, nothing else."""
        
        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=200,
                temperature=0.7,
                system="You are a helpful assistant that generates concise, descriptive filenames based on document content.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            suggested_name = response.content[0].text.strip()
            
            # Clean up the suggested name
            suggested_name = self._sanitize_filename(suggested_name)
            
            # Enforce length limit
            if len(suggested_name) > self.max_filename_length:
                suggested_name = suggested_name[:self.max_filename_length]
            
            return suggested_name
        
        except Exception as e:
            print(f"Error generating filename with AI: {e}")
            return None
    
    def analyze_document(self, pdf_content: str, original_filename: str) -> dict:
        """
        Analyze a PDF and return both a filename suggestion and document metadata
        for folder classification, in a single API call.

        Args:
            pdf_content: Extracted text from the PDF
            original_filename: Original filename for reference

        Returns:
            Dict with keys: filename, document_type, date, company, keywords.
            On failure falls back to generate_filename() with neutral metadata.
        """
        if not pdf_content or not pdf_content.strip():
            return self._fallback_metadata(original_filename)

        max_content_length = 3000
        truncated_content = pdf_content[:max_content_length]
        if len(pdf_content) > max_content_length:
            truncated_content += "...[content truncated]"

        system_prompt = (
            "You are a document analysis assistant. "
            "You MUST respond with ONLY valid JSON — no markdown, no explanation, no extra text.\n\n"
            "Document type taxonomy (pick exactly one):\n"
            "  invoice         — payment request: Rechnung, Faktura, Quittung, Jahresrechnung\n"
            "  insurance       — insurance policy/Police, Prämienrechnung from an insurer, AVB,\n"
            "                    accident report (Unfallmeldung), insurer correspondence about coverage\n"
            "  tax             — Veranlagungsverfügung, Steuerrechnung, Lohnausweis, Säule 3a,\n"
            "                    Steuerbescheinigung (even if issued by an insurer!), BVG/pension statements\n"
            "  medical_report  — doctor's report, diagnosis/Befund, lab results — NOT medical bills\n"
            "  bank_statement  — Kontoauszug, Depotauszug, account/depot overview\n"
            "  contract        — Arbeitsvertrag, Mietvertrag, Hypothek, subscription contract\n"
            "  warranty        — Garantieschein, warranty certificate\n"
            "  id_document     — passport, ID card, Ausländerausweis, Führerausweis\n"
            "  certificate     — Schulzeugnis, Arbeitszeugnis, Diplom, sports/dive certificate\n"
            "  letter          — general correspondence not matching another type\n"
            "  quote           — Offerte, Angebot, Kostenvoranschlag\n"
            "  other           — recipes, manuals, or anything not listed above\n\n"
            "Important distinctions:\n"
            "  - A tax document FROM an insurer (e.g. Steuerbescheinigung Allianz) → tax, NOT insurance\n"
            "  - A medical BILL (Rechnung Zahnarzt) → invoice, NOT medical_report\n"
            "  - A Prämienrechnung from Allianz/Mobiliar → insurance\n"
            "  - A BVG/pension fund statement → tax"
        )

        user_prompt = (
            f"Analyze the following PDF and return a JSON object with these exact keys:\n"
            f"  filename      — concise descriptive filename (no extension, max {self.max_filename_length} chars,\n"
            f"                  use hyphens or underscores, alphanumeric only)\n"
            f"  document_type — one value from the taxonomy above\n"
            f"  date          — document date as YYYY-MM-DD, or null if not found\n"
            f"  company       — name of the issuing company/sender, or null\n"
            f"  keywords      — list of 3-8 lowercase German/English keywords describing the document\n\n"
            f"Original filename: {original_filename}\n\n"
            f"PDF Content:\n{truncated_content}\n\n"
            f"Respond with ONLY the JSON object."
        )

        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=400,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)

            filename = self._sanitize_filename(str(data.get("filename", "") or original_filename))
            if len(filename) > self.max_filename_length:
                filename = filename[:self.max_filename_length]

            return {
                "filename": filename or original_filename,
                "document_type": str(data.get("document_type") or "other").lower(),
                "date": data.get("date"),
                "company": data.get("company"),
                "keywords": [str(k).lower() for k in (data.get("keywords") or [])],
            }

        except Exception as e:
            print(f"Error in analyze_document: {e}. Falling back to basic rename.")
            return self._fallback_metadata(original_filename)

    def _fallback_metadata(self, original_filename: str) -> dict:
        """Return neutral metadata using generate_filename() for the filename."""
        filename = self.generate_filename("", original_filename) or original_filename
        return {
            "filename": filename,
            "document_type": "other",
            "date": None,
            "company": None,
            "keywords": [],
        }

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename to ensure it's safe for filesystem.
        
        Args:
            filename: Raw filename string
            
        Returns:
            Sanitized filename
        """
        # Remove quotes if present
        filename = filename.strip('"').strip("'")
        
        # Replace problematic characters
        replacements = {
            ' ': '_',
            '/': '-',
            '\\': '-',
            ':': '-',
            '*': '',
            '?': '',
            '"': '',
            '<': '',
            '>': '',
            '|': '-'
        }
        
        for old, new in replacements.items():
            filename = filename.replace(old, new)
        
        # Remove any leading/trailing special characters
        filename = filename.strip('_-.')
        
        return filename
