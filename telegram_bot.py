"""
Telegram bot for Ablage — notifications, /statistik, /suche, duplicate alerts,
PDF forwarding, and natural-language document queries.

Required env vars:
    TELEGRAM_BOT_TOKEN   — from @BotFather
    TELEGRAM_CHAT_ID     — your personal chat ID
    ABLAGE_URL           — e.g. https://ablage.meisterm.dnsuser.de (for deep links)
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.parse
from typing import TYPE_CHECKING, Optional

import db as _db

if TYPE_CHECKING:
    from graph_client import GraphClient

_MD_SPECIAL = r'\_*[]()~`>#+-=|{}.!'


def _esc(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    for ch in _MD_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


class TelegramBot:
    def __init__(
        self,
        token: str,
        chat_id: str,
        ablage_url: str = "",
        graph: Optional["GraphClient"] = None,
        source_folder_id: str = "",
        openai_api_key: str = "",
        nl_max_distance: float = 0.75,
    ):
        self._token = token
        self._chat_id = str(chat_id)
        self._ablage_url = ablage_url.rstrip("/")
        self._graph = graph
        self._source_folder_id = source_folder_id
        self._openai_api_key = openai_api_key
        self._nl_max_distance = nl_max_distance
        self._offset = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_new_document(
        self,
        doc_id: int,
        filename: str,
        doc_type: Optional[str],
        doc_date: Optional[str],
        email_from: Optional[str] = None,
    ) -> None:
        type_str = _esc(doc_type or "Unbekannt")
        date_str = f" vom {_esc(doc_date)}" if doc_date else ""
        source_str = f"\n✉ Per E\-Mail von _{_esc(email_from)}_" if email_from else ""
        text = (
            f"📄 Ein neues Dokument vom Typ *{type_str}* wurde Meisters Ablage"
            f" hinzugefügt{date_str}:{source_str}\n_{_esc(filename)}_"
        )
        buttons = []
        if self._ablage_url:
            buttons.append({"text": "🔗 In Ablage öffnen", "url": f"{self._ablage_url}/document/{doc_id}"})
        buttons.append({"text": "★ Steuerrelevant", "callback_data": f"tax:{doc_id}:1"})
        self._send(self._chat_id, text, keyboard=[[b] for b in buttons])

    def notify_duplicate(
        self,
        new_doc_id: int,
        new_filename: str,
        existing_doc_id: int,
        existing_filename: str,
    ) -> None:
        text = (
            f"⚠️ *Mögliches Duplikat erkannt*\n\n"
            f"Neu archiviert: _{_esc(new_filename)}_\n"
            f"Bereits vorhanden: _{_esc(existing_filename)}_\n\n"
            f"Was soll mit dem neuen Dokument passieren?"
        )
        keyboard = [
            [{"text": "✓ Behalten", "callback_data": f"dup_keep:{new_doc_id}"}],
            [{"text": "🗑 Als Duplikat löschen", "callback_data": f"dup_del:{new_doc_id}"}],
        ]
        self._send(self._chat_id, text, keyboard=keyboard)

    def start_polling(self) -> None:
        t = threading.Thread(target=self._poll_loop, daemon=True, name="telegram-poll")
        t.start()

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    def _api(self, method: str, data: dict) -> dict:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _api_get(self, method: str, params: Optional[dict] = None) -> dict:
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}{qs}",
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _send(self, chat_id: str, text: str, keyboard: Optional[list] = None) -> None:
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            self._api("sendMessage", payload)
        except Exception as e:
            print(f"Warning   : Telegram sendMessage failed: {e}")

    def _send_plain(self, chat_id: str, text: str) -> None:
        """Send without Markdown parsing — safe for arbitrary content."""
        try:
            self._api("sendMessage", {"chat_id": chat_id, "text": text})
        except Exception as e:
            print(f"Warning   : Telegram sendMessage failed: {e}")

    def _edit_keyboard(self, chat_id: str, message_id: int, keyboard: list) -> None:
        try:
            self._api("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": keyboard},
            })
        except Exception:
            pass

    def _edit_text(self, chat_id: str, message_id: int, text: str) -> None:
        try:
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "MarkdownV2",
            })
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while True:
            try:
                result = self._api("getUpdates", {
                    "offset": self._offset,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                })
                for update in result.get("result", []):
                    self._offset = update["update_id"] + 1
                    try:
                        self._handle_update(update)
                    except Exception as e:
                        print(f"Warning   : Telegram update error: {e}")
            except Exception as e:
                print(f"Warning   : Telegram polling error: {e}")
                time.sleep(5)

    def _handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        elif "message" in update:
            self._handle_message(update["message"])

    # ------------------------------------------------------------------
    # Callback handler
    # ------------------------------------------------------------------

    def _handle_callback(self, cb: dict) -> None:
        data = cb.get("data", "")
        cb_id = cb["id"]
        msg = cb.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        message_id = msg.get("message_id")

        if data.startswith("tax:"):
            _, doc_id_str, value_str = data.split(":")
            doc_id, value = int(doc_id_str), int(value_str)
            try:
                _db.update_document(doc_id, tax_relevant=value)
                answer = "Als steuerrelevant markiert ✓" if value else "Steuerrelevanz entfernt"
                self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": answer})
                new_value = 1 - value
                new_label = "☆ Nicht steuerrelevant" if value else "★ Steuerrelevant"
                old_keyboard = msg.get("reply_markup", {}).get("inline_keyboard", [])
                new_keyboard = []
                for row in old_keyboard:
                    new_row = []
                    for btn in row:
                        if btn.get("callback_data", "").startswith("tax:"):
                            new_row.append({"text": new_label, "callback_data": f"tax:{doc_id}:{new_value}"})
                        else:
                            new_row.append(btn)
                    new_keyboard.append(new_row)
                if chat_id and message_id:
                    self._edit_keyboard(chat_id, message_id, new_keyboard)
            except Exception as e:
                print(f"Warning   : Tax toggle failed: {e}")
                self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Fehler aufgetreten"})

        elif data.startswith("dup_keep:"):
            self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Dokument behalten ✓"})
            if chat_id and message_id:
                self._edit_text(chat_id, message_id, "✓ Dokument wurde behalten\\.")

        elif data.startswith("dup_del:"):
            doc_id = int(data.split(":")[1])
            self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Wird gelöscht…"})
            try:
                doc = _db.get_document(doc_id)
                if doc and doc.get("onedrive_path") and self._graph:
                    item = self._graph.get_item_by_path(doc["onedrive_path"])
                    self._graph.delete_item(item["id"])
                if doc:
                    _db.delete_document(doc_id)
                if chat_id and message_id:
                    self._edit_text(chat_id, message_id, "🗑 Duplikat wurde gelöscht\\.")
            except Exception as e:
                print(f"Warning   : Duplicate delete failed: {e}")
                if chat_id and message_id:
                    self._edit_text(chat_id, message_id,
                                    f"⚠️ Fehler beim Löschen: {_esc(str(e))}")

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    def _handle_message(self, msg: dict) -> None:
        chat_id = str(msg.get("chat", {}).get("id", ""))

        # Only respond to the configured chat
        if chat_id != self._chat_id:
            return

        # PDF document forwarded to bot
        if msg.get("document"):
            self._handle_pdf(chat_id, msg["document"])
            return

        text = (msg.get("text") or "").strip()
        cmd = text.lower()

        if cmd.startswith("/suche"):
            query = text[6:].strip()
            if not query:
                self._send(chat_id, "Verwendung: `/suche Suchbegriff`")
                return
            try:
                rows, total = _db.search_documents(query=query, per_page=5)
            except Exception as e:
                self._send(chat_id, f"Fehler bei der Suche: {_esc(str(e))}")
                return

            if not rows:
                self._send(chat_id, f"Keine Dokumente für \"{_esc(query)}\" gefunden\.")
                return

            shown = len(rows)
            lines = [
                f"🔍 *{total} Treffer für \"{_esc(query)}\"*"
                + (f" \\(Top {shown}\\)" if total > shown else "") + ":"
            ]
            keyboard = []
            for doc in rows:
                name = doc["new_filename"] or "-"
                date = f" · {_esc(doc['document_date'])}" if doc.get("document_date") else ""
                lines.append(f"• _{_esc(name)}_{date}")
                if self._ablage_url:
                    keyboard.append([{"text": name[:60], "url": f"{self._ablage_url}/document/{doc['id']}"}])

            self._send(chat_id, "\n".join(lines), keyboard=keyboard or None)

        elif cmd.startswith("/statistik"):
            self._handle_statistik(chat_id)

        elif cmd.startswith("/start") or cmd.startswith("/hilfe"):
            self._send(chat_id, (
                "*Meisters Ablage Bot* 📄\n\n"
                "Ich benachrichtige dich über neue Dokumente und beantworte Fragen zur Ablage\\.\n\n"
                "*Befehle:*\n"
                "`/suche Begriff` — Dokumente suchen\n"
                "`/statistik` — Archiv\\-Übersicht\n"
                "`/hilfe` — diese Hilfe\n\n"
                "*PDF einreichen:* Schicke mir einfach eine PDF\\-Datei — sie landet automatisch in deiner Ablage\\.\n\n"
                "*Fragen stellen:* Schreibe eine normale Frage auf Deutsch, z\\.B\\. "
                "_\"Welche Rechnungen habe ich von 2025?\"_"
            ))

        elif text and not text.startswith("/"):
            self._handle_natural_language(chat_id, text)

    # ------------------------------------------------------------------
    # Feature handlers
    # ------------------------------------------------------------------

    def _handle_statistik(self, chat_id: str) -> None:
        stats = _db.get_statistics()
        lines = ["📊 *Ablage Statistik*\n"]
        lines.append(f"Dokumente gesamt: *{stats['total']}*")
        lines.append(f"Diesen Monat: *{stats['this_month']}*")
        lines.append(f"Letzten Monat: *{stats['last_month']}*")
        lines.append(f"Steuerrelevant: *{stats['tax_relevant']}*")
        lines.append(f"Per E\\-Mail eingegangen: *{stats['email_source']}*")
        if stats["by_type"]:
            lines.append("\n*Nach Dokumenttyp:*")
            for doc_type, cnt in stats["by_type"]:
                lines.append(f"  • {_esc(doc_type)}: {cnt}")
        self._send(chat_id, "\n".join(lines))

    def _handle_pdf(self, chat_id: str, document: dict) -> None:
        if not self._graph or not self._source_folder_id:
            self._send_plain(chat_id, "PDF-Empfang ist nicht konfiguriert.")
            return
        mime = document.get("mime_type", "")
        filename = document.get("file_name") or "dokument.pdf"
        if "pdf" not in mime.lower() and not filename.lower().endswith(".pdf"):
            self._send_plain(chat_id, "Bitte sende nur PDF-Dateien.")
            return
        file_size = document.get("file_size", 0)
        if file_size > 20 * 1024 * 1024:
            self._send_plain(chat_id, "Die Datei ist zu groß (max. 20 MB).")
            return

        self._send_plain(chat_id, f"📥 Empfange {filename} …")
        try:
            file_info = self._api_get("getFile", {"file_id": document["file_id"]})
            file_path = file_info["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
            with urllib.request.urlopen(download_url, timeout=60) as r:
                content = r.read()
            self._graph.upload_file(self._source_folder_id, filename, content)
            self._send_plain(
                chat_id,
                f"✓ {filename} wurde hochgeladen und wird in Kürze verarbeitet und archiviert.",
            )
        except Exception as e:
            print(f"Warning   : PDF upload failed: {e}")
            self._send_plain(chat_id, f"Fehler beim Upload: {e}")

    _NL_STOP_WORDS = {
        # English
        "i", "me", "my", "we", "you", "the", "a", "an", "is", "are", "was",
        "have", "has", "do", "did", "will", "would", "could", "should", "can",
        "and", "or", "but", "in", "on", "at", "to", "for", "of", "from",
        "with", "by", "when", "where", "what", "how", "who", "this", "that",
        "not", "all", "any", "some", "show", "find", "get", "give", "tell",
        "which", "last", "latest", "recent", "received", "sent", "got",
        # German pronouns / articles
        "ich", "mir", "mich", "wir", "uns", "sie", "ihr", "ihnen",
        "der", "die", "das", "dem", "den", "des", "ein", "eine", "einem", "einen", "eines",
        # German question words & determiners
        "wann", "wo", "wie", "was", "wer", "wen", "wem", "wessen",
        "welche", "welches", "welchen", "welchem", "welcher",
        "welch",
        # German verbs / auxiliaries
        "ist", "war", "sind", "waren", "hat", "hatte", "haben", "hatten",
        "bin", "wird", "wurde", "wurden", "werden",
        "und", "oder", "aber", "wenn", "denn", "weil", "dass",
        # German prepositions
        "in", "an", "auf", "zu", "von", "mit", "bei", "nach", "aus", "für",
        "durch", "über", "unter", "neben", "zwischen", "vor", "hinter",
        # German adverbs / question context
        "alle", "zeige", "zeig", "finde", "suche", "zeigen", "anzeigen", "auflisten", "liste",
        "habe", "hatte", "bekommen", "erhalten", "zuletzt", "letzte", "letzten", "letzter",
        "neueste", "neuesten", "neuester", "aktuell", "aktuellste", "aktuellsten",
        "erste", "ersten", "erster",
        # Document meta words
        "dokument", "dokumente", "datei", "dateien", "unterlagen", "ablage",
        "deren", "dessen", "absender", "empfänger", "betreff", "thema",
        "gibt",
    }

    def _fts_terms_from_question(self, question: str) -> Optional[str]:
        tokens = [t.strip(".,!?;:\"'()") for t in question.split()]
        terms = [t for t in tokens if t.lower() not in self._NL_STOP_WORDS and len(t) > 1]
        return " ".join(terms) if terms else None

    def _handle_natural_language(self, chat_id: str, question: str) -> None:
        if not self._openai_api_key:
            self._send_plain(chat_id, "KI-Fragen sind nicht konfiguriert (OPENAI_API_KEY fehlt).")
            return
        self._send_plain(chat_id, "🤔 Suche in der Ablage …")
        try:
            from openai import OpenAI
            from embed import get_embedding
            client = OpenAI(api_key=self._openai_api_key)

            # 1. FTS search — catches exact proper nouns (names, companies)
            fts_query = self._fts_terms_from_question(question)
            fts_rows, _ = _db.search_documents(query=fts_query, per_page=20)
            fts_ids = [r["id"] for r in fts_rows]

            # 2. Vector search — catches synonyms, plurals, cross-language matches
            question_vec = get_embedding(question, self._openai_api_key)
            vec_results = _db.search_by_embedding(question_vec, k=20, max_distance=self._nl_max_distance)
            vec_ids = [r[0] for r in vec_results]


            # 3. Reciprocal Rank Fusion — documents appearing in both lists rank highest
            K = 60
            scores: dict[int, float] = {}
            for rank, doc_id in enumerate(fts_ids):
                scores[doc_id] = scores.get(doc_id, 0) + 1 / (K + rank + 1)
            for rank, doc_id in enumerate(vec_ids):
                scores[doc_id] = scores.get(doc_id, 0) + 1 / (K + rank + 1)

            if scores:
                merged_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:15]
            else:
                merged_ids = fts_ids[:15]

            # Also fetch the 5 most recent documents matching the FTS query (or all docs
            # if no FTS terms) so recency questions always have the latest docs in context
            recent_rows, _ = _db.search_documents(
                query=fts_query if fts_query else None,
                per_page=5,
                sort_by="document_date",
                sort_order="desc",
            )
            seen_ids: set[int] = set(merged_ids)
            for r in recent_rows:
                if r["id"] not in seen_ids:
                    merged_ids.append(r["id"])
                    seen_ids.add(r["id"])

            rows = [doc for doc_id in merged_ids if (doc := _db.get_document(doc_id))]
            # Sort by date descending so GPT can answer recency questions correctly
            rows.sort(key=lambda d: d.get("document_date") or "", reverse=True)

            # 4. Build context and answer with GPT (single call, structured output)
            stats = _db.get_statistics()
            doc_lines = []
            id_to_doc = {doc["id"]: doc for doc in rows}
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
                doc_lines.append(" | ".join(parts))
            context = "\n".join(doc_lines) if doc_lines else "Keine passenden Dokumente gefunden."

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Du bist ein persönlicher Assistent für Meisters Dokumentenablage. "
                            "Beantworte Fragen auf Deutsch, präzise und freundlich. "
                            f"Die Ablage enthält insgesamt {stats['total']} Dokumente. "
                            "Die folgenden Dokumente sind top Suchergebnisse — nach Datum absteigend sortiert (neuestes zuerst), "
                            "können aber auch ähnliche (nicht exakt passende) Treffer enthalten. "
                            "Beantworte die Frage präzise anhand der Dokumente, die wirklich passen. "
                            "Ignoriere Dokumente, die nicht zur Frage passen. "
                            "Antworte im folgenden JSON-Format:\n"
                            '{"answer": "<deine Antwort auf Deutsch>", "ids": [<IDs der wirklich passenden Dokumente>]}\n'
                            "Gib nur JSON zurück, kein weiterer Text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Frage: {question}\n\nSuchergebnisse:\n{context}",
                    },
                ],
                max_tokens=500,
                response_format={"type": "json_object"},
            )

            import json as _json
            try:
                gpt_result = _json.loads(response.choices[0].message.content)
                answer_text = gpt_result.get("answer", "")
                referenced_ids = [int(i) for i in gpt_result.get("ids", [])]
            except Exception:
                answer_text = response.choices[0].message.content
                referenced_ids = [doc["id"] for doc in rows]

            self._send_plain(chat_id, answer_text)

            # Only show links for documents GPT actually referenced
            linked_docs = [id_to_doc[i] for i in referenced_ids if i in id_to_doc]
            if linked_docs and self._ablage_url:
                keyboard = [
                    [{"text": (doc.get("new_filename") or "Dokument")[:60],
                      "url": f"{self._ablage_url}/document/{doc['id']}"}]
                    for doc in linked_docs
                ]
                self._send(chat_id, "🔗 *Passende Dokumente:*", keyboard=keyboard)
        except Exception as e:
            print(f"Warning   : NL query failed: {e}")
            self._send_plain(chat_id, f"Fehler bei der KI-Anfrage: {e}")
