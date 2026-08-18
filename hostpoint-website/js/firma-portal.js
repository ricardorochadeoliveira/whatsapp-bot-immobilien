// Kompletter JS-Layer des Firmen-Portals - spricht ausschliesslich direkt
// mit Supabase (siehe supabase-auth.js/supabase-session.js/supabase-data.js),
// kein eigener Server mehr involviert. DOM-IDs/Struktur unveraendert
// gegenueber der frueheren Backend-Version.
let pendingMfaToken = null;
let pendingMfaFactorId = null;
let recoveryToken = null;
let editingInseratId = null;
let currentFirma = null;
let inserateById = {};

function toggleForgotPassword() {
  const step = document.getElementById('forgotPasswordStep');
  step.style.display = step.style.display === 'none' ? 'block' : 'none';
}

async function requestPasswordReset() {
  const email = document.getElementById('forgot_email').value;
  const infoEl = document.getElementById('loginInfo');
  const errEl = document.getElementById('loginError');
  errEl.textContent = ''; infoEl.textContent = '';
  try {
    const redirectTo = window.location.origin + window.location.pathname;
    await supabaseRecoverPassword(email, redirectTo);
    infoEl.textContent = 'Falls ein Konto mit dieser E-Mail existiert, wurde eine E-Mail mit einem Link zum Zuruecksetzen verschickt.';
    document.getElementById('forgotPasswordStep').style.display = 'none';
  } catch (e) {
    errEl.textContent = e.message || 'Fehler beim Anfordern.';
  }
}

async function submitPasswordReset() {
  const newPassword = document.getElementById('reset_password').value;
  const errEl = document.getElementById('resetError');
  errEl.textContent = '';
  const policyError = validatePasswordStrength(newPassword);
  if (policyError) { errEl.textContent = policyError; return; }
  try {
    await supabaseUpdatePasswordWithRecoveryToken(recoveryToken, newPassword);
    recoveryToken = null;
    document.getElementById('resetPasswordCard').style.display = 'none';
    document.getElementById('loginInfo').textContent = 'Passwort geaendert. Du kannst dich jetzt einloggen.';
  } catch (e) {
    errEl.textContent = e.message || 'Fehler beim Zuruecksetzen.';
  }
}

function findVerifiedTotpFactor(user) {
  return (user.factors || []).find(f => f.factor_type === 'totp' && f.status === 'verified') || null;
}

async function ensureOwnFirma(authUserId, email) {
  let firma = await fetchOwnFirma();
  if (!firma) {
    const pendingName = localStorage.getItem('wc_pending_firma_name') || (email ? email.split('@')[0] : 'Neue Firma');
    firma = await createOwnFirma(authUserId, pendingName, email);
    localStorage.removeItem('wc_pending_firma_name');
  }
  return firma;
}

async function login() {
  const email = document.getElementById('login_email').value;
  const password = document.getElementById('login_password').value;
  const errEl = document.getElementById('loginError');
  errEl.textContent = '';
  try {
    const data = await supabaseSignIn(email, password);
    const factor = findVerifiedTotpFactor(data.user || {});
    if (factor) {
      pendingMfaToken = data.access_token;
      pendingMfaFactorId = factor.id;
      document.getElementById('loginStep').style.display = 'none';
      document.getElementById('mfaLoginStep').style.display = 'block';
      return;
    }
    saveSession(data);
    const firma = await ensureOwnFirma(data.user.id, data.user.email);
    showDashboard(firma);
  } catch (e) {
    errEl.textContent = e.message || 'Login fehlgeschlagen.';
  }
}

async function submitMfaLogin() {
  const code = document.getElementById('mfa_login_code').value;
  const errEl = document.getElementById('loginError');
  errEl.textContent = '';
  try {
    const data = await supabaseChallengeAndVerifyTotp(pendingMfaToken, pendingMfaFactorId, code);
    pendingMfaToken = null; pendingMfaFactorId = null;
    document.getElementById('mfaLoginStep').style.display = 'none';
    document.getElementById('loginStep').style.display = 'block';
    saveSession(data);
    const firma = await ensureOwnFirma(data.user.id, data.user.email);
    showDashboard(firma);
  } catch (e) {
    errEl.textContent = e.message || 'Code ungueltig.';
  }
}

async function signup() {
  const name = document.getElementById('signup_name').value;
  const email = document.getElementById('signup_email').value;
  const password = document.getElementById('signup_password').value;
  const errEl = document.getElementById('signupError');
  const infoEl = document.getElementById('signupInfo');
  errEl.textContent = ''; infoEl.textContent = '';

  const policyError = validatePasswordStrength(password);
  if (policyError) { errEl.textContent = policyError; return; }

  try {
    const data = await supabaseSignUp(email, password);
    localStorage.setItem('wc_pending_firma_name', name);

    if (data.access_token) {
      // Keine E-Mail-Bestaetigung noetig - Session sofort da, Firma-Zeile
      // gleich anlegen und direkt einloggen.
      saveSession(data);
      const firma = await ensureOwnFirma(data.user.id, data.user.email);
      showDashboard(firma);
    } else {
      infoEl.textContent = 'Registriert. Bitte zuerst die Bestaetigungsmail bestaetigen, dann einloggen.';
    }
  } catch (e) {
    errEl.textContent = e.message || 'Registrierung fehlgeschlagen.';
  }
}

async function logout() {
  const session = loadSession();
  if (session) await supabaseSignOut(session.access_token);
  clearSession();
  currentFirma = null;
  document.getElementById('dashboard').style.display = 'none';
  document.getElementById('authForms').style.display = 'block';
}

async function showDashboard(firma) {
  currentFirma = firma;
  document.getElementById('authForms').style.display = 'none';
  document.getElementById('dashboard').style.display = 'block';
  document.getElementById('firmaName').textContent = firma.name + ' (' + firma.email + ')';
  document.getElementById('profile_name').value = firma.name;
  await loadInserate();
  await loadLeads();
  await loadMfaStatus();
}

async function saveProfile() {
  const name = document.getElementById('profile_name').value;
  const errEl = document.getElementById('profileError');
  const infoEl = document.getElementById('profileInfo');
  errEl.textContent = ''; infoEl.textContent = '';
  try {
    const updated = await updateOwnFirma(currentFirma.id, name);
    currentFirma = updated;
    document.getElementById('firmaName').textContent = updated.name + ' (' + updated.email + ')';
    infoEl.textContent = 'Gespeichert.';
  } catch (e) {
    errEl.textContent = e.message || 'Fehler beim Speichern.';
  }
}

async function loadMfaStatus() {
  const errEl = document.getElementById('mfaError');
  try {
    const token = await getValidAccessToken();
    const user = await supabaseGetUser(token);
    const active = !!findVerifiedTotpFactor(user);
    const el = document.getElementById('mfaStatusText');
    el.textContent = active ? 'aktiv ✓' : 'nicht aktiviert';
    el.className = active ? 'mfa-active' : '';
    document.getElementById('mfaEnrollBtn').style.display = active ? 'none' : 'inline-block';
  } catch (e) {
    errEl.textContent = e.message || '';
  }
}

async function startMfaEnroll() {
  const errEl = document.getElementById('mfaError');
  errEl.textContent = '';
  try {
    const token = await getValidAccessToken();
    const data = await supabaseEnrollTotp(token);
    pendingMfaFactorId = data.id;
    document.getElementById('mfaQrCode').src = data.totp.qr_code;
    document.getElementById('mfaSecret').textContent = data.totp.secret;
    document.getElementById('mfaEnrollDetails').style.display = 'block';
    document.getElementById('mfaEnrollBtn').style.display = 'none';
  } catch (e) {
    errEl.textContent = e.message || 'Fehler beim Starten der 2FA-Einrichtung.';
  }
}

async function activateMfa() {
  const code = document.getElementById('mfa_activate_code').value;
  const errEl = document.getElementById('mfaError');
  errEl.textContent = '';
  try {
    const token = await getValidAccessToken();
    const data = await supabaseChallengeAndVerifyTotp(token, pendingMfaFactorId, code);
    saveSession(data); // neue aal2-Session
    document.getElementById('mfaEnrollDetails').style.display = 'none';
    document.getElementById('mfaStatusText').textContent = 'aktiv ✓';
    document.getElementById('mfaStatusText').className = 'mfa-active';
  } catch (e) {
    errEl.textContent = e.message || 'Code ungueltig.';
  }
}

function inseratFormValues() {
  return {
    titel: document.getElementById('f_titel').value,
    beschreibung: document.getElementById('f_beschreibung').value || null,
    typ: document.getElementById('f_typ').value,
    objekttyp: document.getElementById('f_objekttyp').value,
    zimmer: parseFloat(document.getElementById('f_zimmer').value),
    flaeche_m2: parseFloat(document.getElementById('f_flaeche').value),
    preis: parseInt(document.getElementById('f_preis').value, 10),
    kanton: document.getElementById('f_kanton').value,
    ort: document.getElementById('f_ort').value,
    hat_garten: document.getElementById('f_garten').checked,
  };
}

function resetInseratForm() {
  document.getElementById('f_titel').value = '';
  document.getElementById('f_typ').value = 'miete';
  document.getElementById('f_objekttyp').value = 'Wohnung';
  document.getElementById('f_zimmer').value = 3;
  document.getElementById('f_flaeche').value = 70;
  document.getElementById('f_preis').value = 2000;
  document.getElementById('f_kanton').value = '';
  document.getElementById('f_ort').value = '';
  document.getElementById('f_beschreibung').value = '';
  document.getElementById('f_garten').checked = false;
}

function startInseratEdit(i) {
  editingInseratId = i.id;
  document.getElementById('f_titel').value = i.titel;
  document.getElementById('f_typ').value = i.typ;
  document.getElementById('f_objekttyp').value = i.objekttyp;
  document.getElementById('f_zimmer').value = i.zimmer;
  document.getElementById('f_flaeche').value = i.flaeche_m2;
  document.getElementById('f_preis').value = i.preis;
  document.getElementById('f_kanton').value = i.kanton;
  document.getElementById('f_ort').value = i.ort;
  document.getElementById('f_beschreibung').value = i.beschreibung || '';
  document.getElementById('f_garten').checked = i.hat_garten;
  document.getElementById('inseratFormTitle').textContent = 'Inserat bearbeiten';
  document.getElementById('inseratCancelBtn').style.display = 'inline-block';
  document.getElementById('f_bilder_label').style.display = 'none';
  document.getElementById('inseratFormTitle').scrollIntoView({ behavior: 'smooth' });
}

function cancelInseratEdit() {
  editingInseratId = null;
  document.getElementById('inseratFormTitle').textContent = 'Neues Inserat';
  document.getElementById('inseratCancelBtn').style.display = 'none';
  document.getElementById('f_bilder_label').style.display = '';
  resetInseratForm();
}

async function submitInserat() {
  const values = inseratFormValues();
  const errEl = document.getElementById('inseratError');
  errEl.textContent = '';
  try {
    if (editingInseratId) {
      await updateInserat(editingInseratId, values);
    } else {
      const created = await createInserat(currentFirma.id, values);
      const bilderInput = document.getElementById('f_bilder');
      if (bilderInput.files.length > 0) {
        await uploadBilder(currentFirma.id, created.id, [], bilderInput.files);
        bilderInput.value = '';
      }
    }
    cancelInseratEdit();
    loadInserate();
  } catch (e) {
    errEl.textContent = e.message || 'Fehler beim Speichern.';
  }
}

function td(text) {
  const cell = document.createElement('td');
  cell.textContent = text;
  return cell;
}

async function loadInserate() {
  let items;
  try {
    items = await listOwnInserate();
  } catch {
    return;
  }
  inserateById = Object.fromEntries(items.map(i => [i.id, i]));
  const tbody = document.querySelector('#inserateTable tbody');
  tbody.innerHTML = '';
  for (const i of items) {
    const tr = document.createElement('tr');
    const nextStatus = i.status === 'aktiv' ? 'deaktiviert' : 'aktiv';

    const thumbCell = document.createElement('td');
    if (i.bilder && i.bilder.length > 0) {
      const img = document.createElement('img');
      img.className = 'thumb';
      img.src = i.bilder[0];
      img.alt = i.titel;
      thumbCell.appendChild(img);
    } else {
      const placeholder = document.createElement('div');
      placeholder.className = 'thumb-placeholder';
      thumbCell.appendChild(placeholder);
    }
    tr.appendChild(thumbCell);

    tr.appendChild(td(i.titel));
    tr.appendChild(td(`${i.ort}, ${i.kanton}`));
    tr.appendChild(td(i.zimmer));
    tr.appendChild(td(i.preis));
    const statusCell = td(i.status);
    statusCell.className = `status-${i.status}`;
    tr.appendChild(statusCell);

    const editCell = document.createElement('td');
    const editBtn = document.createElement('button');
    editBtn.className = 'secondary';
    editBtn.textContent = 'Bearbeiten';
    editBtn.addEventListener('click', () => startInseratEdit(i));
    editCell.appendChild(editBtn);
    tr.appendChild(editCell);

    const actionCell = document.createElement('td');
    const btn = document.createElement('button');
    btn.className = 'secondary';
    btn.textContent = nextStatus === 'aktiv' ? 'Aktivieren' : 'Deaktivieren';
    btn.addEventListener('click', () => toggleStatus(i.id, nextStatus));
    actionCell.appendChild(btn);
    tr.appendChild(actionCell);

    const bilderCell = document.createElement('td');
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.multiple = true;
    fileInput.accept = 'image/jpeg,image/png,image/webp';
    fileInput.style.display = 'none';
    fileInput.addEventListener('change', async () => {
      if (fileInput.files.length === 0) return;
      try {
        await uploadBilder(currentFirma.id, i.id, i.bilder || [], fileInput.files);
      } catch (e) {
        alert(e.message || 'Fehler beim Bild-Upload.');
      }
      loadInserate();
    });
    const addBtn = document.createElement('button');
    addBtn.className = 'secondary';
    addBtn.textContent = 'Bilder +';
    addBtn.addEventListener('click', () => fileInput.click());
    bilderCell.appendChild(addBtn);
    bilderCell.appendChild(fileInput);
    tr.appendChild(bilderCell);

    tbody.appendChild(tr);
  }
}

async function toggleStatus(id, status) {
  try {
    await setInseratStatus(id, status);
  } catch (e) {
    alert(e.message || 'Fehler beim Aendern des Status.');
  }
  loadInserate();
}

async function loadLeads() {
  let items;
  try {
    items = await listOwnLeads();
  } catch {
    return;
  }
  const tbody = document.querySelector('#leadsTable tbody');
  tbody.innerHTML = '';
  for (const l of items) {
    const titel = inserateById[l.immobilie_id] ? inserateById[l.immobilie_id].titel : l.immobilie_id;
    const tr = document.createElement('tr');
    tr.appendChild(td(titel));
    tr.appendChild(td(l.status));
    tr.appendChild(td(l.erstellt_am));
    tbody.appendChild(tr);
  }
}

// Beim Laden: erst pruefen, ob wir aus einem Passwort-Reset-Link kommen
// (Supabase haengt access_token/type=recovery als URL-Fragment an), sonst
// pruefen ob schon eine gueltige lokale Session existiert.
(async function init() {
  const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  if (hashParams.get('type') === 'recovery' && hashParams.get('access_token')) {
    recoveryToken = hashParams.get('access_token');
    document.getElementById('resetPasswordCard').style.display = 'block';
    history.replaceState(null, '', window.location.pathname);
    return;
  }

  const token = await getValidAccessToken();
  if (!token) return;
  try {
    const firma = await fetchOwnFirma();
    if (firma) showDashboard(firma);
  } catch {
    /* Session ungueltig - Login-Formular bleibt sichtbar */
  }
})();
