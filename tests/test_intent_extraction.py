from app.intent_extraction import LISTING_TOOL_DEFINITION, SEARCH_TOOL_DEFINITION


def test_search_tool_only_requires_canton():
    # rooms/max_price/property_type sind absichtlich optional, damit ein
    # Nutzer "egal" sagen kann, ohne dass Claude sie erzwingt (siehe
    # app/matching.py: matches() behandelt None bei diesen drei Feldern
    # bereits als "kein Filter").
    assert SEARCH_TOOL_DEFINITION["input_schema"]["required"] == ["canton"]


def test_listing_tool_still_requires_all_mandatory_fields():
    # Auf der Vermieter-Seite bleibt "egal" kein sinnvolles Konzept - ein
    # echtes Inserat braucht konkrete Angaben.
    assert SEARCH_TOOL_DEFINITION is not LISTING_TOOL_DEFINITION
    assert LISTING_TOOL_DEFINITION["input_schema"]["required"] == [
        "title",
        "rooms",
        "canton",
        "city",
        "price",
        "property_type",
        "living_space_m2",
        "listing_type",
    ]
