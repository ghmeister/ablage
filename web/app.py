"""
Flask web UI for browsing and searching the PDF document archive.

Run locally:
    DB_PATH=./documents.db flask --app web/app.py run --debug --port 5000

Or from this file's directory:
    DB_PATH=../documents.db python app.py
"""
from __future__ import annotations

import os
import sys
from math import ceil
from pathlib import Path
from urllib.parse import quote_plus

# Allow importing db.py from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import db as db_module
from flask import Flask, abort, render_template, request, send_file, url_for

_archive_root_env = os.getenv("ARCHIVE_ROOT", "").strip()
_ARCHIVE_ROOT = Path(_archive_root_env) if _archive_root_env else None

app = Flask(__name__)

# German labels for document types
TYPE_LABELS: dict[str, str] = {
    "invoice": "Rechnung",
    "insurance": "Versicherung",
    "tax": "Steuer",
    "medical_report": "Arztbericht",
    "bank_statement": "Kontoauszug",
    "contract": "Vertrag",
    "warranty": "Garantie",
    "id_document": "Ausweis",
    "certificate": "Zeugnis/Diplom",
    "letter": "Brief",
    "quote": "Offerte",
    "other": "Sonstiges",
}

# Bootstrap 5 badge colours per type
TYPE_COLORS: dict[str, str] = {
    "invoice": "danger",
    "insurance": "primary",
    "tax": "warning text-dark",
    "medical_report": "success",
    "bank_statement": "info text-dark",
    "contract": "secondary",
    "warranty": "light text-dark border",
    "id_document": "dark",
    "certificate": "primary",
    "letter": "secondary",
    "quote": "warning text-dark",
    "other": "secondary",
}

_PER_PAGE = 25


@app.before_request
def _ensure_db() -> None:
    db_module.init_db()


@app.template_filter("type_label")
def type_label_filter(value: str) -> str:
    return TYPE_LABELS.get(value or "", value or "–")


@app.template_filter("type_color")
def type_color_filter(value: str) -> str:
    return TYPE_COLORS.get(value or "", "secondary")


@app.template_filter("qp")
def qp_filter(value: str) -> str:
    return quote_plus(str(value or ""))


@app.route("/")
def index() -> str:
    query = request.args.get("q", "").strip()
    doc_type = request.args.get("type", "").strip()
    year = request.args.get("year", "").strip()
    sender = request.args.get("sender", "").strip()
    page = max(1, int(request.args.get("page", "1") or "1"))
    sort_by = request.args.get("sort", "scan_timestamp").strip()
    sort_order = request.args.get("order", "desc").strip()

    try:
        rows, total = db_module.search_documents(
            query=query or None,
            document_type=doc_type or None,
            year=year or None,
            sender=sender or None,
            page=page,
            per_page=_PER_PAGE,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except Exception as e:
        app.logger.warning(f"Search error: {e}")
        rows, total = [], 0

    total_pages = max(1, ceil(total / _PER_PAGE))

    return render_template(
        "index.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        query=query,
        doc_type=doc_type,
        year=year,
        sender=sender,
        doc_types=db_module.get_distinct_values("document_type"),
        years=db_module.get_distinct_years(),
        senders=db_module.get_distinct_values("sender"),
        type_labels=TYPE_LABELS,
        type_colors=TYPE_COLORS,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@app.route("/document/<int:doc_id>")
def document(doc_id: int) -> str:
    doc = db_module.get_document(doc_id)
    if doc is None:
        abort(404)
    can_open = bool(
        _ARCHIVE_ROOT is not None
        and doc.get("onedrive_path")
        and (_ARCHIVE_ROOT / doc["onedrive_path"]).is_file()
    )
    return render_template("document.html", doc=doc, can_open=can_open)


@app.route("/pdf/<int:doc_id>")
def open_pdf(doc_id: int):
    if _ARCHIVE_ROOT is None:
        abort(404, "ARCHIVE_ROOT not configured")
    doc = db_module.get_document(doc_id)
    if doc is None:
        abort(404)
    pdf_path = _ARCHIVE_ROOT / doc["onedrive_path"]
    if not pdf_path.is_file():
        abort(404, f"File not found: {pdf_path}")
    return send_file(str(pdf_path), mimetype="application/pdf")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
