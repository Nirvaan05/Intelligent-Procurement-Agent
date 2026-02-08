"""Persistence layer for the intelligent procurement agent.

Manages file I/O for three data stores:

* ``memory_store.json``  — site rules and confirmed orders (structured JSON)
* ``mock_vendors.json``  — vendor catalog (read-only JSON)
* ``audit_log.jsonl``    — append-only decision audit trail (JSONL)

No business logic lives here — only read / write / append operations
with robust error handling.  Tool functions in ``tools.py`` import from
this module; they never touch the filesystem directly.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path constants  (all relative to this package directory)
# ---------------------------------------------------------------------------

_BASE: Path = Path(__file__).resolve().parent

VENDORS_PATH: Path = _BASE / "mock_vendors.json"
"""Read-only vendor catalog."""

MEMORY_PATH: Path = _BASE / "memory_store.json"
"""Structured store for site rules and orders."""

AUDIT_LOG_PATH: Path = _BASE / "audit_log.jsonl"
"""Append-only decision audit trail."""


# ---------------------------------------------------------------------------
# JSON read / write helpers
# ---------------------------------------------------------------------------

def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file and return its contents as a dict.

    Handles missing files, corrupt JSON, and permission errors gracefully.
    If the file does not exist it is created with an empty ``{}``.

    Args:
        path: Absolute path to the JSON file.

    Returns:
        Parsed dict.  Returns ``{}`` on any read/parse failure.
    """
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return {}
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, data: dict[str, Any]) -> str | None:
    """Write a dict to a JSON file, creating parent directories if needed.

    Args:
        path: Absolute path to the JSON file.
        data: Dict to serialise.

    Returns:
        ``None`` on success, or a human-readable error string on failure.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        return None
    except (OSError, TypeError, ValueError) as exc:
        return f"File write error ({path.name}): {exc}"


# ---------------------------------------------------------------------------
# Audit logging  (append-only JSONL)
# ---------------------------------------------------------------------------

def log_decision(event_type: str, site_name: str, details: dict[str, Any]) -> None:
    """Append a single audit entry to ``audit_log.jsonl``.

    Each line is a self-contained JSON object::

        {"timestamp": "ISO-8601", "event_type": "…", "site_name": "…", "details": {…}}

    Valid *event_type* values:

    * ``rules_stored``        — site rules saved
    * ``vendor_rejected``     — vendor excluded (blacklist or over-budget)
    * ``vendor_selected``     — vendor chosen for an order
    * ``approval_requested``  — order exceeds approval limit
    * ``order_placed``        — order confirmed (auto or human-approved)

    Args:
        event_type: One of the event types listed above.
        site_name:  Construction site this decision relates to.
        details:    Free-form dict with context (vendor, price, reason …).
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "site_name": site_name,
        "details": details,
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # audit logging must never crash a tool call


def read_audit_log() -> list[dict[str, Any]]:
    """Read every entry from ``audit_log.jsonl``.

    Returns:
        A list of dicts (one per logged event), in chronological order.
        Returns an empty list if the file is missing or empty.
    """
    entries: list[dict[str, Any]] = []
    try:
        with open(AUDIT_LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    entries.append(json.loads(stripped))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return entries


def clear_audit_log() -> None:
    """Delete ``audit_log.jsonl`` so the next run starts with a clean slate."""
    try:
        AUDIT_LOG_PATH.unlink(missing_ok=True)
    except OSError:
        pass
