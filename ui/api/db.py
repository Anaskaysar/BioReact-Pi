"""MongoDB Atlas telemetry persistence — optional, fail-soft.

Soft-imports pymongo (same pattern as advisor.py's google-genai and
color_ph.py's Pillow) so a missing package or unset MONGODB_URI degrades to
harmless no-ops instead of crashing the dashboard. The WS telemetry loop
never depends on this succeeding — see main.py, where inserts are fired as
a background task rather than awaited inline.
"""

from __future__ import annotations

from typing import Any

from ui.config import settings

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except Exception:  # noqa: BLE001 — broader than ImportError on purpose: pymongo's
    # optional PyOpenSSL/OCSP support can raise AttributeError at import time on
    # environments with a mismatched pyOpenSSL/cryptography version, not just a
    # missing package. Any failure here should degrade the same way (no
    # persistence), not crash the whole dashboard.
    MongoClient = None  # type: ignore[assignment,misc]
    PyMongoError = Exception  # type: ignore[assignment,misc]

_client: "MongoClient | None" = None


def _get_collection():
    """Lazily connect on first real use (not at import time — importing this
    module must never touch the network), or None if not configured/available."""
    global _client
    if MongoClient is None or not settings.mongodb_uri:
        return None
    if _client is None:
        # serverSelectionTimeoutMS keeps a bad/unreachable URI from hanging
        # the caller — same "never block the demo" principle as everywhere
        # else in this project.
        _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
    return _client[settings.mongodb_db_name][settings.mongodb_collection]


def insert_packet(packet: dict[str, Any]) -> bool:
    """Store one telemetry packet. Returns whether it actually got stored —
    callers should treat this as fire-and-forget, not something to retry."""
    collection = _get_collection()
    if collection is None:
        return False
    try:
        collection.insert_one(dict(packet))
        return True
    except PyMongoError as exc:
        print(f"[db] insert failed ({exc}); continuing without persistence for this packet")
        return False


def get_recent(limit: int = 200) -> list[dict[str, Any]]:
    """Most recent stored packets, newest first. Empty list if unavailable —
    callers shouldn't distinguish "not configured" from "temporarily down"."""
    collection = _get_collection()
    if collection is None:
        return []
    try:
        docs = list(collection.find().sort("timestamp", -1).limit(limit))
        for doc in docs:
            doc["_id"] = str(doc["_id"])  # ObjectId isn't JSON-serializable
        return docs
    except PyMongoError as exc:
        print(f"[db] history query failed ({exc})")
        return []


def is_configured() -> bool:
    return MongoClient is not None and bool(settings.mongodb_uri)
