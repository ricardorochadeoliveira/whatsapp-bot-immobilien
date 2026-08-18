// Gemeinsames Skript fuer alle statischen Seiten - bisher nur der mobile
// Nav-Toggle, um dieselben paar Zeilen nicht in jeder HTML-Datei zu duplizieren.
document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.querySelector('.nav-toggle');
  const links = document.querySelector('.nav-links');
  if (toggle && links) {
    toggle.addEventListener('click', () => links.classList.toggle('open'));
  }
});
