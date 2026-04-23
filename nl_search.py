"""
Shared natural-language search logic used by telegram_bot.py and web/app.py.
"""
from __future__ import annotations

import json as _json
from typing import Any


_INTENT_SYSTEM = (
    "You extract search intent from a question about a personal document archive. "
    "Known document types in the archive: {known_types}. "
    "The conversation history is provided so you can resolve references like "
    "'Und von KPT?', 'die gleichen aber von 2024', 'Wie viel war das insgesamt?' etc. "
    "Use the previous questions and answers to understand what the current question refers to. "
    "Return JSON with these fields:\n"
    '{{"is_document_query": true|false,\n'
    ' "document_type": "<exact type from known list or null>",\n'
    ' "sender": "<company or person name or null>",\n'
    ' "year": "<4-digit year or null>",\n'
    ' "keywords": "<1-3 key terms for semantic search, or null>",\n'
    ' "sort": "date_desc" | "date_asc" | "relevance",\n'
    ' "limit": <1-20, default 10>}}\n'
    "IMPORTANT: is_document_query must be TRUE for virtually every question — "
    "including questions in German, questions about amounts/costs/dates, "
    "questions about specific companies or senders, and any follow-up or reference "
    "to a previous exchange. "
    "Only set is_document_query=false for pure greetings with no document intent (e.g. 'Hello', 'How are you'). "
    "Use sort=date_desc + limit=1 for 'latest/most recent X'. "
    "Use sort=date_asc for oldest. "
    "Use limit=20 for aggregation questions (total, sum, how much overall, how many in total, all from X). "
    "Default limit=10 for listing questions. "
    "Only return JSON, no other text."
)

_ANSWER_SYSTEM = (
    "Du bist ein persönlicher Assistent für Meisters Dokumentenablage. "
    "Beantworte Fragen auf Deutsch, präzise und freundlich. "
    "Die Ablage enthält insgesamt {total} Dokumente. "
    "Du hast Zugang zum bisherigen Gesprächsverlauf — nutze ihn, um Folgefragen "
    "('Und von KPT?', 'Wie viel war das insgesamt?', 'Zeig mehr davon') "
    "kohärent zu beantworten und frühere Ergebnisse zu referenzieren. "
    "Die folgenden Dokumente wurden für die aktuelle Frage abgerufen; "
    "der 'Inhalt'-Abschnitt enthält den relevantesten Textausschnitt. "
    "Nutze diesen Inhalt, um inhaltliche Fragen (z.B. Preise, Beträge, Daten, Klauseln) zu beantworten. "
    "Bei Summenfragen addiere die Beträge aus allen Dokumenten und nenne die Summe. "
    "Antworte im folgenden JSON-Format:\n"
    '{{"answer": "<deine Antwort auf Deutsch>", "ids": [<IDs der relevanten Dokumente>]}}\n'
    "Gib nur JSON zurück, kein weiterer Text."
)

_MAX_HISTORY = 6  # max messages (= 3 exchanges) to include for context


def run(
    question: str,
    openai_api_key: str,
    db: Any,
    nl_max_distance: float = 1.05,
    history: list[dict] | None = None,
) -> dict:
    """
    Execute a natural-language archive query.

    history – list of {role, content} dicts from previous exchanges
              (last _MAX_HISTORY messages are used for context)

    Returns a dict:
        is_document_query  – False when the question isn't about documents
        answer             – German prose answer from GPT
        rows               – retrieved document dicts
        referenced_ids     – doc IDs GPT cited in its answer
        chunk_context      – {doc_id: chunk_text} for snippet display
        stats              – raw DB statistics dict
    """
    from openai import OpenAI
    from embed import get_embedding
    import cost_tracker

    client = OpenAI(api_key=openai_api_key)
    stats = db.get_statistics()
    known_types = (
        ", ".join(t for t, _ in (stats.get("by_type") or []) if t)
        or "invoice, insurance, tax, contract, quote"
    )

    # Trim history to last N messages
    recent_history: list[dict] = (history or [])[-_MAX_HISTORY:]

    # ── Step 1: extract structured query intent ───────────────────────────────
    intent_messages = [
        {"role": "system", "content": _INTENT_SYSTEM.format(known_types=known_types)},
        *recent_history,
        {"role": "user", "content": question},
    ]
    intent_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=intent_messages,
        max_tokens=150,
        temperature=0,
        response_format={"type": "json_object"},
    )
    cost_tracker.log("gpt-4o-mini", "nl_intent", intent_resp.usage)
    try:
        intent: dict = _json.loads(intent_resp.choices[0].message.content)
    except Exception:
        intent = {}

    if not intent.get("is_document_query", True):
        return {
            "is_document_query": False,
            "answer": (
                "Ich bin dein Assistent für die Dokumentenablage. "
                "Stelle mir Fragen zu deinen Dokumenten — "
                "z.B. nach Rechnungen, Verträgen oder Absendern."
            ),
            "rows": [],
            "referenced_ids": [],
            "chunk_context": {},
            "stats": stats,
        }

    doc_type = intent.get("document_type") or None
    sender   = intent.get("sender") or None
    year     = intent.get("year") or None
    _kw      = intent.get("keywords")
    keywords = " ".join(_kw) if isinstance(_kw, list) else (_kw or None)
    sort     = intent.get("sort", "relevance")
    limit    = max(1, min(int(intent.get("limit") or 10), 20))

    sort_by    = "document_date" if sort in ("date_desc", "date_asc") else "scan_timestamp"
    sort_order = "asc" if sort == "date_asc" else "desc"

    print(
        f"NL intent : type={doc_type!r} sender={sender!r} year={year!r} "
        f"keywords={keywords!r} sort={sort} limit={limit}"
    )

    # ── Step 2: retrieve candidates ───────────────────────────────────────────
    rows: list[dict]        = []
    seen_ids: set[int]      = set()
    chunk_context: dict[int, str] = {}

    question_vec = get_embedding(question, openai_api_key)

    # For aggregation queries (limit=20) with structured filters, prioritise the
    # deterministic DB query first so that unrelated semantic hits don't crowd
    # out the actually-matching documents and produce inconsistent sums.
    is_aggregation = limit >= 15 and (doc_type or sender or year or keywords)

    def _add_db_rows() -> None:
        db_rows, _ = db.search_documents(
            query=keywords,
            document_type=doc_type,
            sender=sender,
            year=year,
            per_page=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        for r in db_rows:
            if r["id"] not in seen_ids:
                rows.append(r)
                seen_ids.add(r["id"])

    def _add_chunk_rows(k: int) -> None:
        for doc_id, _chunk_id, _dist, chunk_text in db.search_by_chunk_embedding(
            question_vec, k=k, max_distance=nl_max_distance
        ):
            if doc_id not in seen_ids:
                doc = db.get_document(doc_id)
                if doc:
                    rows.append(doc)
                    seen_ids.add(doc_id)
            chunk_context.setdefault(doc_id, chunk_text)

    if is_aggregation:
        # A. Structured DB first (deterministic, respects filters)
        _add_db_rows()
        # B. Semantic only to fill remaining slots
        if len(rows) < limit:
            _add_chunk_rows(limit - len(rows))
    else:
        # A. Chunk-level semantic search first (better for relevance queries)
        _add_chunk_rows(limit)
        # B. Structured DB to fill gaps
        _add_db_rows()

    # C. Doc-level vector fallback
    if len(rows) < limit:
        for doc_id, _ in db.search_by_embedding(
            question_vec, k=limit, max_distance=nl_max_distance
        ):
            if doc_id not in seen_ids:
                doc = db.get_document(doc_id)
                if doc:
                    rows.append(doc)
                    seen_ids.add(doc_id)

    if sort in ("date_desc", "date_asc"):
        rows.sort(
            key=lambda d: d.get("document_date") or "",
            reverse=(sort == "date_desc"),
        )
    rows = rows[:limit]

    for doc in rows:
        if doc["id"] not in chunk_context:
            chunk_context[doc["id"]] = db.get_best_chunk_for_doc(doc["id"], question_vec)

    # ── Step 3: answer with GPT (conversation-aware) ──────────────────────────
    doc_lines = []
    for doc in rows:
        parts = [f"[ID:{doc['id']}] {doc.get('new_filename', '')}"]
        if doc.get("document_type"):
            parts.append(f"Typ: {doc['document_type']}")
        if doc.get("document_date"):
            parts.append(f"Datum: {doc['document_date']}")
        if doc.get("company"):
            parts.append(f"Firma: {doc['company']}")
        if doc.get("sender"):
            parts.append(f"Absender: {doc['sender']}")
        ctx = chunk_context.get(doc["id"])
        if ctx:
            parts.append(f"Inhalt: {ctx[:1200]}")
        doc_lines.append(" | ".join(parts))
    context_text = "\n\n".join(doc_lines) if doc_lines else "Keine passenden Dokumente gefunden."

    answer_messages = [
        {"role": "system", "content": _ANSWER_SYSTEM.format(total=stats["total"])},
        *recent_history,
        {"role": "user", "content": f"Frage: {question}\n\nDokumente:\n{context_text}"},
    ]
    answer_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=answer_messages,
        max_tokens=600,
        temperature=0,
        response_format={"type": "json_object"},
    )
    cost_tracker.log("gpt-4o-mini", "nl_answer", answer_resp.usage)

    try:
        gpt_result     = _json.loads(answer_resp.choices[0].message.content)
        answer_text    = gpt_result.get("answer", "")
        referenced_ids = [int(i) for i in gpt_result.get("ids", [])]
    except Exception:
        answer_text    = answer_resp.choices[0].message.content
        referenced_ids = [doc["id"] for doc in rows]

    return {
        "is_document_query": True,
        "answer": answer_text,
        "rows": rows,
        "referenced_ids": referenced_ids,
        "chunk_context": chunk_context,
        "stats": stats,
    }
