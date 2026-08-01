from app.matching import MatchingEngine
from app.models import SearchCriteria
from app.repository import InMemoryImmobilienRepository
from app.seed_data import build_seed_immobilien


def make_engine():
    repo = InMemoryImmobilienRepository(seed=build_seed_immobilien())
    return MatchingEngine(repo)


def test_search_filters_by_canton_and_price():
    engine = make_engine()
    criteria = SearchCriteria(rooms=2.5, canton="Zug", max_price=2200, property_type="Wohnung")
    results = engine.search(criteria)

    assert len(results) == 1
    assert results[0].titel == "Helle 2.5-Zimmer-Wohnung mit Balkon"


def test_search_rooms_is_minimum_not_exact():
    engine = make_engine()
    criteria = SearchCriteria(rooms=3, canton="Zug", max_price=5000, property_type="Wohnung")
    results = engine.search(criteria)

    titel = {r.titel for r in results}
    assert "Moderne 3.5-Zimmer-Wohnung, Neubau" in titel
    assert "Gemuetliche 2-Zimmer-Wohnung Altstadt" not in titel


def test_search_no_match_returns_empty_list():
    engine = make_engine()
    criteria = SearchCriteria(rooms=10, canton="Zug", max_price=100, property_type="Wohnung")
    assert engine.search(criteria) == []


def test_search_is_case_insensitive():
    engine = make_engine()
    criteria = SearchCriteria(canton="zug", property_type="wohnung")
    results = engine.search(criteria)
    assert len(results) >= 1
