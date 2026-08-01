# Launch-Checkliste

Sammlung aller Punkte, die waehrend der Entwicklung aufgefallen sind und vor
einem echten Go-Live geprueft/erledigt werden muessen. Nicht chronologisch
sortiert, sondern nach Bereich. Bei Erledigung bitte abhaken.

## Impersonation / Injection / XSS

- [x] **Sicherheitsanalyse durchgefuehrt und Luecke behoben (2026-07-30).**
      Siehe `docs/produkt-abgleich.md`, Abschnitt "Sicherheitsanalyse" fuer
      Details: Admin-Rechte sind strukturell nicht per Chat-Text erreichbar,
      System-Prompts gegen Prompt-Injection gehaertet, echte XSS-Luecke in
      Admin-/Firmen-Oberflaeche gefunden+behoben+live verifiziert,
      Firmennamen-Kollisionswarnung fuer die Pruef-Queue ergaenzt.
- [ ] Namenskollisions-Warnung ist nur ein Hinweis, keine Blockade - der
      Admin muss beim Freigeben selbst aufpassen. Bei Bedarf spaeter
      verschaerfen (z.B. Freigabe fuer Konflikt-Faelle zusaetzlich bestaetigen
      lassen).

## Supabase Auth (Firmen-Login)

- [ ] **"Confirm email" pruefen/aktivieren.** Beim Testen war unklar, wo genau
      dieser Schalter in der aktuellen Supabase-Dashboard-Version sitzt
      (nicht unter Authentication -> Sign In/Providers -> Email, wo er
      erwartet wurde). Vor Launch sicherstellen, dass Firmen-Signups eine
      echte E-Mail-Bestaetigung durchlaufen muessen - sonst kann sich jemand
      mit einer fremden/falschen Adresse als Firma registrieren.
- [ ] **Eigener SMTP-Anbieter hinterlegen.** Supabase's Standard-E-Mail-Versand
      hat ein sehr niedriges Rate-Limit (beim Testen nach wenigen Signups
      bereits erreicht). Fuer den Produktivbetrieb unter Authentication ->
      Settings -> SMTP Settings einen eigenen Anbieter (z.B. Postmark,
      SendGrid, Resend) einrichten.
- [ ] `SUPABASE_SERVICE_ROLE_KEY` in `.env` wurde nur fuer einmalige
      Testzwecke eingetragen (Testnutzer ohne E-Mail-Versand anlegen). Diesen
      Key besonders schuetzen (umgeht RLS + Auth komplett) - im normalen
      App-Code nirgends verwenden, nur fuer Admin-/Migrationsskripte.

## Datenbank / RLS

- [ ] Die App laeuft aktuell fuer den WhatsApp-/Matching-Pfad noch teils
      ueber die `postgres`-Superuser-Rolle (siehe `app/bootstrap.py`), die RLS
      umgeht. Firmen-Login/-Dashboard laeuft bereits korrekt ueber die
      eingeschraenkte `app_runtime`-Rolle. Vor Launch pruefen, ob weitere
      Pfade (z.B. echte WhatsApp-Webhooks) ebenfalls RLS-konform laufen
      sollten.
- [ ] `DATABASE_URL` (Superuser) nur fuer Migrationen/Admin-Skripte
      verwenden, nie in einem oeffentlich erreichbaren Endpunkt.

## Kostenschutz (Claude-API)

- [x] **Rate-Limiting umgesetzt und live getestet (2026-07-30).**
      `app/rate_limiter.py`: pro Telefonnummer max. `RATE_LIMIT_PER_PHONE_PER_MINUTE`
      (Default 6) / `RATE_LIMIT_PER_PHONE_PER_DAY` (Default 200) Claude-Aufrufe,
      global max. `RATE_LIMIT_GLOBAL_PER_MINUTE` (Default 60) als Sicherheitsnetz
      gegen viele Nummern gleichzeitig. Nachrichten laenger als 800 Zeichen werden
      abgelehnt, bevor sie an Claude gehen; Konversationshistorie wird auf die
      letzten 20 Nachrichten gekappt (begrenzt Tokens pro Anfrage bei langen
      Chats). Live verifiziert: bei engem Test-Limit (2/Minute) loeste die
      dritte Nachricht nachweislich **keinen** dritten Claude-API-Aufruf mehr
      aus (in den Logs bestaetigt).
- [ ] Aktuell In-Memory (pro Prozess) - falls die App spaeter auf mehreren
      Instanzen laeuft (z.B. Load-Balancing), muesste der Zaehler ueber einen
      gemeinsamen Speicher (Redis o.ae.) laufen, sonst umgeht ein Angreifer
      das Limit durch Verteilung auf mehrere Instanzen.
- [ ] Kein Hard-Cap auf tatsaechliche Token-/Kostenbudget (nur Anfrage-Anzahl).
      Fuer noch praezisere Kostenkontrolle koennte spaeter das echte
      `usage`-Feld aus der Claude-Antwort mitgezaehlt und ein Tagesbudget
      hart durchgesetzt werden.

## Admin-/Test-Oberflaechen

- [x] **Admin-Panel geschuetzt (2026-07-28).** `/api/chat*` und `/api/admin/*`
      verlangen jetzt den Header `X-Admin-Key` (Wert aus `ADMIN_API_KEY` in
      `.env`, im Chat-UI links oben einmalig eintragen - wird lokal
      gespeichert). Ohne gesetzten `ADMIN_API_KEY` bleibt der Bereich
      unprotected (nur fuer lokale Entwicklung gedacht) - **vor einem
      oeffentlich erreichbaren Server unbedingt `ADMIN_API_KEY` setzen und
      den generierten Wert nicht mit anderen teilen.** Firmen-Portal
      (`/firma`, `/api/firma/*`) ist davon unabhaengig, laeuft eigenstaendig
      ueber Supabase Auth.
- [ ] Simuliertes Chat-Frontend (`web/`) ist ein WhatsApp-Platzhalter, kein
      Teil des finalen Produkts - vor Launch durch echte WhatsApp-Anbindung
      ersetzen (siehe Meta/BSP-Punkte unten).
- [x] **Schutz gegen Fake-Inserate (2026-07-29).** Seit dem Marktplatz-Pivot
      (kein Login fuer WhatsApp-Vermieter) durchlaufen alle per Chat
      eingereichten Inserate eine manuelle Freigabe im Admin-Panel (Status
      `in_pruefung` -> `aktiv`/`abgelehnt`), bevor sie in der Mieter-Suche
      erscheinen. Das ist bewusst ein einfacher erster Schritt - siehe
      "Offene Punkte" in `docs/produkt-abgleich.md` (kein Verifizierungscode,
      keine Wiederholungstaeter-Erkennung).

## Hosting

- [ ] Supabase liefert nur die Datenbank, nicht das Hosting fuer die
      FastAPI-App selbst. Hosting-Entscheidung noch offen (z.B. Railway,
      Render, Fly.io, eigener VPS).
- [ ] Die App braucht eine oeffentlich erreichbare HTTPS-URL, sobald WhatsApp
      angebunden wird (Meta ruft den Webhook nur auf einer echten,
      erreichbaren Adresse auf - kein localhost).

## WhatsApp / Meta

- [ ] WhatsApp Business Platform ueber Business Solution Provider (Twilio,
      360dialog o.ae.) anbinden statt direkt gegen die rohe Meta-API (siehe
      Chef-Spezifikation, Abschnitt 4.7).
- [ ] **Message-Template zur Genehmigung bei Meta einreichen** fuer die
      proaktiven "neues Inserat gefunden"-Benachrichtigungen (Punkt 5) - Meta
      erlaubt freien Text nur innerhalb 24h nach der letzten Kundennachricht,
      ausserhalb davon braucht es ein genehmigtes Template. Genehmigung
      dauert typischerweise Stunden bis wenige Tage - rechtzeitig einreichen.
- [ ] Business-Verifizierung bei Meta fuer eigene Nummer + hoehere
      Nachrichtenlimits (statt Test-Nummer).
- [ ] Opt-in/Opt-out-Pflicht beachten (siehe Chef-Spezifikation 4.7): Nutzer
      muessen zuerst selbst schreiben, "STOP" muss das gespeicherte Profil
      vollstaendig loeschen. Datenschutzerklaerung noetig.

## Offene Geschaeftsentscheidungen (mit dem Chef klaeren, nicht selbst entscheiden)

- [ ] Genaue Rabatthoehe fuer Gruendungsmitglieder (Spezifikation nennt nur
      "z.B. 30-40%").
- [ ] Ob die Abweichung "kein oeffentliches Web-Suchportal, nur WhatsApp +
      Firmen-Portal" so vom Chef akzeptiert ist (siehe
      `docs/produkt-abgleich.md`).
- [ ] Definition "qualifizierter Lead" fuer den WhatsApp-Kanal (Entwickler
      darf hier laut Spezifikation selbst entscheiden, aber sollte kurz
      rueckgemeldet werden). Lead-Zaehlung pro Firma ist bereits umgesetzt
      (`Lead`-Tabelle, sichtbar im Firmen-Portal unter `/firma`) - aktuell
      zaehlt jede positive Antwort auf eine Match-Benachrichtigung als Lead,
      das kann spaeter verfeinert werden.

## Verweis

Siehe auch `docs/produkt-abgleich.md` fuer den technischen Hintergrund zu
den meisten dieser Punkte.
