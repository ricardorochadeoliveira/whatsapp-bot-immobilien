# WhatsApp-Immobilien-Bot

Ein einziger, gemeinsamer WhatsApp-Bot fuer alle: Vermieter (Firmen und
Privatpersonen) stellen ihre Inserate direkt per Chat ein, Mieter suchen per
Freitext firmenuebergreifend ueber alle Anbieter. Kein Login fuer die
WhatsApp-Nutzung noetig - Identifikation laeuft ueber die Telefonnummer.
WhatsApp selbst ist noch nicht angebunden - stattdessen simuliert ein
kleiner Web-Chat die Konversation.

## Status

**Produktrichtung:** eigenstaendiger Marktplatz-Bot, kein Login fuer
WhatsApp-Nutzer (siehe `docs/produkt-abgleich.md`, Abschnitt
"Marktplatz-Pivot"). Damit sich niemand faelschlicherweise als fremder
Anbieter ausgeben kann, durchlaufen alle per WhatsApp eingereichten Inserate
eine manuelle Freigabe im Admin-Panel, bevor sie in der Mieter-Suche
erscheinen.

**Erledigt:**
- Server/DB: Supabase-Postgres via SQLAlchemy (`app/db.py`,
  `app/models_orm.py`, `app/repository_supabase.py`). Ist `DATABASE_URL`
  in `.env` gesetzt, nutzt die App automatisch Supabase statt In-Memory-Daten.
- Intent-Extraktion mit Claude Function Calling: sowohl Mieter-Suche
  (`search_properties`) als auch Vermieter-Inserat-Erfassung
  (`submit_listing`) - `app/intent_extraction.py`.
- Matching-Engine / Suchlogik, firmenuebergreifend (`app/matching.py`).
- Matching-Job fuer neue/freigegebene Inserate + Match-Log (`app/matching_job.py`).
- Lead-Tracking: positive Antwort auf eine Match-Benachrichtigung wird als
  Lead an den Anbieter weitergeleitet.
- Simuliertes Chat-Frontend als WhatsApp-Platzhalter (`web/`) mit
  Rollenwahl Vermieter/Mieter am Gespraechsanfang.
- Admin-Pruef-Queue: neue WhatsApp-Inserate landen im Status `in_pruefung`
  und muessen im Admin-Panel freigegeben werden.
- Admin-Panel + Chat-Simulator per `ADMIN_API_KEY` geschuetzt.
- Firmen-Login (Supabase Auth) + eigenes Inserate-Portal unter `/firma` -
  fuer Firmen, die lieber ueber eine Weboberflaeche pflegen (mit Login,
  RLS-mandantengetrennt). Inserate von dort sind sofort aktiv (kein
  Pruef-Schritt, da der Login bereits eine staerkere Identitaetspruefung ist).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

In `.env` eintragen (siehe `.env.example` fuer alle Variablen und wo man sie
findet):
- `ANTHROPIC_API_KEY` (siehe console.anthropic.com)
- `DATABASE_URL` (optional): Supabase-Connection-String. Ohne diese Variable
  laeuft die App mit In-Memory-Platzhalterdaten weiter.
- `DATABASE_URL_RUNTIME`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`: nur noetig,
  wenn das Firmen-Portal (`/firma`, Login) genutzt werden soll.
- `ADMIN_API_KEY`: schuetzt Chat-Simulator + Admin-Panel.

Tabellen werden beim ersten Start automatisch angelegt/migriert und die
Immobilien-Tabelle wird einmalig mit Dummy-Inseraten befuellt, falls leer.

## Starten

```bash
uvicorn web.main:app --reload
```

Dann [http://localhost:8000](http://localhost:8000) oeffnen (Admin-Key im
Chat-Panel eintragen). Links der simulierte WhatsApp-Chat, rechts das
Admin-Panel mit Pruef-Queue, Testinserat-Formular und Match-Log.

### Kompletten Ablauf durchspielen

**Als Vermieter:**
1. Im Chat schreiben: `Vermieter`
2. `Firma` oder `Privatperson` waehlen, Name/Firmenname angeben.
3. Inserat in einem Satz beschreiben (braucht gueltigen
   `ANTHROPIC_API_KEY` mit Guthaben).
4. Inserat landet im Status `in_pruefung` - im Admin-Panel links unter
   "Inserate zur Pruefung" freigeben oder ablehnen.
5. Nach Freigabe bekommt der Vermieter eine Bestaetigung im Chat, und das
   Inserat ist ab sofort fuer alle Mieter-Suchen sichtbar.

**Als Mieter:**
1. Im Chat schreiben: `Mieter`
2. Suche beschreiben, z.B. `2.5-Zimmer-Wohnung in Zug, max 2200.-`
3. Bot zeigt Treffer aus allen freigegebenen Inseraten (aller Anbieter) und
   fragt nach einem Suchabo -> mit `ja` antworten.
4. Neues passendes Inserat (von irgendeinem Anbieter) -> 🔔-Benachrichtigung
   im Chat + Frage nach Interesse -> bei `ja` wird ein Lead an den
   jeweiligen Anbieter weitergeleitet.

Mit `reset` kann eine Konversation jederzeit neu gestartet werden (z.B. um
Vermieter- und Mieter-Flow mit derselben Testnummer durchzuspielen).

Firmen-Login/Inserate-Portal separat unter `/firma` (Signup + Login +
eigene Inserate/Leads verwalten, siehe `docs/produkt-abgleich.md`).

## Tests

Matching-Engine, Matching-Job und Chat-Konversationslogik (inkl. Rollenwahl,
Vermieter- und Mieter-Pfad) sind ohne Claude-API-Aufruf testbar (LLM-Aufruf
wird gemockt):

```bash
pytest
```

Die Intent-Extraktion selbst (`app/intent_extraction.py`) laesst sich direkt
gegen die echte Claude-API testen, z.B. interaktiv im Web-Chat.

## Naechste Schritte

Siehe `docs/launch-checkliste.md` fuer die vollstaendige Liste. Kurzfassung:

- Admin-Panel zu echter Inserate-Verwaltung ausbauen (Bearbeiten, nicht nur
  Freigeben/Ablehnen).
- Hosting fuer die FastAPI-App selbst klaeren (Supabase liefert nur die DB).
- Echte WhatsApp-Anbindung (WhatsApp Business API ueber Twilio/360dialog)
  anstelle von `web/` als Frontend fuer `app/chat_service.py`.
- Zusaetzlicher Verifizierungsschritt fuer WhatsApp-Vermieter erwaegen
  (aktuell nur Telefonnummer + manuelle Admin-Pruefung).
- Falls spaeter eine Immobilienfirma mit eigenem CRM/Feed andocken will:
  neue `ImmobilienRepository`-Implementierung schreiben und in
  `app/bootstrap.py` einstecken - der Rest der App bleibt unveraendert.
