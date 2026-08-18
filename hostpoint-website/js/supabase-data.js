// Direkter PostgREST-/Storage-Zugriff (ersetzt die bisherigen
// /api/firma/*-Datenendpunkte). Autorisierung passiert ausschliesslich
// ueber Row-Level-Security (scripts/migrate_rls_native_auth.py) - hier wird
// bewusst NICHT mehr geprueft, ob ein Inserat "der eigenen Firma gehoert",
// das ist jetzt Aufgabe der Datenbank.

const IMMOBILIEN_BUCKET = 'immobilien-bilder';
const MAX_BILD_BYTES = 5 * 1024 * 1024;
const ALLOWED_BILD_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

function newId() {
  // Analog zu uuid.uuid4().hex[:12] in Python.
  return crypto.randomUUID().replace(/-/g, '').slice(0, 12);
}

async function _asJson(resp, what) {
  if (!resp.ok) {
    let detail = '';
    try {
      const body = await resp.json();
      detail = body.message || body.msg || JSON.stringify(body);
    } catch {
      detail = await resp.text();
    }
    throw new Error(`${what} fehlgeschlagen: ${detail}`);
  }
  if (resp.status === 204) return null;
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

// --- Firma / Profil ---------------------------------------------------

async function fetchOwnFirma() {
  const resp = await authedFetch('/rest/v1/firma?select=*');
  const rows = await _asJson(resp, 'Firma laden');
  return rows && rows.length > 0 ? rows[0] : null;
}

async function createOwnFirma(authUserId, name, email) {
  const resp = await authedFetch('/rest/v1/firma', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Prefer: 'return=representation' },
    body: JSON.stringify({
      id: newId(),
      name,
      typ: 'firma',
      email,
      auth_user_id: authUserId,
    }),
  });
  const rows = await _asJson(resp, 'Firmenprofil anlegen');
  return rows[0];
}

async function updateOwnFirma(firmaId, name) {
  const resp = await authedFetch(`/rest/v1/firma?id=eq.${firmaId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Prefer: 'return=representation' },
    body: JSON.stringify({ name }),
  });
  const rows = await _asJson(resp, 'Profil speichern');
  return rows[0];
}

// --- Inserate -----------------------------------------------------------

const EDITABLE_INSERAT_FIELDS = [
  'titel', 'beschreibung', 'typ', 'zimmer', 'kanton', 'ort',
  'preis', 'objekttyp', 'flaeche_m2', 'hat_garten',
];

async function listOwnInserate() {
  const resp = await authedFetch('/rest/v1/immobilie?select=*&order=inseriert_am.desc');
  return _asJson(resp, 'Inserate laden');
}

async function createInserat(firmaId, fields) {
  const body = {};
  for (const key of EDITABLE_INSERAT_FIELDS) body[key] = fields[key];
  Object.assign(body, {
    id: newId(),
    firma_id: firmaId,
    status: 'aktiv',
    bilder: [],
    link: 'https://example.com/inserate/neu',
    inseriert_am: new Date().toISOString(),
  });
  const resp = await authedFetch('/rest/v1/immobilie', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Prefer: 'return=representation' },
    body: JSON.stringify(body),
  });
  const rows = await _asJson(resp, 'Inserat erstellen');
  return rows[0];
}

async function updateInserat(immobilieId, fields) {
  const body = {};
  for (const key of EDITABLE_INSERAT_FIELDS) body[key] = fields[key];
  const resp = await authedFetch(`/rest/v1/immobilie?id=eq.${immobilieId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Prefer: 'return=representation' },
    body: JSON.stringify(body),
  });
  const rows = await _asJson(resp, 'Inserat speichern');
  return rows[0];
}

async function setInseratStatus(immobilieId, status) {
  const resp = await authedFetch(`/rest/v1/immobilie?id=eq.${immobilieId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  await _asJson(resp, 'Status aendern');
}

// --- Bilder (direkter Storage-Upload, kein Service-Role-Key im Browser) -

async function uploadBilder(firmaId, immobilieId, existingBilder, fileList) {
  if (existingBilder.length + fileList.length > 6) {
    throw new Error('Maximal 6 Bilder pro Inserat erlaubt.');
  }

  const token = await getValidAccessToken();
  if (!token) throw new Error('Nicht eingeloggt.');

  const neueUrls = [];
  for (const file of fileList) {
    if (!ALLOWED_BILD_TYPES.has(file.type)) {
      throw new Error(`Ungueltiger Bildtyp '${file.type}'. Erlaubt: JPEG, PNG, WebP.`);
    }
    if (file.size > MAX_BILD_BYTES) {
      throw new Error('Bild ist zu gross (max. 5 MB).');
    }
    const safeName = file.name.replace(/[^a-zA-Z0-9.\-_]/g, '') || 'bild';
    const path = `${firmaId}/${immobilieId}/${crypto.randomUUID()}_${safeName}`;

    const resp = await fetch(`${SUPABASE_URL}/storage/v1/object/${IMMOBILIEN_BUCKET}/${path}`, {
      method: 'POST',
      headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${token}`, 'Content-Type': file.type },
      body: file,
    });
    await _asJson(resp, 'Bild-Upload');
    neueUrls.push(`${SUPABASE_URL}/storage/v1/object/public/${IMMOBILIEN_BUCKET}/${path}`);
  }

  const alleBilder = [...existingBilder, ...neueUrls];
  const resp = await authedFetch(`/rest/v1/immobilie?id=eq.${immobilieId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Prefer: 'return=representation' },
    body: JSON.stringify({ bilder: alleBilder }),
  });
  const rows = await _asJson(resp, 'Bilder speichern');
  return rows[0];
}

// --- Leads ----------------------------------------------------------------

async function listOwnLeads() {
  const resp = await authedFetch('/rest/v1/lead?select=*&order=erstellt_am.desc');
  return _asJson(resp, 'Leads laden');
}
