from app.webhook_dedup import SeenMessageIds


def test_mark_seen_true_on_first_call():
    dedup = SeenMessageIds()
    assert dedup.mark_seen("wamid.abc") is True


def test_mark_seen_false_on_repeated_call():
    dedup = SeenMessageIds()
    dedup.mark_seen("wamid.abc")
    assert dedup.mark_seen("wamid.abc") is False


def test_mark_seen_true_again_after_ttl_expires(monkeypatch):
    dedup = SeenMessageIds(ttl_seconds=60)
    current_time = [1000.0]
    monkeypatch.setattr("app.webhook_dedup.time.time", lambda: current_time[0])

    assert dedup.mark_seen("wamid.abc") is True
    assert dedup.mark_seen("wamid.abc") is False

    current_time[0] += 61
    assert dedup.mark_seen("wamid.abc") is True


def test_different_ids_are_independent():
    dedup = SeenMessageIds()
    assert dedup.mark_seen("wamid.one") is True
    assert dedup.mark_seen("wamid.two") is True
