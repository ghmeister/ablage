"""
Persons and sender groups from the Einstellungen page, for every component.

The data lives in the archive database (tables persons, sender_groups), so a
change in the web UI applies right away to the bot, the KI-Suche, the Telegram
bot and the ablage-mcp server. The tables are tiny, so they are read fresh on
every call instead of being cached.
"""
from __future__ import annotations

from typing import Any

import db as _db


def persons() -> list[dict[str, Any]]:
    try:
        return _db.get_persons()
    except Exception:
        return []


def sender_groups() -> list[dict[str, Any]]:
    try:
        return _db.get_sender_groups()
    except Exception:
        return []


def _terms(item: dict[str, Any]) -> list[str]:
    return [item["name"], *(item.get("variants") or [])]


def expand_sender(value: str) -> list[str]:
    """All spellings for a sender filter that names a group (or one of its terms)."""
    v = value.strip().lower()
    for g in sender_groups():
        if any(v == t.lower() for t in _terms(g) + list(g.get("search_terms") or [])):
            return _terms(g)
    return [value]


def canonical_sender(sender: str | None, company: str | None) -> str | None:
    """Short name of the sender group for a new document, or None if no group fits."""
    try:
        return _db.canonical_sender(sender, company, sender_groups())
    except Exception:
        return None


def expand_person(value: str) -> list[str]:
    """All spellings for a recipient filter that names a person (or one of the variants)."""
    v = value.strip().lower()
    for p in persons():
        if any(v == t.lower() for t in _terms(p)):
            return _terms(p)
    return [value]


def known_person_names() -> list[str]:
    return [p["name"] for p in persons()]


def renamer_hints() -> str:
    """Prompt lines for the filename AI: family members and canonical sender names."""
    lines = []
    names = known_person_names()
    if names:
        lines.append(f"Known family members: {', '.join(names)}.")
    groups = [g for g in sender_groups() if g.get("variants")]
    if groups:
        lines.append("Known senders — use the short name on the left for the Company part:")
        for g in groups[:80]:
            lines.append(f"  {g['name']} ← {', '.join(g['variants'][:6])}")
    return "\n".join(lines)


def search_hints() -> str:
    """Context for the KI-Suche: which words point to which sender, and who is in the family."""
    lines = []
    for g in sender_groups():
        parts = [g["name"]]
        if g.get("variants"):
            parts.append(f"(also written: {', '.join(g['variants'][:6])})")
        if g.get("search_terms"):
            parts.append(f"— search words: {', '.join(g['search_terms'][:10])}")
        if g.get("document_type"):
            parts.append(f"— usual type: {g['document_type']}")
        if g.get("note"):
            parts.append(f"— {g['note']}")
        lines.append("  " + " ".join(parts))
    out = []
    if lines:
        out.append("Known senders:\n" + "\n".join(lines))
    people = persons()
    if people:
        out.append("Family members: " + "; ".join(
            p["name"] + (f" (also: {', '.join(p['variants'][:6])})" if p.get("variants") else "") for p in people
        ))
    return "\n".join(out)
