// 1:1-Transliteration der REST-Aufrufe aus app/supabase_auth.py - gleiche
// Endpunkte, gleiche Header, gleiche Fehlermeldungs-Extraktion, nur als
// fetch() statt httpx. Wirft bei Fehlern ein Error-Objekt mit deutscher
// Meldung (message), analog zu SupabaseAuthError im Python-Pendant.

class SupabaseAuthError extends Error {}

async function _extractErrorMessage(resp) {
  try {
    const body = await resp.json();
    return body.msg || body.error_description || body.error || JSON.stringify(body);
  } catch {
    return await resp.text();
  }
}

async function supabaseSignUp(email, password) {
  const resp = await fetch(`${SUPABASE_URL}/auth/v1/signup`, {
    method: 'POST',
    headers: { apikey: SUPABASE_ANON_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw new SupabaseAuthError(await _extractErrorMessage(resp));
  return resp.json();
}

async function supabaseSignIn(email, password) {
  const resp = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
    method: 'POST',
    headers: { apikey: SUPABASE_ANON_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw new SupabaseAuthError(await _extractErrorMessage(resp));
  return resp.json();
}

async function supabaseRefresh(refreshToken) {
  const resp = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=refresh_token`, {
    method: 'POST',
    headers: { apikey: SUPABASE_ANON_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!resp.ok) throw new SupabaseAuthError(await _extractErrorMessage(resp));
  return resp.json();
}

async function supabaseGetUser(accessToken) {
  const resp = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${accessToken}` },
  });
  if (!resp.ok) throw new SupabaseAuthError('Token ungueltig oder abgelaufen.');
  return resp.json();
}

async function supabaseSignOut(accessToken) {
  // Darf nie fehlschlagen - die lokale Session wird so oder so geloescht.
  try {
    await fetch(`${SUPABASE_URL}/auth/v1/logout`, {
      method: 'POST',
      headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${accessToken}` },
    });
  } catch {
    /* ignorieren */
  }
}

async function supabaseRecoverPassword(email, redirectTo) {
  const params = redirectTo ? `?redirect_to=${encodeURIComponent(redirectTo)}` : '';
  const resp = await fetch(`${SUPABASE_URL}/auth/v1/recover${params}`, {
    method: 'POST',
    headers: { apikey: SUPABASE_ANON_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!resp.ok) throw new SupabaseAuthError(await _extractErrorMessage(resp));
}

async function supabaseUpdatePasswordWithRecoveryToken(recoveryToken, newPassword) {
  const resp = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    method: 'PUT',
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${recoveryToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: newPassword }),
  });
  if (!resp.ok) {
    const msg = await _extractErrorMessage(resp);
    throw new SupabaseAuthError(msg || 'Link ungueltig oder abgelaufen - bitte neu anfordern.');
  }
}

function decodeJwtAal(accessToken) {
  // Rein informativ (UI-Anzeige) - die eigentliche Durchsetzung passiert in
  // Postgres via mfa_ok(), nicht hier. Keine eigene Signaturpruefung noetig.
  try {
    const payloadB64 = accessToken.split('.')[1];
    const padded = payloadB64.replace(/-/g, '+').replace(/_/g, '/').padEnd(payloadB64.length + (4 - (payloadB64.length % 4)) % 4, '=');
    const payload = JSON.parse(atob(padded));
    return payload.aal || null;
  } catch {
    return null;
  }
}

async function supabaseEnrollTotp(accessToken, friendlyName = 'Authenticator App') {
  const resp = await fetch(`${SUPABASE_URL}/auth/v1/factors`, {
    method: 'POST',
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ factor_type: 'totp', friendly_name: friendlyName }),
  });
  if (!resp.ok) throw new SupabaseAuthError(await _extractErrorMessage(resp));
  return resp.json();
}

async function supabaseChallengeAndVerifyTotp(accessToken, factorId, code) {
  const headers = { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' };

  const challengeResp = await fetch(`${SUPABASE_URL}/auth/v1/factors/${factorId}/challenge`, {
    method: 'POST',
    headers,
  });
  if (!challengeResp.ok) throw new SupabaseAuthError(await _extractErrorMessage(challengeResp));
  const challenge = await challengeResp.json();

  const verifyResp = await fetch(`${SUPABASE_URL}/auth/v1/factors/${factorId}/verify`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ challenge_id: challenge.id, code }),
  });
  if (!verifyResp.ok) {
    const msg = await _extractErrorMessage(verifyResp);
    throw new SupabaseAuthError(msg || 'Code ungueltig oder abgelaufen.');
  }
  return verifyResp.json();
}
