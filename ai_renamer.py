"""
AI-powered document analysis using the OpenAI API.
Generates structured filenames and metadata from PDF content.
"""
import json
import os
from typing import Optional
from openai import OpenAI
import cost_tracker


class AIRenamer:
    """Uses AI to analyze documents and generate structured filenames."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")

        self.client = OpenAI(api_key=self.api_key)
        self.max_filename_length = int(os.getenv('MAX_FILENAME_LENGTH', '100'))

    def analyze_document(self, pdf_content: str, original_filename: str, email_context: dict | None = None) -> dict:
        """
        Analyze a PDF and return a filename suggestion plus document metadata
        in a single API call.

        Returns:
            Dict with keys: filename, document_type, date, company, sender,
            recipient, keywords. Falls back to classifying from filename when
            PDF text is empty, or to _fallback_metadata on total failure.
        """
        if not pdf_content or not pdf_content.strip():
            print(f"PDF text empty — classifying from filename: {original_filename}")
            return self.classify_from_filename(original_filename)

        email_context_str = ""
        if email_context:
            parts = []
            if email_context.get("from"):
                parts.append(f"  From: {email_context['from']}")
            if email_context.get("subject"):
                parts.append(f"  Subject: {email_context['subject']}")
            if email_context.get("date"):
                parts.append(f"  Date: {email_context['date']}")
            if parts:
                email_context_str = "Email context (use to assist classification):\n" + "\n".join(parts) + "\n\n"

        max_content_length = 3000
        truncated_content = pdf_content[:max_content_length]
        if len(pdf_content) > max_content_length:
            truncated_content += "...[content truncated]"

        system_prompt = (
            "You are a document analysis assistant. "
            "You MUST respond with ONLY valid JSON — no markdown, no explanation, no extra text.\n\n"
            "Document type taxonomy (pick exactly one):\n"
            "  invoice         — ANY payment request or bill: Rechnung, Faktura, Quittung, Jahresrechnung,\n"
            "                    Nebenkostenabrechnung, utility bill, membership fee, subscription invoice,\n"
            "                    Mahnungen (payment reminders), any document with a total amount due\n"
            "  insurance       — insurance policy/Police, Prämienrechnung from an insurer, AVB,\n"
            "                    accident report (Unfallmeldung), insurer correspondence about coverage,\n"
            "                    Versicherungsnachweis, Deckungsbestätigung\n"
            "  tax             — Veranlagungsverfügung, Steuerrechnung, Säule 3a,\n"
            "                    Steuerbescheinigung (even if issued by an insurer!), BVG/pension statements,\n"
            "                    Quellensteuer, Grundstückgewinnsteuer, any document from Steueramt/ESTV\n"
            "  medical_report  — doctor's report, diagnosis/Befund, lab results, Therapiebericht,\n"
            "                    Impfausweis, Austrittsbericht — NOT medical bills (those are invoice)\n"
            "  bank_statement  — Kontoauszug, Depotauszug, account/depot overview, Vermögensübersicht\n"
            "  contract         — Arbeitsvertrag, Mietvertrag, Hypothek, subscription contract,\n"
            "                     Dienstleistungsvertrag, Garantieschein, warranty certificate,\n"
            "                     any multi-page agreement with signatures\n"
            "  id_document      — passport, ID card, Ausländerausweis, Führerausweis, Niederlassungsbewilligung\n"
            "  certificate      — Schulzeugnis, Arbeitszeugnis, Diplom, sports/dive/training certificate,\n"
            "                     Kursbestätigung, Teilnahmebestätigung, any formal attestation of achievement\n"
            "  letter           — formal written correspondence from a government body, employer, school,\n"
            "                     lawyer, or organisation that does not fit a more specific type above.\n"
            "                     Includes: Entscheid, Verfügung (non-tax), Einladung, Mahnung (non-payment),\n"
            "                     Kündigungsschreiben, Informationsschreiben\n"
            "  quote            — Offerte, Angebot, Kostenvoranschlag, Preisangebot\n"
            "  payslip          — Lohnausweis, Gehaltsabrechnung, Lohnabrechnung, salary slip,\n"
            "                     any document showing monthly/annual salary breakdown from an employer\n"
            "  donation_receipt — Spendenbescheinigung, Spendenquittung, donation certificate\n"
            "  other            — ONLY use this for documents that genuinely cannot be classified above:\n"
            "                     recipes, product manuals, personal notes, photos/scans of objects.\n"
            "                     DO NOT use 'other' for any document from a company, government body,\n"
            "                     insurer, bank, employer, school, or medical provider — those always\n"
            "                     fit one of the types above.\n\n"
            "Important distinctions:\n"
            "  - A tax document FROM an insurer (e.g. Steuerbescheinigung Allianz) → tax, NOT insurance\n"
            "  - A medical BILL (Rechnung Zahnarzt) → invoice, NOT medical_report\n"
            "  - A Prämienrechnung from Allianz/Mobiliar → insurance\n"
            "  - A BVG/pension fund statement → tax\n"
            "  - A Gemeinde letter about fees/taxes → invoice or tax, not letter\n"
            "  - A SERAFE bill → invoice\n"
            "  - A Lohnausweis or Gehaltsabrechnung from an employer → payslip\n"
            "  - A Spendenbescheinigung → donation_receipt\n"
            "  - A Garantieschein or warranty card → contract\n"
            "  - When in doubt between two types, pick the more specific one over 'other' or 'letter'"
        )

        user_prompt = (
            f"Analyze the following PDF and return a JSON object with these exact keys:\n"
            f"  filename      — structured filename following this EXACT pattern:\n"
            f"                  <Type>_<Company>_<FirstName>_<Details>_<YYYYMMDD>\n"
            f"                  Parts separated by underscores. Rules:\n"
            f"                  1. Type: German document type (Rechnung, Offerte, Brief, Lohnausweis,\n"
            f"                     Praemienrechnung, Kontoauszug, Vertrag, Garantie, Zeugnis, Befund, etc.)\n"
            f"                  2. Company: simplified sender/company name — use the short, commonly known\n"
            f"                     brand name only, drop legal suffixes (GmbH, AG, SA, etc.).\n"
            f"                     Examples: 'Swisscard AECS GmbH' → Swisscard, 'Die Mobiliar' → Mobiliar,\n"
            f"                     'Gebäudeversicherung Bern' → GVB, 'Allianz Suisse' → Allianz.\n"
            f"                     Omit if no company/sender is identifiable.\n"
            f"                  3. FirstName: the person the document is for/about, if identifiable.\n"
            f"                     Known family members: Manuel, Judith, Dominik, Clara, Nora.\n"
            f"                     Omit this part if no person is identifiable.\n"
            f"                  4. Details: concise additional info. Include relevant identifiers such as:\n"
            f"                     - Insurance/policy number (e.g. V955415027, G-1286-0634)\n"
            f"                     - Contract or account number\n"
            f"                     - Subject or product (e.g. Motorfahrzeug, Strom, Hausrat)\n"
            f"                     - Tax year for tax documents\n"
            f"                     Combine multiple details with underscores. Keep it concise.\n"
            f"                     Omit if Type + Company + Name already describe the document fully.\n"
            f"                  5. Date: document date as YYYYMMDD (from the document content, NOT the filename)\n"
            f"                  Examples: Rechnung_Swisscard_Manuel_20260128\n"
            f"                           Praemienrechnung_Allianz_V955415027_Motorfahrzeug_20251001\n"
            f"                           Lohnausweis_Accenture_Judith_20241231\n"
            f"                           Befund_Lungenfunktion_Manuel_20241205\n"
            f"                           Rechnung_BKW_Strom_20250301\n"
            f"                           Steuerrechnung_Bern_2025_20250615\n"
            f"                           Kontoauszug_UBS_Manuel_IBAN-CH12-1234_20260131\n"
            f"                  Max {self.max_filename_length} chars, alphanumeric and underscores only.\n"
            f"  document_type — one value from the taxonomy above\n"
            f"  date          — document date as YYYY-MM-DD, or null if not found\n"
            f"  company       — short brand/company name of the ISSUER for routing (e.g. Swisscard, Allianz), or null\n"
            f"  sender        — full name of the sender/issuer as printed on the document\n"
            f"                  (formal company name or person name), or null\n"
            f"  recipient     — full name of the recipient the document is addressed TO\n"
            f"                  (person name with first+last if available, or organisation), or null\n"
            f"  keywords      — list of 3-8 lowercase German/English keywords describing the document\n"
            f"  tax_relevant  — true if this document is relevant for the Swiss annual tax declaration.\n"
            f"                  Set to true for: Lohnausweis, Spendenbescheinigung/donation certificate,\n"
            f"                  Steuerbescheinigung, BVG/pension fund statement, Säule-3a certificate,\n"
            f"                  home maintenance invoices (Elektriker, Heizung, Fenster, Maler, Sanitär,\n"
            f"                  Dach, Isolation — any skilled trade working on or in the home),\n"
            f"                  bank tax documents (Zinsausweis, Steuerausweis), AirBnB income\n"
            f"                  statements, any document from Steueramt/ESTV.\n"
            f"                  Set to false for regular invoices, insurance policies, letters, etc.\n\n"
            f"Original filename: {original_filename}\n\n"
            f"{email_context_str}"
            f"PDF Content:\n{truncated_content}\n\n"
            f"Respond with ONLY the JSON object."
        )

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=500,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            cost_tracker.log("gpt-4o-mini", "classification", response.usage)
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
                "sender": data.get("sender"),
                "recipient": data.get("recipient"),
                "keywords": [str(k).lower() for k in (data.get("keywords") or [])],
                "tax_relevant": bool(data.get("tax_relevant", False)),
            }

        except Exception as e:
            print(f"Error in analyze_document: {e}. Falling back to original filename.")
            return self._fallback_metadata(original_filename)

    def classify_from_filename(self, filename_stem: str) -> dict:
        """
        Classify a document based on its structured filename stem alone.
        Used when PDF text extraction yields nothing.

        The filename follows the pattern: Type_Company_Person_Details_YYYYMMDD
        where Type is a German document type keyword.
        """
        system_prompt = (
            "You are a document classification assistant. "
            "You MUST respond with ONLY valid JSON — no markdown, no explanation.\n\n"
            "Document type taxonomy (pick exactly one):\n"
            "  invoice, insurance, tax, medical_report, bank_statement,\n"
            "  contract, id_document, certificate, letter, quote,\n"
            "  payslip, donation_receipt, other\n\n"
            "The filename follows a German structured pattern: Type_Company_Person_Details_YYYYMMDD\n"
            "German type prefix hints:\n"
            "  Rechnung/Faktura/Jahresrechnung  → invoice\n"
            "  Praemienrechnung/Versicherung    → insurance\n"
            "  Steuer/BVG                       → tax\n"
            "  Lohnausweis/Gehaltsabrechnung    → payslip\n"
            "  Kontoauszug/Depotauszug          → bank_statement\n"
            "  Vertrag/Mietvertrag/Garantie     → contract\n"
            "  Arztbericht/Befund               → medical_report\n"
            "  Zeugnis/Diplom/Zertifikat        → certificate\n"
            "  Offerte/Angebot                  → quote\n"
            "  Brief/Schreiben                  → letter\n"
            "  Spendenbescheinigung/Spendenquittung → donation_receipt\n"
            "Only use 'other' if the filename gives no usable type hint."
        )
        user_prompt = (
            f"Classify this document based on its filename stem only: {filename_stem}\n\n"
            "Return a JSON object with:\n"
            "  document_type — one value from the taxonomy above\n"
            "  date          — YYYY-MM-DD extracted from the filename, or null\n"
            "  company       — short company/sender name from the filename, or null\n"
            "  sender        — sender name if identifiable, or null\n"
            "  recipient     — recipient name if identifiable, or null\n"
            "  keywords      — list of 2-5 lowercase keywords derived from the filename\n"
            "Respond with ONLY the JSON object."
        )
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=200,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            cost_tracker.log("gpt-4o-mini", "classification_filename", response.usage)
            raw = response.choices[0].message.content.strip()
            data = json.loads(raw)
            return {
                "filename": filename_stem,  # keep the existing filename as-is
                "document_type": str(data.get("document_type") or "other").lower(),
                "date": data.get("date"),
                "company": data.get("company"),
                "sender": data.get("sender"),
                "recipient": data.get("recipient"),
                "keywords": [str(k).lower() for k in (data.get("keywords") or [])],
                "tax_relevant": False,  # filename-only classification can't determine tax relevance
            }
        except Exception as e:
            print(f"classify_from_filename error: {e}")
            return self._fallback_metadata(filename_stem)

    def _fallback_metadata(self, original_filename: str) -> dict:
        return {
            "filename": self._sanitize_filename(original_filename) or "unnamed",
            "document_type": "other",
            "date": None,
            "company": None,
            "sender": None,
            "recipient": None,
            "keywords": [],
            "tax_relevant": False,
        }

    def _sanitize_filename(self, filename: str) -> str:
        filename = filename.strip('"').strip("'")
        replacements = {
            ' ': '_', '/': '-', '\\': '-', ':': '-',
            '*': '', '?': '', '"': '', '<': '', '>': '', '|': '-',
        }
        for old, new in replacements.items():
            filename = filename.replace(old, new)
        return filename.strip('_-.')
