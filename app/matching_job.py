"""Punkt 5: Matching-Job fuer neue Inserate.

Wird bei jedem neuen Inserat ausgefuehrt (bzw. sobald ein Inserat die
Admin-Pruefung passiert und aktiv wird): prueft alle aktiven Suchabos, loest
bei Treffer eine Benachrichtigung aus und fuehrt ein MatchLog, damit niemand
doppelt benachrichtigt wird.

Firmenuebergreifend seit dem Marktplatz-Pivot (docs/produkt-abgleich.md):
ein Suchabo matcht gegen Inserate aller Anbieter, nicht nur einer Firma.
"""
from __future__ import annotations

from app.matching import MatchingEngine
from app.models import Immobilie, MatchLog
from app.notifications import NotificationDispatcher
from app.repository import KundenRepository, MatchLogRepository, SuchprofilRepository


class MatchingJob:
    def __init__(
        self,
        matching_engine: MatchingEngine,
        suchprofil_repo: SuchprofilRepository,
        kunden_repo: KundenRepository,
        matchlog_repo: MatchLogRepository,
        dispatcher: NotificationDispatcher,
    ):
        self._matching_engine = matching_engine
        self._suchprofil_repo = suchprofil_repo
        self._kunden_repo = kunden_repo
        self._matchlog_repo = matchlog_repo
        self._dispatcher = dispatcher

    def process_new_listing(self, immobilie: Immobilie) -> list[MatchLog]:
        """Prueft ein neues Inserat gegen alle aktiven Suchabos.

        Gibt die neu erzeugten MatchLog-Eintraege zurueck (bereits
        benachrichtigte Kombinationen werden uebersprungen).
        """
        neue_matches: list[MatchLog] = []
        for suchprofil in self._suchprofil_repo.get_all_active():
            if self._matchlog_repo.exists(suchprofil.id, immobilie.id):
                continue
            if not self._matching_engine.matches_suchprofil(immobilie, suchprofil):
                continue

            kunde = self._kunden_repo.get_by_id(suchprofil.kunde_id)
            if kunde is None or not kunde.opt_in:
                continue

            match_log = self._matchlog_repo.add(
                MatchLog(
                    suchprofil_id=suchprofil.id,
                    immobilie_id=immobilie.id,
                    firma_id=immobilie.firma_id,
                )
            )
            neue_matches.append(match_log)
            self._dispatcher.notify(kunde, suchprofil, immobilie)

        return neue_matches
