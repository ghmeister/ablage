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
from flask import Flask, abort, jsonify, render_template, request, send_file, url_for

_archive_root_env = os.getenv("ARCHIVE_ROOT", "").strip()
_ARCHIVE_ROOT = Path(_archive_root_env) if _archive_root_env else None

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Lazy Graph + classifier (optional — requires Graph env vars in container)
# ---------------------------------------------------------------------------
_graph_client = None
_folder_classifier = None


def _get_graph():
    global _graph_client
    if _graph_client is None:
        tenant_id = os.getenv("TENANT_ID")
        client_id = os.getenv("CLIENT_ID")
        if not (tenant_id and client_id):
            return None
        client_secret = os.getenv("CLIENT_SECRET") or None
        user_id = os.getenv("USER_ID") or None
        from graph_client import GraphClient
        _graph_client = GraphClient(
            tenant_id, client_id, client_secret=client_secret, user_id=user_id
        )
    return _graph_client


def _get_classifier():
    global _folder_classifier
    if _folder_classifier is None:
        if not os.getenv("OUTPUT_BASE_FOLDER", "").strip():
            return None
        rules_file = os.getenv("CLASSIFICATION_RULES_FILE", "classification_rules.yaml")
        from folder_classifier import FolderClassifier
        _folder_classifier = FolderClassifier(rules_file)
    return _folder_classifier


def _local_onedrive_path(onedrive_path: str) -> str:
    """
    Strip OUTPUT_BASE_FOLDER prefix for local file access via ARCHIVE_ROOT.

    The bot stores full drive-root paths ('Scanbot/Ablage/Versicherung/2026/file.pdf')
    but ARCHIVE_ROOT is already mounted at 'Scanbot/Ablage', so the prefix must be
    removed before joining with ARCHIVE_ROOT.
    """
    archive_root = os.getenv("OUTPUT_BASE_FOLDER", "").strip("/\\")
    if archive_root and onedrive_path.startswith(archive_root + "/"):
        return onedrive_path[len(archive_root) + 1:]
    return onedrive_path


def _full_onedrive_path(onedrive_path: str) -> str:
    """
    Ensure the path is relative to the drive root, not just the archive root.

    index_existing.py stores paths relative to the archive root
    (e.g. 'Versicherung/2016/file.pdf'), while the bot stores the full
    path from the drive root (e.g. 'Scanbot/Ablage/Versicherung/2016/file.pdf').
    Prepend OUTPUT_BASE_FOLDER when it is missing.
    """
    archive_root = os.getenv("OUTPUT_BASE_FOLDER", "").strip("/\\")
    if archive_root and not onedrive_path.startswith(archive_root + "/"):
        return f"{archive_root}/{onedrive_path}"
    return onedrive_path

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
    local_path = _local_onedrive_path(doc.get("onedrive_path") or "")
    can_open = bool(
        _ARCHIVE_ROOT is not None
        and local_path
        and (_ARCHIVE_ROOT / local_path).is_file()
    )
    graph_enabled = bool(os.getenv("TENANT_ID") and os.getenv("CLIENT_ID"))
    return render_template(
        "document.html",
        doc=doc,
        can_open=can_open,
        graph_enabled=graph_enabled,
        type_labels=TYPE_LABELS,
        type_colors=TYPE_COLORS,
    )


@app.route("/api/documents/<int:doc_id>/reclassify", methods=["POST"])
def reclassify(doc_id: int):
    data = request.get_json(force=True) or {}
    new_type = (data.get("document_type") or "").strip()
    if not new_type:
        return jsonify({"error": "document_type required"}), 400

    doc = db_module.get_document(doc_id)
    if doc is None:
        return jsonify({"error": "not found"}), 404

    if not doc.get("onedrive_path"):
        return jsonify({"error": "No OneDrive path stored for this document"}), 422

    graph = _get_graph()
    classifier = _get_classifier()
    if graph is None or classifier is None:
        return jsonify({"error": "Graph API not configured on this server"}), 503

    try:
        item = graph.get_item_by_path(_full_onedrive_path(doc["onedrive_path"]))
        item_id = item["id"]
        current_filename = item["name"]

        metadata = {
            "document_type": new_type,
            "date": doc.get("document_date"),
            "company": doc.get("company"),
            "keywords": [k.strip() for k in (doc.get("keywords") or "").split(",") if k.strip()],
        }
        folder, year, matched_rule = classifier.build_destination_path(metadata)
        archive_root = os.getenv("OUTPUT_BASE_FOLDER", "").strip("/\\")
        rel_path = f"{folder}/{year}" if year else folder
        full_path = "/".join(filter(None, [archive_root, rel_path]))

        dest_parent_id = graph.ensure_folder_path(full_path)
        result = graph.move_and_rename(item_id, current_filename, dest_parent_id)
        final_name = result.get("name", current_filename)
        new_onedrive_path = f"{full_path}/{final_name}"

        db_module.update_document(
            doc_id,
            document_type=new_type,
            destination_folder=full_path,
            onedrive_path=new_onedrive_path,
            matched_rule=matched_rule,
        )

        return jsonify({
            "document_type": new_type,
            "destination_folder": full_path,
            "matched_rule": matched_rule,
            "type_label": TYPE_LABELS.get(new_type, new_type),
            "type_color": TYPE_COLORS.get(new_type, "secondary"),
        })
    except Exception as e:
        app.logger.error(f"Reclassify {doc_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/documents/<int:doc_id>/rename", methods=["POST"])
def rename_document(doc_id: int):
    data = request.get_json(force=True) or {}
    new_name = (data.get("new_filename") or "").strip()
    if not new_name:
        return jsonify({"error": "new_filename required"}), 400

    # Ensure .pdf extension for OneDrive; strip it for DB storage (template appends .pdf)
    if new_name.lower().endswith(".pdf"):
        new_stem = new_name[:-4]
        new_name_with_ext = new_name
    else:
        new_stem = new_name
        new_name_with_ext = new_name + ".pdf"

    doc = db_module.get_document(doc_id)
    if doc is None:
        return jsonify({"error": "not found"}), 404

    if not doc.get("onedrive_path"):
        return jsonify({"error": "No OneDrive path stored for this document"}), 422

    graph = _get_graph()
    if graph is None:
        return jsonify({"error": "Graph API not configured on this server"}), 503

    try:
        item = graph.get_item_by_path(_full_onedrive_path(doc["onedrive_path"]))
        item_id = item["id"]
        parent_id = item["parentReference"]["id"]

        result = graph.move_and_rename(item_id, new_name_with_ext, parent_id)
        final_name = result.get("name", new_name_with_ext)
        final_stem = final_name[:-4] if final_name.lower().endswith(".pdf") else final_name

        old_path = doc["onedrive_path"]
        parent_path = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
        new_onedrive_path = f"{parent_path}/{final_name}" if parent_path else final_name

        db_module.update_document(doc_id, new_filename=final_stem, onedrive_path=new_onedrive_path)

        return jsonify({"new_filename": final_stem, "onedrive_path": new_onedrive_path})
    except Exception as e:
        app.logger.error(f"Rename {doc_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/pdf/<int:doc_id>")
def open_pdf(doc_id: int):
    if _ARCHIVE_ROOT is None:
        abort(404, "ARCHIVE_ROOT not configured")
    doc = db_module.get_document(doc_id)
    if doc is None:
        abort(404)
    pdf_path = _ARCHIVE_ROOT / _local_onedrive_path(doc["onedrive_path"])
    if not pdf_path.is_file():
        abort(404, f"File not found: {pdf_path}")
    return send_file(str(pdf_path), mimetype="application/pdf")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
