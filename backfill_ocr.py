#!/usr/bin/env python3
"""
One-time script: OCR scanned PDFs that have no extracted_text and fill in what is missing.

These documents were indexed by index_existing.py without OCR, so they only carry
a filename-derived title. For each of them this script
  1. fetches the PDF (OneDrive via Graph, or a local archive folder with --archive-root),
  2. extracts text (PDF text layer first, then local Tesseract OCR),
  3. stores it in extracted_text (the FTS index updates itself through the DB triggers),
  4. fills ONLY EMPTY metadata fields (sender, company, recipient, document_date, keywords)
     from the text via AIRenamer.analyze_document — existing values are never overwritten.
     Two guards against OCR/model errors:
       - document_date is only taken if that exact date appears in the OCR text
         (a date guessed from the filename is dropped, the field stays empty);
       - recipient is only taken if it is a known person (KNOWN_PERSONS + FAMILY_NAME env,
         defaults manuel,judith,dominik,clara,nora / Meister); anything else is ignored,
  5. chunks + embeds the text so semantic search finds the document.

Nothing in OneDrive is renamed, moved or deleted. Identity documents (document_type
'id_document' or an ID-like filename) are skipped and never sent to OpenAI;
--include-ids overrides that.

Default is a DRY RUN: it downloads and OCRs, prints what it would write, writes nothing.
--apply writes to the DB and first takes a backup copy next to it (<db>.bak-<timestamp>).

Run inside the bot container (it has the Graph and OpenAI settings):
    python backfill_ocr.py --limit 5                 # dry run on 5 documents
    python backfill_ocr.py --limit 20 --apply        # write a first small batch, then check it
    python backfill_ocr.py --apply                   # everything left

Safe to re-run: documents that now have text are no longer selected.

Needs: tesseract-ocr with the 'deu' language data (in the Dockerfile), pymupdf.
Environment: DB_PATH, TENANT_ID/CLIENT_ID[/CLIENT_SECRET/USER_ID] (or --archive-root),
OPENAI_API_KEY (not needed with --no-ai), OUTPUT_BASE_FOLDER, TESSERACT_CMD (default 'tesseract'),
TESSDATA_PREFIX (only if the language data is not in the default place).
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

import db as _db

MIN_TEXT = 50          # a stored text at or below this many characters counts as "empty"
OCR_THRESHOLD = 100    # a text layer shorter than this is treated as a scan
MAX_TEXT_STORE = 10_000  # same limit as index_existing.py

_ID_FILENAME = re.compile(
    r"ausweis|\bpass\b|passport|reisepass|f[uü]hrerschein|fahrausweis|identit[aä]t|id[- ]?karte|"
    r"\bidk\b|\bid\b|aufenthaltsbewilligung|versichertenkarte|krankenkassenkarte",
    re.IGNORECASE,
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def is_id_document(doc: dict) -> bool:
    return doc.get("document_type") == "id_document" or bool(_ID_FILENAME.search(doc.get("new_filename") or ""))


def select_documents(ids: list[int]) -> list[dict]:
    """All candidates; --limit is applied by the caller after identity documents are excluded."""
    sql = (
        "SELECT * FROM documents WHERE length(coalesce(extracted_text,'')) <= ? "
        "AND coalesce(onedrive_path,'') != ''"
    )
    params: list[Any] = [MIN_TEXT]
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    sql += " ORDER BY id"
    with _db._conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# PDF source
# ---------------------------------------------------------------------------

def _prefix(args: argparse.Namespace) -> str:
    return (args.onedrive_prefix or os.getenv("OUTPUT_BASE_FOLDER", "")).strip("/\\")


class Fetcher:
    """Returns the PDF bytes of a document, from a local archive folder or via Graph."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.prefix = _prefix(args)
        self.root = Path(args.archive_root) if args.archive_root else None
        self.graph = None
        if not self.root:
            tenant, client = os.getenv("TENANT_ID"), os.getenv("CLIENT_ID")
            if not (tenant and client):
                sys.exit("Set TENANT_ID and CLIENT_ID (Graph) or pass --archive-root.")
            from graph_client import GraphClient
            self.graph = GraphClient(
                tenant, client,
                client_secret=os.getenv("CLIENT_SECRET") or None,
                user_id=os.getenv("USER_ID") or None,
            )

    def _relative(self, onedrive_path: str) -> str:
        p = onedrive_path.strip("/")
        if self.prefix and p.startswith(self.prefix + "/"):
            p = p[len(self.prefix) + 1:]
        return p

    def fetch(self, onedrive_path: str) -> bytes:
        if self.root:
            return (self.root / self._relative(onedrive_path)).read_bytes()
        full = onedrive_path
        if self.prefix and not onedrive_path.startswith(self.prefix + "/"):
            full = f"{self.prefix}/{onedrive_path}"
        item = self.graph.get_item_by_path(full)
        return self.graph.download_file(item["id"])


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(data: bytes, args: argparse.Namespace) -> tuple[str, int, str]:
    """Return (text, page_count, method) where method is 'text-layer' or 'ocr'."""
    try:
        import pymupdf
    except ImportError:  # older PyMuPDF
        import fitz as pymupdf  # type: ignore[no-redef]

    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = min(len(doc), args.max_pages)

    layer = _clean("\n".join(doc[i].get_text() for i in range(pages)))
    if len(layer) >= OCR_THRESHOLD:
        return layer, len(doc), "text-layer"

    parts: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(pages):
            png = Path(tmp) / f"p{i}.png"
            doc[i].get_pixmap(dpi=args.dpi, colorspace=pymupdf.csGRAY).save(str(png))
            r = subprocess.run(
                [os.getenv("TESSERACT_CMD", "tesseract"), str(png), "stdout", "-l", args.lang, "--psm", "3"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            )
            if r.returncode != 0:
                raise RuntimeError(f"tesseract failed: {r.stderr.strip()[:200]}")
            parts.append(r.stdout)
    return _clean("\n".join(parts)), len(doc), "ocr"


# ---------------------------------------------------------------------------
# Metadata: fill only what is empty
# ---------------------------------------------------------------------------

def _empty(value: Any) -> bool:
    return value is None or not str(value).strip()


def _valid_date(value: Any) -> bool:
    if not (isinstance(value, str) and _DATE.match(value)):
        return False
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return False
    return 1990 <= d.year <= date.today().year + 1


_MONTHS = {
    1: ("januar", "jan"), 2: ("februar", "feb"), 3: ("märz", "mär", "mrz"), 4: ("april", "apr"),
    5: ("mai",), 6: ("juni", "jun"), 7: ("juli", "jul"), 8: ("august", "aug"),
    9: ("september", "sept", "sep"), 10: ("oktober", "okt"), 11: ("november", "nov"), 12: ("dezember", "dez"),
}


def date_in_text(iso: str, text: str) -> bool:
    """True if the exact day.month.year appears in the text (12.4.2024, 12.04.24, 2024-04-12, 12. April 2024).
    The model sometimes guesses a date from the filename; such a date is not in the text and is rejected."""
    y, m, d = (int(x) for x in iso.split("-"))
    day, year = f"0?{d}", f"(?:{y}|{y % 100:02d})"
    months = "|".join(re.escape(n) for n in _MONTHS[m])
    patterns = [
        rf"(?<!\d){day}\s*[./]\s*0?{m}\s*[./]\s*{year}(?!\d)",
        rf"(?<!\d){y}\s*-\s*{m:02d}\s*-\s*{d:02d}(?!\d)",
        rf"(?<!\d){day}\s*\.?\s*(?:{months})\.?\s*{y}(?!\d)",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


KNOWN_PERSONS = {p.strip().lower() for p in os.getenv("KNOWN_PERSONS", "manuel,judith,dominik,clara,nora").split(",") if p.strip()}
FAMILY_NAME = os.getenv("FAMILY_NAME", "Meister")


def known_recipient(value: Any) -> Optional[str]:
    """Accept a recipient only if it is a known person: a known first name together with the family name,
    or a bare known first name (completed with the family name). Misread or foreign names return None."""
    s = " ".join(str(value or "").split())
    words = {w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß]+", s)}
    if not words & KNOWN_PERSONS:
        return None
    if s.lower() in KNOWN_PERSONS:
        return f"{s.title()} {FAMILY_NAME}"
    return s if FAMILY_NAME.lower() in words else None


def propose_fields(doc: dict, meta: dict, text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for col in ("sender", "company"):
        if _empty(doc.get(col)) and not _empty(meta.get(col)):
            out[col] = str(meta[col]).strip()
    if _empty(doc.get("recipient")):
        recipient = known_recipient(meta.get("recipient"))
        if recipient:
            out["recipient"] = recipient
    if _empty(doc.get("document_date")) and _valid_date(meta.get("date")) and date_in_text(meta["date"], text):
        out["document_date"] = meta["date"]
    if _empty(doc.get("keywords")) and meta.get("keywords"):
        out["keywords"] = ", ".join(str(k) for k in meta["keywords"])
    return out


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def backup_db() -> Path:
    src = _db._get_db_path()
    dst = src.with_name(f"{src.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dst)) as d:
        s.backup(d)
    return dst


def write_document(doc_id: int, text: str, fields: dict[str, str]) -> None:
    """Update only the columns we own. The FTS table follows through the UPDATE trigger."""
    cols = {"extracted_text": text[:MAX_TEXT_STORE], **fields}
    allowed = {"extracted_text", "sender", "company", "recipient", "document_date", "keywords"}
    assert set(cols) <= allowed
    with _db._conn() as conn:
        conn.execute(
            f"UPDATE documents SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
            [*cols.values(), doc_id],
        )


def embed_document(doc_id: int, text: str, api_key: str, refresh_doc_vector: bool) -> None:
    from embed import build_document_text, chunk_text, get_embedding
    chunks = chunk_text(text[:MAX_TEXT_STORE])
    _db.store_chunks(doc_id, chunks, [get_embedding(c, api_key) for c in chunks])
    if refresh_doc_vector:
        doc = _db.get_document(doc_id)
        _db.store_embedding(doc_id, get_embedding(build_document_text(doc), api_key))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="OCR documents without text and fill empty metadata")
    ap.add_argument("--apply", action="store_true", help="write to the DB (default: dry run)")
    ap.add_argument("--db", metavar="PATH", help="path to documents.db (overrides DB_PATH)")
    ap.add_argument("--limit", type=int, default=0, help="process at most N documents")
    ap.add_argument("--ids", type=int, nargs="+", default=[], help="only these document ids")
    ap.add_argument("--archive-root", metavar="DIR", help="read PDFs from a local archive folder instead of Graph")
    ap.add_argument("--onedrive-prefix", metavar="PATH", help="prefix of stored paths (default OUTPUT_BASE_FOLDER)")
    ap.add_argument("--include-ids", action="store_true", help="also process identity documents (not recommended)")
    ap.add_argument("--no-ai", action="store_true", help="OCR + text only: no metadata, no embeddings, no OpenAI")
    ap.add_argument("--no-embed", action="store_true", help="skip chunk embeddings (run backfill_chunks.py later)")
    ap.add_argument("--no-backup", action="store_true", help="do not back up the DB before --apply")
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--lang", default=os.getenv("TESSERACT_LANGS", "deu+eng"))
    args = ap.parse_args()

    if args.db:
        _db.set_db_path(args.db)
    _db.init_db()

    api_key = os.getenv("OPENAI_API_KEY", "")
    renamer = None
    if not args.no_ai:
        if not api_key:
            sys.exit("OPENAI_API_KEY not set. Use --no-ai for OCR + text only.")
        from ai_renamer import AIRenamer
        renamer = AIRenamer()

    docs = select_documents(args.ids)
    skipped_ids = [d for d in docs if not args.include_ids and is_id_document(d)]
    todo = [d for d in docs if d not in skipped_ids]
    if args.limit:
        todo = todo[:args.limit]  # limit counts only documents that will actually be processed
    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"{mode}: {len(todo)} documents to OCR, {len(skipped_ids)} identity documents skipped.")
    for d in skipped_ids:
        print(f"  skip id={d['id']} {d['new_filename']!r}")
    if not todo:
        return

    if args.apply and not args.no_backup:
        print(f"Backup written: {backup_db()}")
    elif args.apply:
        print("WARNING: no backup (--no-backup).")

    fetcher = Fetcher(args)
    stats = dict(done=0, no_text=0, errors=0, pages=0, text_layer=0, filled=0)
    for n, doc in enumerate(todo, 1):
        label = f"[{n}/{len(todo)}] id={doc['id']} {doc['new_filename']!r}"
        try:
            text, pages, method = extract_text(fetcher.fetch(doc["onedrive_path"]), args)
            stats["pages"] += pages
            if len(text) <= MIN_TEXT:
                print(f"{label}: no readable text ({pages} p.)")
                stats["no_text"] += 1
                continue
            fields = propose_fields(doc, renamer.analyze_document(text, doc["new_filename"]), text) if renamer else {}
            print(f"{label}: {method}, {pages} p., {len(text)} chars"
                  + (f" -> fill {', '.join(f'{k}={v!r}' for k, v in fields.items())}" if fields else ""))
            if args.apply:
                write_document(doc["id"], text, fields)
                if renamer and not args.no_embed:
                    embed_document(doc["id"], text, api_key, refresh_doc_vector=bool(fields))
            stats["done"] += 1
            stats["text_layer"] += method == "text-layer"
            stats["filled"] += bool(fields)
        except Exception as e:  # keep going; one bad PDF must not stop the batch
            print(f"{label}: ERROR {e}")
            stats["errors"] += 1

    print(
        f"\nDone ({'applied' if args.apply else 'dry run'}): {stats['done']} with text "
        f"({stats['text_layer']} from text layer), {stats['filled']} with filled fields, "
        f"{stats['no_text']} without readable text, {stats['errors']} errors, {stats['pages']} pages."
    )
    if not args.apply:
        print("Nothing was written. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
