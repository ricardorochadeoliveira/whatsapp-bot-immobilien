"""Benachrichtigung bei Match (Teil von Punkt 5).

Es gibt noch keine echte WhatsApp-Nummer/API, daher wird jede Benachrichtigung
auf die Konsole geloggt. Zusaetzlich koennen Listener registriert werden (z.B.
das simulierte Chat-Frontend aus Punkt 6), um die Nachricht direkt im
jeweiligen Kunden-Chat anzuzeigen.
"""
from __future__ import annotations

import logging
from typing import Callable

from app.models import Immobilie, Kunde, Suchprofil

logger = logging.getLogger("immo_bot.notifications")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

NotificationListener = Callable[[Kunde, Suchprofil, Immobilie], None]


class NotificationDispatcher:
    def __init__(self):
        self._listeners: list[NotificationListener] = []

    def register(self, listener: NotificationListener) -> None:
        self._listeners.append(listener)

    def notify(self, kunde: Kunde, suchprofil: Suchprofil, immobilie: Immobilie) -> None:
        logger.info(
            "Neues Match fuer %s (Suchprofil %s): %s (%s, %s Zimmer, CHF %s) -> %s",
            kunde.telefonnummer,
            suchprofil.id,
            immobilie.titel,
            immobilie.ort,
            immobilie.zimmer,
            immobilie.preis,
            immobilie.link,
        )
        for listener in self._listeners:
            listener(kunde, suchprofil, immobilie)
