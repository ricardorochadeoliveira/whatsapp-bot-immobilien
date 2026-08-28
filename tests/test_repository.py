from datetime import datetime, timedelta, timezone

from app.repository import InMemoryChatKontaktRepository, InMemoryFehlerLogRepository


def test_record_activity_legt_neuen_kontakt_an():
    repo = InMemoryChatKontaktRepository()
    repo.record_activity("+41790000001")
    assert repo.count_all() == 1


def test_record_activity_ist_idempotent_pro_telefonnummer():
    repo = InMemoryChatKontaktRepository()
    repo.record_activity("+41790000001")
    repo.record_activity("+41790000001")
    repo.record_activity("+41790000002")
    assert repo.count_all() == 2


def test_count_active_since_filtert_nach_letzter_aktivitaet():
    repo = InMemoryChatKontaktRepository()
    repo.record_activity("+41790000001")
    zukunft = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert repo.count_active_since(zukunft) == 0
    vergangenheit = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert repo.count_active_since(vergangenheit) == 1


def test_fehlerlog_add_und_get_recent():
    repo = InMemoryFehlerLogRepository()
    repo.add("whatsapp_send", "Timeout", telefonnummer="+41790000001")
    repo.add("claude_api", "API nicht erreichbar")
    eintraege = repo.get_recent()
    assert len(eintraege) == 2
    # Neueste zuerst
    assert eintraege[0].quelle == "claude_api"
    assert eintraege[1].telefonnummer == "+41790000001"


def test_fehlerlog_get_recent_respektiert_limit():
    repo = InMemoryFehlerLogRepository()
    for i in range(5):
        repo.add("quelle", f"Meldung {i}")
    assert len(repo.get_recent(limit=2)) == 2
