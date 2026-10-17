#!/bin/zsh
# Dvoklik: pravi lokalno okruženje (samo prvi put), pokreće server i otvara pregledač.
cd "$(dirname "$0")"

# Prozor ostaje otvoren posle greške, da se vidi šta je pošlo naopako.
greska() {
  echo
  echo "GREŠKA: $1"
  read -k1 "?Pritisni bilo koji taster da zatvoriš prozor…"
  exit 1
}

# Folder preuzet kao ZIP iz pregledača nosi karantin; skida se da macOS ne blokira skripte.
xattr -dr com.apple.quarantine . 2>/dev/null

if [[ -L snimci && ! -d snimci ]]; then
  greska "Snimci su na SSD-u ($(readlink snimci)). Priključi SSD pa pokreni ponovo."
fi

if curl -s -o /dev/null http://127.0.0.1:8400/; then
  echo "Aplikacija već radi, otvaram pregledač."
  open http://localhost:8400
  exit 0
fi

if ! python3 -c "import sys" 2>/dev/null; then
  xcode-select --install 2>/dev/null
  greska "Nema Python-a. Otvorio se prozor za instalaciju Apple alata (Command Line Tools); instaliraj ih pa ponovo pokreni ovaj fajl."
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Prvo pokretanje: pravim okruženje u .venv (traje nekoliko minuta, treba internet)…"
  rm -rf .venv
  python3 -m venv .venv || greska "Ne mogu da napravim .venv"
  .venv/bin/pip install --upgrade pip || greska "Ne mogu da ažuriram pip; proveri internet"
fi
if ! .venv/bin/python -c "import gphoto2, cv2, rawpy, numpy" 2>/dev/null; then
  echo "Instaliram biblioteke (gphoto2, OpenCV, rawpy)…"
  .venv/bin/pip install gphoto2 opencv-python-headless rawpy numpy || greska "Instalacija biblioteka nije uspela; proveri internet"
fi

echo
echo "Pokrećem aplikaciju. Ne zatvaraj ovaj prozor dok koristiš aparat."
(sleep 2 && open http://localhost:8400) &
.venv/bin/python server.py || greska "Server je stao. Poruka iznad objašnjava zašto."
