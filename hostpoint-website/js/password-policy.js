// 1:1-Transliteration von app/password_policy.py - gleiche Regel, damit
// Client- und (falls noch aktiv) Server-Pruefung nie auseinanderlaufen.
const PASSWORD_MIN_LENGTH = 8;

function validatePasswordStrength(password) {
  if (!password || password.length < PASSWORD_MIN_LENGTH) {
    return `Passwort muss mindestens ${PASSWORD_MIN_LENGTH} Zeichen lang sein.`;
  }
  if (!/[a-zA-Z]/.test(password)) {
    return 'Passwort muss mindestens einen Buchstaben enthalten.';
  }
  if (!/[0-9]/.test(password)) {
    return 'Passwort muss mindestens eine Ziffer enthalten.';
  }
  return null;
}
