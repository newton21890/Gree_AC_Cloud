"""Persistent audit trail for Gree operating actions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION

STORAGE_KEY_ACTION_LOG = f"{DOMAIN}.action_log"
ACTION_LOG_MAX_ENTRIES = 5000


class GreeActionLog:
    """Store a bounded, persistent audit trail of commands and external changes."""

    def __init__(self, hass) -> None:
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY_ACTION_LOG)
        self._entries: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def async_load(self) -> None:
        """Restore entries from Home Assistant storage."""
        data = await self._store.async_load() or {}
        entries = data.get("entries", []) if isinstance(data, dict) else []
        if isinstance(entries, list):
            self._entries = [entry for entry in entries if isinstance(entry, dict)][
                -ACTION_LOG_MAX_ENTRIES:
            ]
        self._next_id = max((int(entry.get("id", 0)) for entry in self._entries), default=0) + 1

    async def async_record(
        self,
        mac: str,
        source: str,
        action: str,
        changes: dict[str, Any] | None = None,
        result: str = "recorded",
        details: str | None = None,
    ) -> dict[str, Any]:
        """Append and immediately persist one audit entry."""
        entry = {
            "id": self._next_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mac": mac,
            "source": source,
            "action": action,
            "changes": changes or {},
            "result": result,
        }
        if details:
            entry["details"] = details
        async with self._lock:
            self._next_id += 1
            self._entries.append(entry)
            del self._entries[:-ACTION_LOG_MAX_ENTRIES]
            await self._store.async_save({"entries": self._entries})
        return entry

    def entries(
        self,
        *,
        mac: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return newest matching entries in chronological display order."""
        matches = (
            entry
            for entry in reversed(self._entries)
            if (not mac or entry.get("mac") == mac)
            and (not source or entry.get("source") == source)
        )
        return list(reversed(list(matches)[: max(1, min(limit, 2000))]))

    async def async_clear(self, mac: str | None = None) -> int:
        """Clear all entries or only entries belonging to one unit."""
        async with self._lock:
            before = len(self._entries)
            if mac:
                self._entries = [entry for entry in self._entries if entry.get("mac") != mac]
            else:
                self._entries.clear()
            removed = before - len(self._entries)
            await self._store.async_save({"entries": self._entries})
        return removed
