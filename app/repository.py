"""Repository-Interfaces + In-Memory-Implementierungen.

Punkt 1/2 (echte DB / CRM-Anbindung) sind on hold. Die Matching-Engine (Punkt 4)
und der Matching-Job (Punkt 5) sprechen ausschliesslich gegen diese Interfaces.
Sobald ein echtes Backend feststeht, wird nur eine neue Implementierung dieser
Interfaces geschrieben - der Rest der Anwendung bleibt unveraendert.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.models import Firma, Immobilie, Kunde, Lead, MatchLog, Suchprofil


class FirmaRepository(ABC):
    @abstractmethod
    def add(self, firma: Firma) -> Firma: ...

    @abstractmethod
    def get_by_auth_user_id(self, auth_user_id: str) -> Optional[Firma]: ...

    @abstractmethod
    def get_by_id(self, firma_id: str) -> Optional[Firma]: ...

    @abstractmethod
    def get_by_email(self, email: str) -> Optional[Firma]: ...

    @abstractmethod
    def get_or_create_by_phone(self, telefonnummer: str, name: str, typ: str) -> Firma: ...

    @abstractmethod
    def get_all(self) -> list[Firma]: ...


class ImmobilienRepository(ABC):
    @abstractmethod
    def add(self, immobilie: Immobilie) -> Immobilie: ...

    @abstractmethod
    def get_all(self) -> list[Immobilie]: ...

    @abstractmethod
    def get_by_id(self, immobilie_id: str) -> Optional[Immobilie]: ...

    @abstractmethod
    def get_by_firma(self, firma_id: str) -> list[Immobilie]: ...

    @abstractmethod
    def get_by_status(self, status: str) -> list[Immobilie]: ...

    @abstractmethod
    def set_status(self, immobilie_id: str, status: str) -> None: ...


class KundenRepository(ABC):
    @abstractmethod
    def get_or_create_by_phone(
        self, telefonnummer: str, firma_id: Optional[str] = None
    ) -> Kunde: ...

    @abstractmethod
    def get_by_id(self, kunde_id: str) -> Optional[Kunde]: ...


class SuchprofilRepository(ABC):
    @abstractmethod
    def add(self, suchprofil: Suchprofil) -> Suchprofil: ...

    @abstractmethod
    def get_all_active(self) -> list[Suchprofil]: ...

    @abstractmethod
    def get_by_kunde(self, kunde_id: str) -> list[Suchprofil]: ...


class MatchLogRepository(ABC):
    @abstractmethod
    def exists(self, suchprofil_id: str, immobilie_id: str) -> bool: ...

    @abstractmethod
    def add(self, match_log: MatchLog) -> MatchLog: ...

    @abstractmethod
    def get_all(self) -> list[MatchLog]: ...


class LeadRepository(ABC):
    @abstractmethod
    def add(self, lead: Lead) -> Lead: ...

    @abstractmethod
    def get_by_firma(self, firma_id: str) -> list[Lead]: ...


# ---------------------------------------------------------------------------
# In-Memory-Implementierungen (Platzhalter, solange Punkt 1/2 on hold sind)
# ---------------------------------------------------------------------------


class InMemoryFirmaRepository(FirmaRepository):
    def __init__(self):
        self._items: dict[str, Firma] = {}

    def add(self, firma: Firma) -> Firma:
        self._items[firma.id] = firma
        return firma

    def get_by_auth_user_id(self, auth_user_id: str) -> Optional[Firma]:
        return next((f for f in self._items.values() if f.auth_user_id == auth_user_id), None)

    def get_by_id(self, firma_id: str) -> Optional[Firma]:
        return self._items.get(firma_id)

    def get_by_email(self, email: str) -> Optional[Firma]:
        return next((f for f in self._items.values() if f.email == email), None)

    def get_or_create_by_phone(self, telefonnummer: str, name: str, typ: str) -> Firma:
        existing = next(
            (f for f in self._items.values() if f.telefonnummer == telefonnummer), None
        )
        if existing:
            return existing
        firma = Firma(name=name, typ=typ, telefonnummer=telefonnummer)
        self._items[firma.id] = firma
        return firma

    def get_all(self) -> list[Firma]:
        return list(self._items.values())


class InMemoryImmobilienRepository(ImmobilienRepository):
    def __init__(self, seed: Optional[list[Immobilie]] = None):
        self._items: dict[str, Immobilie] = {i.id: i for i in (seed or [])}

    def add(self, immobilie: Immobilie) -> Immobilie:
        self._items[immobilie.id] = immobilie
        return immobilie

    def get_all(self) -> list[Immobilie]:
        return list(self._items.values())

    def get_by_id(self, immobilie_id: str) -> Optional[Immobilie]:
        return self._items.get(immobilie_id)

    def get_by_firma(self, firma_id: str) -> list[Immobilie]:
        return [i for i in self._items.values() if i.firma_id == firma_id]

    def get_by_status(self, status: str) -> list[Immobilie]:
        return [i for i in self._items.values() if i.status == status]

    def set_status(self, immobilie_id: str, status: str) -> None:
        if immobilie_id in self._items:
            self._items[immobilie_id] = self._items[immobilie_id].model_copy(update={"status": status})


class InMemoryKundenRepository(KundenRepository):
    def __init__(self):
        self._items: dict[str, Kunde] = {}
        self._by_phone: dict[tuple[str, Optional[str]], str] = {}

    def get_or_create_by_phone(self, telefonnummer: str, firma_id: Optional[str] = None) -> Kunde:
        key = (telefonnummer, firma_id)
        kunde_id = self._by_phone.get(key)
        if kunde_id:
            return self._items[kunde_id]
        kunde = Kunde(telefonnummer=telefonnummer, firma_id=firma_id)
        self._items[kunde.id] = kunde
        self._by_phone[key] = kunde.id
        return kunde

    def get_by_id(self, kunde_id: str) -> Optional[Kunde]:
        return self._items.get(kunde_id)


class InMemorySuchprofilRepository(SuchprofilRepository):
    def __init__(self):
        self._items: dict[str, Suchprofil] = {}

    def add(self, suchprofil: Suchprofil) -> Suchprofil:
        self._items[suchprofil.id] = suchprofil
        return suchprofil

    def get_all_active(self) -> list[Suchprofil]:
        return [s for s in self._items.values() if s.aktiv]

    def get_by_kunde(self, kunde_id: str) -> list[Suchprofil]:
        return [s for s in self._items.values() if s.kunde_id == kunde_id]


class InMemoryMatchLogRepository(MatchLogRepository):
    def __init__(self):
        self._items: list[MatchLog] = []
        self._seen: set[tuple[str, str]] = set()

    def exists(self, suchprofil_id: str, immobilie_id: str) -> bool:
        return (suchprofil_id, immobilie_id) in self._seen

    def add(self, match_log: MatchLog) -> MatchLog:
        self._items.append(match_log)
        self._seen.add((match_log.suchprofil_id, match_log.immobilie_id))
        return match_log

    def get_all(self) -> list[MatchLog]:
        return list(self._items)


class InMemoryLeadRepository(LeadRepository):
    def __init__(self):
        self._items: list[Lead] = []

    def add(self, lead: Lead) -> Lead:
        self._items.append(lead)
        return lead

    def get_by_firma(self, firma_id: str) -> list[Lead]:
        return [l for l in self._items if l.firma_id == firma_id]
