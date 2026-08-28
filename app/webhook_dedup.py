"""Verhindert doppelte Verarbeitung von WhatsApp-Webhook-Zustellungen.

Meta liefert ein Webhook-Event erneut aus, wenn unsere Antwort nicht
rechtzeitig ankam (z.B. weil die Verarbeitung zu lange dauerte). Ohne Dedup
wuerde jede Zustellung wie eine neue Nachricht behandelt - inklusive eigenem
Rate-Limit-Verbrauch und eigenem Eintrag im Chatverlauf.

Gleiche Bauweise wie app/rate_limiter.py: In-Memory, reicht fuer eine
einzelne Prozess-Instanz (siehe Docstring dort). Mit Lock geschuetzt, weil
die Verarbeitung jetzt in FastAPIs Background-Task-Threadpool laeuft statt
immer seriell im Single-Event-Loop.
"""
from __future__ import annotations

import threading
import time


class SeenMessageIds:
    def __init__(self, ttl_seconds: int = 900):
        self._ttl_seconds = ttl_seconds
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def mark_seen(self, message_id: str) -> bool:
        """True beim ersten Mal (merkt sich die ID), False wenn sie innerhalb
        der TTL bereits gesehen wurde."""
        now = time.time()
        with self._lock:
            self._seen = {mid: ts for mid, ts in self._seen.items() if ts > now - self._ttl_seconds}
            if message_id in self._seen:
                return False
            self._seen[message_id] = now
            return True
