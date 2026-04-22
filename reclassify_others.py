"""
One-time backfill script: reclassify documents whose type is 'other' or 'warranty'.

  warranty  → contract  (no GPT needed, deterministic)
  other     → GPT-determined type from the known type list

Run inside the bot container:
    docker exec <container> python reclassify_others.py

The script only updates document_type in the DB — it does NOT move files on
OneDrive.  After running, use the web UI or trigger a manual reclassify if
you also want to move the files.

Set DRY_RUN=1 to preview changes without writing anything.
"""
from __future__ import annotations

import os
import sys
import json

DB_PATH   = os.getenv("DB_PATH", "/data/documents.db")
API_KEY   = os.getenv("OPENAI_API_KEY", "")
DRY_RUN   = os.getenv("DRY_RUN", "0") == "1"
BATCH     = int(os.getenv("BATCH", "20"))   # GPT calls per run (cost control)

KNOWN_TYPES = [
    "invoice",
    "insurance",
    "tax",
    "letter",
    "certificate",
    "bank_statement",
    "medical_report",
    "quote",
    "contract",
    "id_document",
    "payslip",
    "donation_receipt",
    "other",
]

SYSTEM_PROMPT = (
    "You classify a document into exactly one of these types based on metadata and text excerpt:\n"
    + "\n".join(f"  - {t}" for t in KNOWN_TYPES)
    + "\n\n"
    "Guidelines:\n"
    "  payslip          – salary slip, Lohnausweis, Gehaltsabrechnung\n"
    "  donation_receipt – Spendenbescheinigung, Spendenquittung\n"
    "  tax              – tax assessment, Steuererklärung, Steuerrechnung\n"
    "  bank_statement   – Kontoauszug, credit card statement, Kreditkartenabrechnung\n"
    "  insurance        – policy, Versicherungspolice, Prämienrechnung\n"
    "  medical_report   – doctor letter, lab result, Arztbericht, Krankengeschichte\n"
    "  certificate      – Zeugnis, Diplom, Arbeitszeugnis, Attest, Nachweis\n"
    "  contract         – Vertrag, Mietvertrag, Garantie, warranty\n"
    "  id_document      – Ausweis, Reisepass, Führerausweis\n"
    "  letter           – general correspondence that fits none of the above\n"
    "  other            – genuinely unclassifiable (recipes, manuals, personal notes)\n"
    "\n"
    "Return ONLY the type string, nothing else."
)


def classify_one(client, doc: dict) -> str:
    parts = []
    if doc.get("new_filename"):
        parts.append(f"Filename: {doc['new_filename']}")
    if doc.get("sender"):
        parts.append(f"Sender: {doc['sender']}")
    if doc.get("company"):
        parts.append(f"Company: {doc['company']}")
    if doc.get("keywords"):
        parts.append(f"Keywords: {doc['keywords']}")
    if doc.get("extracted_text"):
        parts.append(f"Text excerpt:\n{doc['extracted_text'][:1500]}")
    user_msg = "\n".join(parts) or "(no metadata available)"

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=20,
        temperature=0,
    )
    result = resp.choices[0].message.content.strip().lower()
    return result if result in KNOWN_TYPES else "other"


def main() -> None:
    import sqlite3
    if not API_KEY:
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ── 1. warranty → contract (deterministic) ───────────────────────────────
    cur.execute("SELECT id, new_filename FROM documents WHERE document_type = 'warranty'")
    warranty_docs = cur.fetchall()
    print(f"warranty docs to migrate → contract: {len(warranty_docs)}")
    for doc in warranty_docs:
        print(f"  [{doc['id']}] {doc['new_filename']}")
        if not DRY_RUN:
            cur.execute(
                "UPDATE documents SET document_type = 'contract' WHERE id = ?",
                (doc["id"],),
            )
    if not DRY_RUN and warranty_docs:
        con.commit()
        print(f"  → committed {len(warranty_docs)} warranty→contract updates")

    # ── 2. other → GPT reclassification ──────────────────────────────────────
    cur.execute(
        "SELECT id, new_filename, sender, company, keywords, extracted_text "
        "FROM documents WHERE document_type = 'other' "
        "ORDER BY id LIMIT ?",
        (BATCH,),
    )
    other_docs = cur.fetchall()
    print(f"\n'other' docs to reclassify (batch={BATCH}): {len(other_docs)}")

    if not other_docs:
        print("Nothing to do.")
        con.close()
        return

    from openai import OpenAI
    client = OpenAI(api_key=API_KEY)

    stats: dict[str, int] = {}
    for doc in other_docs:
        new_type = classify_one(client, dict(doc))
        stats[new_type] = stats.get(new_type, 0) + 1
        changed = new_type != "other"
        marker = "→" if changed else "="
        print(f"  [{doc['id']}] {marker} {new_type:20s}  {doc['new_filename']}")
        if not DRY_RUN and changed:
            cur.execute(
                "UPDATE documents SET document_type = ? WHERE id = ?",
                (new_type, doc["id"]),
            )

    if not DRY_RUN:
        con.commit()

    print("\nSummary of reclassified 'other' docs:")
    for t, n in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {n:3d}  {t}")

    remaining = con.execute(
        "SELECT COUNT(*) FROM documents WHERE document_type = 'other'"
    ).fetchone()[0]
    print(f"\n'other' docs remaining in DB: {remaining}")
    if remaining > 0:
        print(f"Re-run to process the next batch of {BATCH}.")

    con.close()


if __name__ == "__main__":
    main()
