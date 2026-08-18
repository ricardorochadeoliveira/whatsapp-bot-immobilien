// Verwaltet die Firmen-Session im Browser. Ohne eigenen Server kann kein
// httpOnly-Cookie gesetzt werden (bewusst akzeptierter Kompromiss, siehe
// docs/produkt-abgleich.md) - Supabases eigener Standardweg fuer
// Client-only-Apps ist localStorage, dem folgen wir hier.
const SESSION_KEY = 'wc_firma_session';

function saveSession(tokenData) {
  const expiresAt = Date.now() + (tokenData.expires_in || 3600) * 1000;
  localStorage.setItem(SESSION_KEY, JSON.stringify({
    access_token: tokenData.access_token,
    refresh_token: tokenData.refresh_token,
    expires_at: expiresAt,
  }));
}

function loadSession() {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}

// Refresht bei Bedarf lazy (kein Hintergrund-Timer) - erst wenn tatsaechlich
// ein Aufruf gemacht wird und der Token in < 60s ablaeuft oder schon
// abgelaufen ist.
async function getValidAccessToken() {
  const session = loadSession();
  if (!session) return null;

  if (session.expires_at - Date.now() > 60_000) {
    return session.access_token;
  }

  try {
    const refreshed = await supabaseRefresh(session.refresh_token);
    saveSession(refreshed);
    return refreshed.access_token;
  } catch {
    clearSession();
    return null;
  }
}

// Duenner Wrapper: haengt apikey + Authorization automatisch an, Rest wie
// normales fetch(). path ist relativ zu SUPABASE_URL (z.B. '/rest/v1/immobilie').
async function authedFetch(path, options = {}) {
  const token = await getValidAccessToken();
  if (!token) throw new Error('Nicht eingeloggt.');

  const headers = Object.assign({}, options.headers, {
    apikey: SUPABASE_ANON_KEY,
    Authorization: `Bearer ${token}`,
  });
  return fetch(`${SUPABASE_URL}${path}`, Object.assign({}, options, { headers }));
}
