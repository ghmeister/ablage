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
     Guards against OCR/model errors:
       - a complete date written in the filename (d.m.yy, YYYYMMDD, YYYY-MM-DD) is taken first;
       - otherwise document_date is only taken if the model's date appears in the OCR text
         (a date guessed from the filename's year is dropped, the field stays empty);
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

Repair (vectors of documents whose embedding step failed; no OCR, needs OPENAI_API_KEY):
    python backfill_ocr.py --repair-from-backup /data/documents.db.bak-<timestamp>          # list
    python backfill_ocr.py --repair-from-backup /data/documents.db.bak-<timestamp> --apply  # repair

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

# Identity documents only. Deliberately NOT a bare "ausweis": Lohnausweis, Versicherungsausweis
# and Gebäudeenergieausweis are ordinary documents.
_ID_FILENAME = re.compile(
    r"ausl[aä]nderausweis|niederlassungsausweis|personalausweis|identit[aä]tsausweis|sch[uü]lerausweis|"
    r"lernfahrausweis|fahrausweis|f[uü]hrerausweis|f[uü]hrerschein|reisepass|passeport|passport|\bpass\b|"
    r"identit[aä]t|id[- ]?karte|\bidk\b|\bid\b|aufenthaltsbewilligung|versichertenkarte|krankenkassenkarte",
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
        # Like the web UI: a mounted ARCHIVE_ROOT is tried first, Graph is the fallback.
        env_root = os.getenv("ARCHIVE_ROOT", "").strip()
        self.local = Path(env_root) if env_root and not self.root else None
        self.graph = None
        if not self.root:
            tenant, client = os.getenv("TENANT_ID"), os.getenv("CLIENT_ID")
            if not (tenant and client):
                if self.local:
                    return
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
        try:
            return self._fetch(onedrive_path)
        except Exception as e:  # only a missing PDF counts as "missing", not e.g. a missing Tesseract binary
            if isinstance(e, FileNotFoundError) or "Graph error 404" in str(e):
                raise MissingPDF(str(e)) from e
            raise

    def _fetch(self, onedrive_path: str) -> bytes:
        if self.root:
            return (self.root / self._relative(onedrive_path)).read_bytes()
        if self.local:
            path = self.local / self._relative(onedrive_path)
            if path.is_file():
                return path.read_bytes()
            if not self.graph:
                raise FileNotFoundError(f"not found under ARCHIVE_ROOT: {path}")
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


_FN_YMD = re.compile(r"(?<!\d)((?:19|20)\d{2})[-.]?(0[1-9]|1[0-2])[-.]?(0[1-9]|[12]\d|3[01])(?!\d)")  # 20210108, 2021-11-26
_FN_DMY = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)")                          # 12.4.24, 09.09.2021


def date_from_filename(name: str) -> Optional[str]:
    """The document date written in a filename, as YYYY-MM-DD, or None.

    Recognises YYYYMMDD / YYYY-MM-DD and d.m.yy / d.m.yyyy. Only a complete, valid date counts
    (a bare year or YYYY-MM does not), and if the name holds more than one different date it is
    ambiguous and None is returned. Long numbers (policy numbers) are not mistaken for dates.
    """
    found: set[str] = set()
    for y, m, d in _FN_YMD.findall(name or ""):
        found.add(f"{y}-{m}-{d}")
    for d, m, y in _FN_DMY.findall(name or ""):
        found.add(f"{2000 + int(y) if len(y) == 2 else int(y):04d}-{int(m):02d}-{int(d):02d}")
    valid = {d for d in found if _valid_date(d)}
    return valid.pop() if len(valid) == 1 and len(found) == 1 else None


def propose_fields(doc: dict, meta: dict, text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for col in ("sender", "company"):
        if _empty(doc.get(col)) and not _empty(meta.get(col)):
            out[col] = str(meta[col]).strip()
    if _empty(doc.get("recipient")):
        recipient = known_recipient(meta.get("recipient"))
        if recipient:
            out["recipient"] = recipient
    if _empty(doc.get("document_date")):
        # a date written in the filename wins; otherwise the model's date, but only if it is in the text
        from_name = date_from_filename(doc.get("new_filename") or "")
        if from_name:
            out["document_date"] = from_name
        elif _valid_date(meta.get("date")) and date_in_text(meta["date"], text):
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


def _missing_file() -> Path:
    src = _db._get_db_path()
    return src.with_name(f"{src.name}.missing-ids.txt")


def load_missing() -> set[int]:
    """Ids whose PDF was not found at the stored path in an earlier --apply run."""
    try:
        return {int(x) for x in _missing_file().read_text(encoding="utf-8").split() if x.isdigit()}
    except FileNotFoundError:
        return set()


def save_missing(ids: set[int]) -> None:
    _missing_file().write_text("\n".join(str(i) for i in sorted(ids)) + "\n", encoding="utf-8")


class MissingPDF(Exception):
    """The PDF is not at the stored path (Graph 404 / file not found)."""


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


def set_date(doc_id: int, value: str) -> None:
    """Set document_date only while it is still empty (never overwrites)."""
    with _db._conn() as conn:
        conn.execute(
            "UPDATE documents SET document_date = ? WHERE id = ? AND coalesce(document_date,'') = ''",
            (value, doc_id),
        )


def fill_dates_from_filenames(args: argparse.Namespace) -> None:
    """No OCR, no OpenAI: set an empty document_date from a date written in the filename."""
    with _db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, new_filename, document_type FROM documents WHERE coalesce(document_date,'') = '' ORDER BY id")]
    todo = []
    for r in rows:
        value = date_from_filename(r["new_filename"] or "")
        if value and (args.include_ids or not is_id_document(r)):
            todo.append((r["id"], r["new_filename"], value))
    print(f"{'APPLY' if args.apply else 'DRY RUN (nothing is written)'}: {len(todo)} of {len(rows)} documents "
          f"without a date have a date in the filename.")
    for doc_id, name, value in todo:
        print(f"  id={doc_id} {name!r} -> {value}")
    if not args.apply:
        print("Re-run with --apply to write.")
        return
    if todo and not args.no_backup:
        print(f"Backup written: {backup_db()}")
    for doc_id, _, value in todo:
        set_date(doc_id, value)
    print(f"Done: {len(todo)} dates set.")


def refresh_vectors(doc_id: int, api_key: str) -> None:
    """Re-chunk from the stored text and replace the document-level vector.

    vec0 tables cannot REPLACE an existing row (INSERT OR REPLACE fails with a UNIQUE error),
    so the old document vector is deleted first. Idempotent: safe to run again.
    """
    from embed import build_document_text, chunk_text, get_embedding
    doc = _db.get_document(doc_id)
    chunks = chunk_text((doc.get("extracted_text") or "")[:MAX_TEXT_STORE])
    _db.store_chunks(doc_id, chunks, [get_embedding(c, api_key) for c in chunks])
    vector = get_embedding(build_document_text(doc), api_key)
    with _db._conn() as conn:
        try:
            conn.execute("DELETE FROM vec_documents WHERE rowid = ?", (doc_id,))
        except Exception:  # no vec table (sqlite-vec missing) — store_embedding is a no-op then
            pass
    _db.store_embedding(doc_id, vector)


def ids_gained_text(backup_path: str) -> list[int]:
    """Ids that had no text in the backup but have text now, i.e. the ones this script filled."""
    uri = Path(backup_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as bak:
        before = {r[0] for r in bak.execute(
            "SELECT id FROM documents WHERE length(coalesce(extracted_text,'')) <= ?", (MIN_TEXT,))}
    with _db._conn() as conn:
        now = {r[0] for r in conn.execute(
            "SELECT id FROM documents WHERE length(coalesce(extracted_text,'')) > ?", (MIN_TEXT,))}
    return sorted(before & now)


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
    ap.add_argument("--repair-from-backup", metavar="BACKUP_DB",
                    help="no OCR: re-chunk and re-embed the documents that gained text since BACKUP_DB "
                         "(repairs the vectors of documents whose embedding step failed); lists them without --apply")
    ap.add_argument("--dates-from-filenames", action="store_true",
                    help="no OCR: set an empty document_date from a date in the filename (d.m.yy, YYYYMMDD, ...) "
                         "for ALL documents; lists them without --apply")
    ap.add_argument("--retry-missing", action="store_true",
                    help="also retry documents whose PDF was not found (404) in an earlier --apply run "
                         "(they are otherwise skipped; list kept in <db>.missing-ids.txt)")
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--lang", default=os.getenv("TESSERACT_LANGS", "deu+eng"))
    args = ap.parse_args()

    if args.db:
        _db.set_db_path(args.db)
    _db.init_db()

    if args.dates_from_filenames:
        fill_dates_from_filenames(args)
        return

    api_key = os.getenv("OPENAI_API_KEY", "")
    if args.repair_from_backup:
        ids = ids_gained_text(args.repair_from_backup)
        print(f"{'APPLY' if args.apply else 'DRY RUN (nothing is written)'}: "
              f"{len(ids)} documents gained text since the backup: {ids}")
        if args.apply:
            if not api_key:
                sys.exit("OPENAI_API_KEY not set.")
            failed = 0
            for doc_id in ids:
                try:
                    refresh_vectors(doc_id, api_key)
                except Exception as e:
                    print(f"id={doc_id}: ERROR {e}")
                    failed += 1
            print(f"Done: {len(ids) - failed} repaired, {failed} errors.")
        else:
            print("Re-run with --apply to repair.")
        return

    renamer = None
    if not args.no_ai:
        if not api_key:
            sys.exit("OPENAI_API_KEY not set. Use --no-ai for OCR + text only.")
        from ai_renamer import AIRenamer
        renamer = AIRenamer()

    docs = select_documents(args.ids)
    skipped_ids = [d for d in docs if not args.include_ids and is_id_document(d)]
    missing = set() if args.retry_missing else load_missing()
    skipped_missing = [d for d in docs if d["id"] in missing and d not in skipped_ids]
    todo = [d for d in docs if d not in skipped_ids and d not in skipped_missing]
    if args.limit:
        todo = todo[:args.limit]  # limit counts only documents that will actually be processed
    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"{mode}: {len(todo)} documents to OCR, {len(skipped_ids)} identity documents skipped"
          + (f", {len(skipped_missing)} skipped as not found earlier (--retry-missing to retry)" if skipped_missing else "")
          + ".")
    for d in skipped_ids:
        print(f"  skip id={d['id']} {d['new_filename']!r}")
    if not todo:
        return

    if args.apply and not args.no_backup:
        print(f"Backup written: {backup_db()}")
    elif args.apply:
        print("WARNING: no backup (--no-backup).")

    fetcher = Fetcher(args)
    stats = dict(done=0, no_text=0, errors=0, embed_errors=0, pages=0, text_layer=0, filled=0)
    newly_missing: set[int] = set()
    for n, doc in enumerate(todo, 1):
        label = f"[{n}/{len(todo)}] id={doc['id']} {doc['new_filename']!r}"
        try:
            text, pages, method = extract_text(fetcher.fetch(doc["onedrive_path"]), args)
            stats["pages"] += pages
            if len(text) <= MIN_TEXT:
                from_name = date_from_filename(doc["new_filename"] or "") if _empty(doc.get("document_date")) else None
                print(f"{label}: no readable text ({pages} p.)" + (f" -> date from filename {from_name}" if from_name else ""))
                if from_name and args.apply:
                    set_date(doc["id"], from_name)
                stats["no_text"] += 1
                continue
            meta = renamer.analyze_document(text, doc["new_filename"]) if renamer else {}
            fields = propose_fields(doc, meta, text)  # with --no-ai only the filename date can apply
            print(f"{label}: {method}, {pages} p., {len(text)} chars"
                  + (f" -> fill {', '.join(f'{k}={v!r}' for k, v in fields.items())}" if fields else ""))
            if args.apply:
                write_document(doc["id"], text, fields)
                if renamer and not args.no_embed:
                    try:
                        refresh_vectors(doc["id"], api_key)
                    except Exception as e:  # text and fields are saved; only the vectors are missing
                        print(f"{label}: WARNING text saved, embedding failed: {e}  "
                              f"(fix later with --repair-from-backup)")
                        stats["embed_errors"] += 1
            stats["done"] += 1
            stats["text_layer"] += method == "text-layer"
            stats["filled"] += bool(fields)
        except Exception as e:  # keep going; one bad PDF must not stop the batch
            print(f"{label}: ERROR {e}")
            stats["errors"] += 1
            if isinstance(e, MissingPDF):
                newly_missing.add(doc["id"])

    if newly_missing and args.apply:
        save_missing(load_missing() | newly_missing)
    if newly_missing:
        print(f"\nPDF not found at the stored OneDrive path: {sorted(newly_missing)}"
              + (f" (recorded in {_missing_file()}; skipped from now on)" if args.apply else
                 " (will be recorded and skipped after an --apply run)"))
    print(
        f"\nDone ({'applied' if args.apply else 'dry run'}): {stats['done']} with text "
        f"({stats['text_layer']} from text layer), {stats['filled']} with filled fields, "
        f"{stats['no_text']} without readable text, {stats['errors']} errors, "
        f"{stats['embed_errors']} embedding warnings, {stats['pages']} pages."
    )
    if not args.apply:
        print("Nothing was written. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
