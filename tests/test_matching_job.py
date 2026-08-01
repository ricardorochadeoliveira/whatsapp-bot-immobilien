from app.matching import MatchingEngine
from app.matching_job import MatchingJob
from app.models import Immobilie, Suchprofil
from app.notifications import NotificationDispatcher
from app.repository import (
    InMemoryImmobilienRepository,
    InMemoryKundenRepository,
    InMemoryMatchLogRepository,
    InMemorySuchprofilRepository,
)


def make_job():
    immobilien_repo = InMemoryImmobilienRepository()
    kunden_repo = InMemoryKundenRepository()
    suchprofil_repo = InMemorySuchprofilRepository()
    matchlog_repo = InMemoryMatchLogRepository()
    dispatcher = NotificationDispatcher()
    job = MatchingJob(
        matching_engine=MatchingEngine(immobilien_repo),
        suchprofil_repo=suchprofil_repo,
        kunden_repo=kunden_repo,
        matchlog_repo=matchlog_repo,
        dispatcher=dispatcher,
    )
    return job, kunden_repo, suchprofil_repo, matchlog_repo, dispatcher


def make_immobilie(**overrides):
    defaults = dict(
        titel="Test-Wohnung",
        zimmer=2.5,
        kanton="Zug",
        ort="Zug",
        preis=2000,
        objekttyp="Wohnung",
        flaeche_m2=60,
        bild_url="https://example.com/img.png",
        link="https://example.com/inserate/1",
    )
    defaults.update(overrides)
    return Immobilie(**defaults)


def test_matching_listing_notifies_and_logs():
    job, kunden_repo, suchprofil_repo, matchlog_repo, dispatcher = make_job()
    kunde = kunden_repo.get_or_create_by_phone("+41791234567")
    suchprofil_repo.add(
        Suchprofil(kunde_id=kunde.id, zimmer=2.0, kanton="Zug", preis_max=2200, objekttyp="Wohnung")
    )

    notified = []
    dispatcher.register(lambda k, s, i: notified.append((k.id, s.id, i.id)))

    immobilie = make_immobilie()
    matches = job.process_new_listing(immobilie)

    assert len(matches) == 1
    assert len(notified) == 1
    assert matchlog_repo.exists(matches[0].suchprofil_id, immobilie.id)


def test_no_duplicate_notification_for_same_listing():
    job, kunden_repo, suchprofil_repo, matchlog_repo, dispatcher = make_job()
    kunde = kunden_repo.get_or_create_by_phone("+41791234567")
    suchprofil_repo.add(
        Suchprofil(kunde_id=kunde.id, zimmer=2.0, kanton="Zug", preis_max=2200, objekttyp="Wohnung")
    )

    immobilie = make_immobilie()
    first = job.process_new_listing(immobilie)
    second = job.process_new_listing(immobilie)

    assert len(first) == 1
    assert len(second) == 0
    assert len(matchlog_repo.get_all()) == 1


def test_no_match_when_criteria_dont_fit():
    job, kunden_repo, suchprofil_repo, matchlog_repo, dispatcher = make_job()
    kunde = kunden_repo.get_or_create_by_phone("+41791234567")
    suchprofil_repo.add(
        Suchprofil(kunde_id=kunde.id, zimmer=4, kanton="Zug", preis_max=2200, objekttyp="Wohnung")
    )

    immobilie = make_immobilie(zimmer=2.5)
    matches = job.process_new_listing(immobilie)

    assert matches == []
    assert matchlog_repo.get_all() == []


def test_opt_out_kunde_is_not_notified():
    job, kunden_repo, suchprofil_repo, matchlog_repo, dispatcher = make_job()
    kunde = kunden_repo.get_or_create_by_phone("+41791234567")
    kunde.opt_in = False
    suchprofil_repo.add(
        Suchprofil(kunde_id=kunde.id, zimmer=2.0, kanton="Zug", preis_max=2200, objekttyp="Wohnung")
    )

    matches = job.process_new_listing(make_immobilie())
    assert matches == []
