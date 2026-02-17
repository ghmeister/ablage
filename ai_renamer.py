"""
AI-powered naming module using the OpenAI API.
Generates intelligent filenames based on PDF content.
"""
import json
import os
from typing import Optional
from openai import OpenAI


class AIRenamer:
    """Uses AI to generate intelligent filenames based on PDF content."""

    def __init__(self, api_key: Optional[str] = None, custom_prompt: Optional[str] = None):
        """
        Initialize AI renamer.

        Args:
            api_key: OpenAI API key (if not provided, reads OPENAI_API_KEY from environment)
            custom_prompt: Custom prompt template for naming
        """
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")

        self.client = OpenAI(api_key=self.api_key)
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

        max_content_length = 3000
        truncated_content = pdf_content[:max_content_length]
        if len(pdf_content) > max_content_length:
            truncated_content += "...[content truncated]"

        if self.custom_prompt:
            user_prompt = f"{self.custom_prompt}\n\nContent:\n{truncated_content}"
        else:
            user_prompt = (
                f"Based on the following PDF content, suggest a concise and descriptive filename.\n"
                f"The filename should:\n"
                f"- Be descriptive and meaningful\n"
                f"- Use underscores or hyphens instead of spaces\n"
                f"- Be concise (max {self.max_filename_length} characters)\n"
                f"- Not include the file extension\n"
                f"- Use only alphanumeric characters, underscores, and hyphens\n\n"
                f"Original filename: {original_filename}\n\n"
                f"PDF Content:\n{truncated_content}\n\n"
                f"Respond with ONLY the suggested filename, nothing else."
            )

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=200,
                temperature=0.7,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that generates concise, descriptive filenames based on document content."},
                    {"role": "user", "content": user_prompt},
                ],
            )

            suggested_name = response.choices[0].message.content.strip()
            suggested_name = self._sanitize_filename(suggested_name)

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
            f"  filename      — structured filename following this EXACT pattern:\n"
            f"                  <Type>_<FirstName>_<Details>_<YYYYMMDD>\n"
            f"                  Parts separated by underscores. Rules:\n"
            f"                  1. Type: German document type (Rechnung, Offerte, Brief, Lohnausweis,\n"
            f"                     Praemienrechnung, Kontoauszug, Vertrag, Garantie, Zeugnis, Befund, etc.)\n"
            f"                  2. FirstName: the person the document is for/about, if identifiable.\n"
            f"                     Known family members: Manuel, Judith, Dominik, Clara, Nora.\n"
            f"                     Omit this part if no person is identifiable.\n"
            f"                  3. Details: concise additional info (company name, subject, etc.)\n"
            f"                  4. Date: document date as YYYYMMDD (from the document content, NOT the filename)\n"
            f"                  Examples: Rechnung_Manuel_Zahnarzt_20250315\n"
            f"                           Praemienrechnung_Allianz_Motorfahrzeug_20251001\n"
            f"                           Lohnausweis_Judith_Accenture_20241231\n"
            f"                           Befund_Manuel_Lungenfunktion_20241205\n"
            f"                           Rechnung_Amex_20260128\n"
            f"                  Max {self.max_filename_length} chars, alphanumeric and underscores only.\n"
            f"  document_type — one value from the taxonomy above\n"
            f"  date          — document date as YYYY-MM-DD, or null if not found\n"
            f"  company       — name of the issuing company/sender, or null\n"
            f"  keywords      — list of 3-8 lowercase German/English keywords describing the document\n\n"
            f"Original filename: {original_filename}\n\n"
            f"PDF Content:\n{truncated_content}\n\n"
            f"Respond with ONLY the JSON object."
        )

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=400,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            raw = response.choices[0].message.content.strip()
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
        filename = self.generate_filename("", original_filename) or self._sanitize_filename(original_filename)
        return {
            "filename": filename or "unnamed",
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
        filename = filename.strip('"').strip("'")

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

        filename = filename.strip('_-.')

        return filename
