"""Long-term memory: facts the user explicitly asked ClawMind to remember.

Search uses embeddings when EMBEDDING_MODEL is configured and falls back to
simple keyword matching otherwise (or when the embedding call fails).
"""

import json
import logging
import math
import re

from sqlalchemy import select

from app.db import Memory, Message, get_session, iso
from app.llm import LLMError, get_llm
from app.redact import find_secret

log = logging.getLogger("clawmind.memory")

MAX_MEMORY_LENGTH = 500

STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "what", "which", "who", "whom", "this", "that",
    "these", "those", "with", "from", "have", "has", "had", "you", "your", "yours", "about",
    "into", "does", "did", "can", "could", "would", "should", "will", "just", "tell", "know",
    "remember", "please", "called", "named", "is", "my", "me", "mine", "our", "its", "it",
}


class MemoryRejected(ValueError):
    pass


def _to_dict(row):
    return {"id": row.id, "content": row.content, "created_at": iso(row.created_at)}


def _words(text):
    words = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 2 or word in STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        words.add(word)
    return words


def _embed_one(text):
    llm = get_llm()
    if not llm.can_embed:
        return None
    try:
        return llm.embed([text])[0]
    except LLMError as exc:
        log.info("Embeddings unavailable, using keyword search: %s", exc)
        return None


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def clean_memory_text(text):
    text = " ".join(str(text or "").split())
    text = re.sub(r"^(please\s+)?remember(\s+that)?\s+", "", text, flags=re.I)
    return text[:1].upper() + text[1:] if text else text


def add_memory(text):
    content = clean_memory_text(text)
    if len(content) < 3:
        raise MemoryRejected("That memory is too short.")
    if len(content) > MAX_MEMORY_LENGTH:
        raise MemoryRejected(f"Memories are limited to {MAX_MEMORY_LENGTH} characters.")

    secret = find_secret(content)
    if secret:
        raise MemoryRejected(
            f"I won't save that because it looks like it contains {secret}. "
            "Secrets should be kept in a password manager, not in memory."
        )

    with get_session() as db:
        for row in db.scalars(select(Memory)):
            if row.content.lower() == content.lower():
                return _to_dict(row)

        vector = _embed_one(content)
        row = Memory(content=content, embedding=json.dumps(vector) if vector else None)
        db.add(row)
        db.commit()
        log.info("Saved memory #%s", row.id)
        return _to_dict(row)


def list_memories(limit=200):
    with get_session() as db:
        rows = db.scalars(select(Memory).order_by(Memory.id.desc()).limit(limit))
        return [_to_dict(row) for row in rows]


def delete_memory(memory_id):
    with get_session() as db:
        row = db.get(Memory, int(memory_id))
        if not row:
            return False
        db.delete(row)
        db.commit()
        log.info("Deleted memory #%s", memory_id)
        return True


def get_memory(memory_id):
    with get_session() as db:
        row = db.get(Memory, int(memory_id))
        return _to_dict(row) if row else None


def search_memories(query, limit=5):
    query = (query or "").strip()
    if not query:
        return []

    with get_session() as db:
        rows = list(db.scalars(select(Memory)))
    if not rows:
        return []

    scored = {}

    query_words = _words(query)
    for row in rows:
        row_words = _words(row.content)
        overlap = len(query_words & row_words)
        if overlap:
            scored[row.id] = overlap / max(len(query_words), 1)
        elif query.lower() in row.content.lower():
            scored[row.id] = 0.5

    if any(row.embedding for row in rows):
        query_vector = _embed_one(query)
        if query_vector:
            for row in rows:
                if not row.embedding:
                    continue
                similarity = _cosine(query_vector, json.loads(row.embedding))
                if similarity > 0.45:
                    scored[row.id] = max(scored.get(row.id, 0), similarity)

    by_id = {row.id: row for row in rows}
    best = sorted(scored, key=lambda i: scored[i], reverse=True)[:limit]
    return [_to_dict(by_id[i]) for i in best]


# Short-term memory: recent conversation messages for a session

def save_message(session_id, role, content, tools_used=None):
    with get_session() as db:
        row = Message(
            session_id=session_id,
            role=role,
            content=content,
            tools_used=",".join(tools_used or []),
        )
        db.add(row)
        db.commit()
        return row.id


def recent_messages(session_id, limit=20, roles=("user", "assistant")):
    with get_session() as db:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id, Message.role.in_(roles))
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = list(db.scalars(stmt))
    rows.reverse()
    return rows


def message_to_dict(row):
    return {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "tools_used": [t for t in row.tools_used.split(",") if t],
        "created_at": iso(row.created_at),
    }
