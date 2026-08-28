"""Schutz gegen Kosten-/Spam-Missbrauch der Claude-API im WhatsApp-Chat.

Ohne Login kann grundsaetzlich jede Telefonnummer beliebig oft schreiben -
jede Nachricht, die bis zur Intent-/Listing-Extraktion durchkommt, kostet
einen echten Claude-API-Aufruf. Zwei Ebenen schuetzen davor:

- Pro Telefonnummer: verhindert, dass eine einzelne Quelle (Bot/Skript) den
  Chat mit vielen Nachrichten flutet.
- Global: Sicherheitsnetz gegen viele verschiedene Nummern gleichzeitig
  (koordinierter Angriff oder Bug in einer WhatsApp-Integration).

Einfacher In-Memory Sliding-Window-Zaehler - reicht fuer eine einzelne
Prozess-Instanz (passt zum aktuellen Stand ohne Multi-Instance-Hosting).
Bei mehreren Instanzen muesste das ueber einen gemeinsamen Speicher (z.B.
Redis) laufen - siehe docs/launch-checkliste.md.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict


class RateLimiter:
    def __init__(
        self,
        per_phone_per_minute: int,
        per_phone_per_day: int,
        global_per_minute: int,
    ):
        self._per_phone_per_minute = per_phone_per_minute
        self._per_phone_per_day = per_phone_per_day
        self._global_per_minute = global_per_minute
        self._phone_timestamps: dict[str, list[float]] = defaultdict(list)
        self._global_timestamps: list[float] = []
        self._lock = threading.Lock()

    def allow(self, telefonnummer: str) -> bool:
        """True, wenn ein Claude-Aufruf fuer diese Telefonnummer jetzt erlaubt
        ist (und zaehlt ihn sofort mit). False, wenn ein Limit erreicht ist -
        in dem Fall wird NICHTS gezaehlt/aufgerufen."""
        now = time.time()

        with self._lock:
            self._global_timestamps[:] = [t for t in self._global_timestamps if t > now - 60]
            if len(self._global_timestamps) >= self._global_per_minute:
                return False

            history = self._phone_timestamps[telefonnummer]
            history[:] = [t for t in history if t > now - 86400]
            last_minute = [t for t in history if t > now - 60]
            if len(last_minute) >= self._per_phone_per_minute:
                return False
            if len(history) >= self._per_phone_per_day:
                return False

            history.append(now)
            self._global_timestamps.append(now)
            return True


def build_default_rate_limiter() -> RateLimiter:
    return RateLimiter(
        per_phone_per_minute=int(os.environ.get("RATE_LIMIT_PER_PHONE_PER_MINUTE", "6")),
        per_phone_per_day=int(os.environ.get("RATE_LIMIT_PER_PHONE_PER_DAY", "200")),
        global_per_minute=int(os.environ.get("RATE_LIMIT_GLOBAL_PER_MINUTE", "60")),
    )
