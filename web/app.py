"""
Flask web UI for browsing and searching the PDF document archive.

Run locally:
    DB_PATH=./documents.db flask --app web/app.py run --debug --port 5000

Or from this file's directory:
    DB_PATH=../documents.db python app.py
"""
from __future__ import annotations

import json
import os
import sys
from math import ceil
from pathlib import Path
from urllib.parse import quote_plus

import yaml

# Allow importing db.py from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import db as db_module
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for, Response

_archive_root_env = os.getenv("ARCHIVE_ROOT", "").strip()
_ARCHIVE_ROOT = Path(_archive_root_env).resolve() if _archive_root_env else None


def _sanitize_filename(filename: str) -> str:
    filename = filename.strip('"').strip("'")
    replacements = {' ': '_', '/': '-', '\\': '-', ':': '-',
                    '*': '', '?': '', '"': '', '<': '', '>': '', '|': '-'}
    for old, new in replacements.items():
        filename = filename.replace(old, new)
    return filename.strip('_-.')

_data_dir = Path(os.getenv("DB_PATH", "/data/documents.db")).parent
_STATUS_FILE = _data_dir / "bot_status.json"
_LOG_FILE    = _data_dir / "bot.log"

app = Flask(__name__)
_secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
if _secret_key == "dev-secret-change-me":
    import warnings
    warnings.warn("SECRET_KEY is set to the insecure default — set the SECRET_KEY env var in production!", stacklevel=1)
app.secret_key = _secret_key

_UI_USER = os.getenv("UI_USER", "").strip()
_UI_PASSWORD = os.getenv("UI_PASSWORD", "").strip()


@app.before_request
def _require_auth():
    if not (_UI_USER and _UI_PASSWORD):
        return  # auth not configured — allow all (dev/local)
    if request.endpoint in ("login", "static"):
        return
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == _UI_USER
                and request.form.get("password") == _UI_PASSWORD):
            session.permanent = True
            session["logged_in"] = True
            return redirect(request.args.get("next") or "/")
        error = "Ungültige Zugangsdaten"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# Load type labels/colors from classification_rules.yaml (single source of truth)
# ---------------------------------------------------------------------------
def _load_type_ui() -> tuple[dict[str, str], dict[str, str]]:
    rules_file = os.getenv("CLASSIFICATION_RULES_FILE", "classification_rules.yaml")
    rules_path = Path(rules_file)
    if not rules_path.is_absolute():
        rules_path = Path(__file__).parent.parent / rules_file
    if rules_path.exists():
        with open(rules_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("type_labels", {}), cfg.get("type_colors", {})
    return {}, {}

TYPE_LABELS, TYPE_COLORS = _load_type_ui()

_APP_VERSION = os.getenv("APP_VERSION", "dev")
_GIT_COMMIT  = os.getenv("GIT_COMMIT", "")[:7]

_PER_PAGE = 25

# ---------------------------------------------------------------------------
# Recipient normalization — maps verbose DB recipient strings to short first names
# ---------------------------------------------------------------------------
_FAMILY_FIRST_NAMES = ["Manuel", "Judith", "Clara", "Nora", "Dominik"]

def _normalize_recipient(raw: str | None) -> str:
    """Return comma-joined first names found in the recipient string, or the raw value.
    Matches case-insensitively so 'MANUEL OLIVER MEISTER' → 'Manuel'."""
    if not raw:
        return "–"
    words = raw.lower().split()
    found = [n for n in _FAMILY_FIRST_NAMES if n.lower() in words]
    if found:
        return ", ".join(found)
    # Fallback: truncate very long raw values
    return raw[:40] + ("…" if len(raw) > 40 else "")

app.jinja_env.filters["recipient"] = _normalize_recipient


@app.context_processor
def inject_globals():
    return {"app_version": _APP_VERSION, "git_commit": _GIT_COMMIT}

# ---------------------------------------------------------------------------
# One-time DB init at startup
# ---------------------------------------------------------------------------
with app.app_context():
    db_module.init_db()

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
    onedrive_path = onedrive_path.lstrip("/")
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


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

@app.template_filter("type_label")
def type_label_filter(value: str) -> str:
    return TYPE_LABELS.get(value or "", value or "–")


@app.template_filter("type_color")
def type_color_filter(value: str) -> str:
    return TYPE_COLORS.get(value or "", "secondary")


@app.template_filter("qp")
def qp_filter(value: str) -> str:
    return quote_plus(str(value or ""))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index() -> str:
    query = request.args.get("q", "").strip()
    doc_type = request.args.get("type", "").strip()
    year = request.args.get("year", "").strip()
    sender = request.args.get("sender", "").strip()
    recipient = request.args.get("recipient", "").strip()
    sort_by = request.args.get("sort", "scan_timestamp").strip()
    sort_order = request.args.get("order", "desc").strip()
    try:
        page = max(1, int(request.args.get("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1

    try:
        rows, total = db_module.search_documents(
            query=query or None,
            document_type=doc_type or None,
            year=year or None,
            sender=sender or None,
            recipient=recipient or None,
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
        recipient=recipient,
        doc_types=db_module.get_distinct_values("document_type"),
        years=db_module.get_distinct_years(),
        senders=db_module.get_distinct_values("sender"),
        recipients=db_module.get_distinct_values("recipient"),
        type_labels=TYPE_LABELS,
        type_colors=TYPE_COLORS,
        sort_by=sort_by,
        sort_order=sort_order,
        archive_configured=_ARCHIVE_ROOT is not None,
        graph_enabled=bool(_get_graph()),
        unseen_count=db_module.get_unseen_count(),
    )


@app.route("/document/<int:doc_id>")
def document(doc_id: int) -> str:
    doc = db_module.get_document(doc_id)
    if doc is None:
        abort(404)
    graph_enabled = bool(os.getenv("TENANT_ID") and os.getenv("CLIENT_ID"))
    local_path = _local_onedrive_path(doc.get("onedrive_path") or "")
    try:
        local_file_exists = bool(local_path and _ARCHIVE_ROOT and (_ARCHIVE_ROOT / local_path).is_file())
    except PermissionError:
        local_file_exists = False
    can_open = local_file_exists or bool(graph_enabled and doc.get("onedrive_path"))
    # Build back URL from search params passed in the URL, fall back to referrer
    # Explicit back param takes priority (e.g. ?back=/duplicates from duplicate alert link)
    explicit_back = request.args.get("back", "").strip()
    _back_args = {k: request.args.get(k, "") for k in ("q", "type", "year", "sender", "recipient", "sort", "order", "page")}
    if explicit_back:
        back_url = explicit_back
    elif any(_back_args.values()):
        back_url = "/?" + "&".join(f"{k}={quote_plus(v)}" for k, v in _back_args.items() if v)
    else:
        back_url = request.referrer or "/"
    duplicate_id = None
    if doc.get("content_hash"):
        duplicate_id = db_module.find_duplicate_by_hash(doc["content_hash"], exclude_id=doc_id)

    return render_template(
        "document.html",
        doc=doc,
        can_open=can_open,
        local_file_exists=local_file_exists,
        graph_enabled=graph_enabled,
        type_labels=TYPE_LABELS,
        type_colors=TYPE_COLORS,
        back_url=back_url,
        duplicate_id=duplicate_id,
    )


@app.route("/ki")
def ki():
    return render_template("ki.html", type_labels=TYPE_LABELS, type_colors=TYPE_COLORS)


@app.route("/api/nl-search", methods=["POST"])
def nl_search():
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_api_key:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 503

    data = request.get_json() or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    history = [
        {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
        for m in (data.get("history") or [])
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import nl_search as _nl

        result = _nl.run(
            question=question,
            openai_api_key=openai_api_key,
            db=db_module,
            nl_max_distance=float(os.getenv("NL_MAX_DISTANCE", "1.05")),
            history=history,
        )

        id_to_doc     = {doc["id"]: doc for doc in result["rows"]}
        chunk_context = result["chunk_context"]
        linked_docs = [
            {
                "id": id_to_doc[i]["id"],
                "new_filename": id_to_doc[i].get("new_filename", ""),
                "document_type": id_to_doc[i].get("document_type", ""),
                "document_date": id_to_doc[i].get("document_date", ""),
                "sender": id_to_doc[i].get("sender") or id_to_doc[i].get("company") or "",
                "chunk_text": chunk_context.get(i, ""),
                "type_label": TYPE_LABELS.get(id_to_doc[i].get("document_type", ""), id_to_doc[i].get("document_type", "")),
                "type_color": TYPE_COLORS.get(id_to_doc[i].get("document_type", ""), "secondary"),
            }
            for i in result["referenced_ids"] if i in id_to_doc
        ]
        return jsonify({"answer": result["answer"], "documents": linked_docs})

    except Exception as e:
        app.logger.error(f"NL search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bot-status")
def bot_status():
    try:
        data = json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {"status": "unknown"}
    return jsonify(data)


@app.route("/logs")
def view_logs():
    lines = []
    if _LOG_FILE.exists():
        text = _LOG_FILE.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[-500:]  # last 500 lines
    return render_template("logs.html", lines=lines)


@app.route("/api/documents/<int:doc_id>/reclassify", methods=["POST"])
def reclassify(doc_id: int):
    data = request.get_json() or {}
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
            destination_folder=rel_path,
            onedrive_path=new_onedrive_path,
            matched_rule=matched_rule,
        )

        return jsonify({
            "document_type": new_type,
            "destination_folder": rel_path,
            "matched_rule": matched_rule,
            "type_label": TYPE_LABELS.get(new_type, new_type),
            "type_color": TYPE_COLORS.get(new_type, "secondary"),
        })
    except Exception as e:
        app.logger.error(f"Reclassify {doc_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/documents/<int:doc_id>/rename", methods=["POST"])
def rename_document(doc_id: int):
    data = request.get_json() or {}
    new_name = _sanitize_filename((data.get("new_filename") or "").strip())
    if not new_name:
        return jsonify({"error": "new_filename required"}), 400

    if new_name.lower().endswith(".pdf"):
        new_name_with_ext = new_name
    else:
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


@app.route("/api/documents/<int:doc_id>/metadata", methods=["POST"])
def update_metadata(doc_id: int):
    data = request.get_json() or {}
    allowed = {"document_date", "sender", "recipient"}
    updates = {k: (data.get(k) or "").strip() or None for k in allowed if k in data}
    if not updates:
        return jsonify({"error": "No valid fields provided"}), 400
    if db_module.get_document(doc_id) is None:
        return jsonify({"error": "not found"}), 404
    db_module.update_document(doc_id, **updates)
    return jsonify(updates)


@app.route("/duplicates")
def duplicates():
    groups = db_module.get_duplicate_groups()
    # Normalize onedrive_path for display: strip OUTPUT_BASE_FOLDER prefix so
    # old docs (relative path) and new docs (full path) show consistently
    archive_root = os.getenv("OUTPUT_BASE_FOLDER", "").strip("/\\")
    for group in groups:
        for doc in group:
            path = (doc.get("onedrive_path") or "").lstrip("/")
            if archive_root and path.startswith(archive_root + "/"):
                path = path[len(archive_root) + 1:]
            doc["_display_path"] = path
    return render_template("duplicates.html", groups=groups, type_labels=TYPE_LABELS)


@app.route("/steuern")
def steuern():
    year = request.args.get("year", "").strip()
    docs, years = db_module.get_tax_relevant_documents(year or None)
    # Group by document_type for display
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for doc in docs:
        groups[doc["document_type"] or "other"].append(doc)
    return render_template(
        "steuern.html",
        groups=dict(groups),
        years=years,
        year=year,
        total=len(docs),
        type_labels=TYPE_LABELS,
        type_colors=TYPE_COLORS,
        archive_configured=_ARCHIVE_ROOT is not None,
    )


@app.route("/api/documents/<int:doc_id>/tax-relevant", methods=["POST"])
def set_tax_relevant(doc_id: int):
    data = request.get_json() or {}
    value = bool(data.get("tax_relevant", False))
    if db_module.get_document(doc_id) is None:
        return jsonify({"error": "not found"}), 404
    db_module.update_document(doc_id, tax_relevant=1 if value else 0)
    return jsonify({"tax_relevant": value})


@app.route("/view/<int:doc_id>")
def view_pdf(doc_id: int):
    """HTML wrapper with back button — used by PWA to keep navigation working."""
    doc = db_module.get_document(doc_id)
    if doc is None:
        abort(404)
    return render_template("pdf_view.html", doc=doc)


@app.route("/api/documents/<int:doc_id>", methods=["DELETE"])
def delete_document(doc_id: int):
    doc = db_module.get_document(doc_id)
    if doc is None:
        return jsonify({"error": "not found"}), 404

    # Delete from OneDrive if Graph is available and path is known
    if doc.get("onedrive_path"):
        graph = _get_graph()
        if graph:
            try:
                item = graph.get_item_by_path(_full_onedrive_path(doc["onedrive_path"]))
                graph.delete_item(item["id"])
            except Exception as e:
                # 404/itemNotFound means the file is already gone — still clean up the DB record
                err = str(e)
                if "404" not in err and "itemNotFound" not in err:
                    app.logger.warning(f"OneDrive delete failed for {doc_id}: {e}")
                    return jsonify({"error": f"OneDrive delete failed: {e}"}), 500

    db_module.delete_document(doc_id)
    return jsonify({"deleted": doc_id})


@app.route("/api/documents/bulk-tax-relevant", methods=["POST"])
def bulk_tax_relevant():
    data = request.get_json() or {}
    ids = [int(i) for i in (data.get("ids") or []) if str(i).isdigit()]
    value = 1 if data.get("tax_relevant", True) else 0
    if not ids:
        return jsonify({"error": "ids required"}), 400
    for doc_id in ids:
        db_module.update_document(doc_id, tax_relevant=value)
    return jsonify({"updated": ids, "tax_relevant": bool(value)})


@app.route("/api/documents/<int:doc_id>/seen", methods=["POST"])
def mark_seen(doc_id: int):
    if db_module.get_document(doc_id) is None:
        return jsonify({"error": "not found"}), 404
    db_module.update_document(doc_id, seen=1)
    return jsonify({"seen": True})


@app.route("/api/documents/bulk-seen", methods=["POST"])
def bulk_seen():
    data = request.get_json() or {}
    ids = [int(i) for i in (data.get("ids") or []) if str(i).isdigit()]
    if not ids:
        return jsonify({"error": "ids required"}), 400
    for doc_id in ids:
        db_module.update_document(doc_id, seen=1)
    return jsonify({"updated": ids})


@app.route("/api/documents/bulk-delete", methods=["POST"])
def bulk_delete():
    data = request.get_json() or {}
    ids = [int(i) for i in (data.get("ids") or []) if str(i).isdigit()]
    if not ids:
        return jsonify({"error": "ids required"}), 400

    graph = _get_graph()
    errors = []
    deleted = []

    for doc_id in ids:
        doc = db_module.get_document(doc_id)
        if doc is None:
            continue
        if doc.get("onedrive_path") and graph:
            try:
                item = graph.get_item_by_path(_full_onedrive_path(doc["onedrive_path"]))
                graph.delete_item(item["id"])
            except Exception as e:
                errors.append({"id": doc_id, "error": str(e)})
                continue
        db_module.delete_document(doc_id)
        deleted.append(doc_id)

    return jsonify({"deleted": deleted, "errors": errors})


@app.route("/api/documents/<int:doc_id>/folder-url")
def folder_url(doc_id: int):
    """Return the OneDrive web URL of the folder containing the document."""
    doc = db_module.get_document(doc_id)
    if doc is None:
        return jsonify({"error": "not found"}), 404
    graph = _get_graph()
    if graph is None:
        return jsonify({"error": "Graph API not configured"}), 503
    if not doc.get("onedrive_path"):
        return jsonify({"error": "No OneDrive path stored"}), 422
    try:
        item = graph.get_item_by_path(_full_onedrive_path(doc["onedrive_path"]))
        return jsonify({"url": item.get("webUrl")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/documents/<int:doc_id>/download")
def download_pdf(doc_id: int):
    """Download a PDF from OneDrive via Graph API — used for sharing on iOS."""
    doc = db_module.get_document(doc_id)
    if doc is None:
        abort(404)
    graph = _get_graph()
    if graph is None:
        abort(503, "Graph API not configured")
    if not doc.get("onedrive_path"):
        abort(404, "No OneDrive path stored for this document")
    try:
        item = graph.get_item_by_path(_full_onedrive_path(doc["onedrive_path"]))
        content = graph.download_file(item["id"])
    except Exception as e:
        abort(502, f"OneDrive download failed: {e}")
    filename = (doc.get("new_filename") or "document") + ".pdf"
    return Response(
        content,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename=\"{filename}\""},
    )


@app.route("/pdf/<int:doc_id>")
def open_pdf(doc_id: int):
    if _ARCHIVE_ROOT is None:
        abort(404, "ARCHIVE_ROOT not configured")
    doc = db_module.get_document(doc_id)
    if doc is None:
        abort(404)
    # Resolve and verify path stays within ARCHIVE_ROOT (prevents path traversal)
    pdf_path = (_ARCHIVE_ROOT / _local_onedrive_path(doc["onedrive_path"])).resolve()
    try:
        pdf_path.relative_to(_ARCHIVE_ROOT)
    except ValueError:
        abort(403)
    try:
        if not pdf_path.is_file():
            abort(404)
    except PermissionError:
        abort(403, "No read access to archive — run: chmod -R o+rX <archive_path> on the host")
    return send_file(str(pdf_path), mimetype="application/pdf")


@app.route("/api/costs")
def api_costs():
    import cost_tracker
    return jsonify(cost_tracker.get_summary())


@app.route("/statistik")
def statistik():
    return render_template(
        "statistik.html",
        stats=db_module.get_archive_stats(),
        type_labels=TYPE_LABELS,
        type_colors=TYPE_COLORS,
    )


@app.route("/kosten")
def kosten():
    import cost_tracker
    return render_template(
        "kosten.html",
        summary=cost_tracker.get_summary(),
        daily=cost_tracker.get_daily_totals(60),
        monthly=cost_tracker.get_monthly_totals(),
        per_document=cost_tracker.get_per_document_costs(100),
        type_labels=TYPE_LABELS,
        type_colors=TYPE_COLORS,
    )


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
