"""Dummy-Inserate zum Durchtesten der Matching-Logik (Platzhalter fuer Punkt 2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Immobilie

_now = datetime.now(timezone.utc)


def build_seed_immobilien() -> list[Immobilie]:
    return [
        Immobilie(
            titel="Helle 2.5-Zimmer-Wohnung mit Balkon",
            zimmer=2.5,
            kanton="Zug",
            ort="Zug",
            preis=2100,
            objekttyp="Wohnung",
            flaeche_m2=68,
            bild_url="https://picsum.photos/seed/1/400/300",
            link="https://example.com/inserate/1",
            inseriert_am=_now - timedelta(days=1),
        ),
        Immobilie(
            titel="Moderne 3.5-Zimmer-Wohnung, Neubau",
            zimmer=3.5,
            kanton="Zug",
            ort="Baar",
            preis=2600,
            objekttyp="Wohnung",
            flaeche_m2=95,
            bild_url="https://picsum.photos/seed/2/400/300",
            link="https://example.com/inserate/2",
            inseriert_am=_now - timedelta(days=3),
        ),
        Immobilie(
            titel="Gemuetliche 2-Zimmer-Wohnung Altstadt",
            zimmer=2.0,
            kanton="Zug",
            ort="Zug",
            preis=1850,
            objekttyp="Wohnung",
            flaeche_m2=55,
            bild_url="https://picsum.photos/seed/3/400/300",
            link="https://example.com/inserate/3",
            inseriert_am=_now - timedelta(days=5),
        ),
        Immobilie(
            titel="Einfamilienhaus mit Garten",
            zimmer=5.5,
            kanton="Zuerich",
            ort="Winterthur",
            preis=3400,
            objekttyp="Haus",
            flaeche_m2=140,
            bild_url="https://picsum.photos/seed/4/400/300",
            link="https://example.com/inserate/4",
            inseriert_am=_now - timedelta(days=2),
        ),
        Immobilie(
            titel="Loft im Kreis 5",
            zimmer=3.0,
            kanton="Zuerich",
            ort="Zuerich",
            preis=3100,
            objekttyp="Loft",
            flaeche_m2=88,
            bild_url="https://picsum.photos/seed/5/400/300",
            link="https://example.com/inserate/5",
            inseriert_am=_now - timedelta(days=7),
        ),
        Immobilie(
            titel="4.5-Zimmer-Maisonette mit Seesicht",
            zimmer=4.5,
            kanton="Luzern",
            ort="Luzern",
            preis=2950,
            objekttyp="Wohnung",
            flaeche_m2=120,
            bild_url="https://picsum.photos/seed/6/400/300",
            link="https://example.com/inserate/6",
            inseriert_am=_now - timedelta(days=4),
        ),
        Immobilie(
            titel="Studio fuer Singles, zentral gelegen",
            zimmer=1.5,
            kanton="Bern",
            ort="Bern",
            preis=1450,
            objekttyp="Wohnung",
            flaeche_m2=38,
            bild_url="https://picsum.photos/seed/7/400/300",
            link="https://example.com/inserate/7",
            inseriert_am=_now - timedelta(days=6),
        ),
        Immobilie(
            titel="Reiheneinfamilienhaus mit Doppelgarage",
            zimmer=6.0,
            kanton="Zug",
            ort="Cham",
            preis=3800,
            objekttyp="Haus",
            flaeche_m2=160,
            bild_url="https://picsum.photos/seed/8/400/300",
            link="https://example.com/inserate/8",
            inseriert_am=_now - timedelta(days=8),
        ),
    ]
