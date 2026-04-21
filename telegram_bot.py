"""
Telegram bot for Ablage — sends document notifications with inline buttons
and handles /suche commands and button callbacks.

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
from typing import Optional

import db as _db

_MD_SPECIAL = r'\_*[]()~`>#+-=|{}.!'

def _esc(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    for ch in _MD_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


class TelegramBot:
    def __init__(self, token: str, chat_id: str, ablage_url: str = ""):
        self._token = token
        self._chat_id = str(chat_id)
        self._ablage_url = ablage_url.rstrip("/")
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

    def start_polling(self) -> None:
        t = threading.Thread(target=self._poll_loop, daemon=True, name="telegram-poll")
        t.start()

    # ------------------------------------------------------------------
    # Internals
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

    def _send(self, chat_id: str, text: str, keyboard: Optional[list] = None) -> None:
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            self._api("sendMessage", payload)
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
                # Toggle button for next press
                new_value = 1 - value
                new_label = "☆ Nicht steuerrelevant" if value else "★ Steuerrelevant"
                # Rebuild keyboard: keep URL button if present, replace tax button
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

    def _handle_message(self, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if text.lower().startswith("/suche"):
            query = text[6:].strip()
            if not query:
                self._send(chat_id, "Verwendung: `/suche Suchbegriff`")
                return
            try:
                rows, total = _db.search_documents(query=query, per_page=5)
            except Exception as e:
                self._send(chat_id, f"Fehler bei der Suche: {e}")
                return

            if not rows:
                self._send(chat_id, f"Keine Dokumente fuer \"{_esc(query)}\" gefunden\.")
                return

            shown = len(rows)
            lines = [f"🔍 *{total} Treffer fuer \"{_esc(query)}\"*" + (f" \(Top {shown}\)" if total > shown else "") + ":"]
            keyboard = []
            for doc in rows:
                name = doc["new_filename"] or "-"
                date = f" · {_esc(doc['document_date'])}" if doc.get("document_date") else ""
                lines.append(f"• _{_esc(name)}_{date}")
                if self._ablage_url:
                    keyboard.append([{"text": name[:60], "url": f"{self._ablage_url}/document/{doc['id']}"}])

            self._send(chat_id, "\n".join(lines), keyboard=keyboard or None)

        elif text.lower().startswith("/start") or text.lower().startswith("/hilfe"):
            self._send(chat_id, (
                "*Meisters Ablage Bot* 📄\n\n"
                "Ich benachrichtige dich über neue Dokumente in deiner Ablage "
                "und beantworte Suchanfragen\.\n\n"
                "Befehle:\n"
                "`/suche Begriff` \— Dokumente suchen\n"
                "`/hilfe` \— diese Hilfe anzeigen"
            ))
