#!/bin/zsh
# Dvoklik: pravi lokalno okruženje (samo prvi put), pokreće server i otvara pregledač.
cd "$(dirname "$0")"
if [[ -L snimci && ! -d snimci ]]; then
  echo "Snimci su na SSD-u ($(readlink snimci)). Priključi SSD pa pokreni ponovo."
  read -k1 "?Pritisni bilo koji taster…"; exit 1
fi
if [[ ! -d .venv ]]; then
  echo "Prvo pokretanje: instaliram python-gphoto2 u .venv…"
  python3 -m venv .venv && .venv/bin/pip install --quiet gphoto2 || exit 1
fi
if ! .venv/bin/python -c "import cv2, rawpy" 2>/dev/null; then
  echo "Instaliram biblioteke za HDR spajanje…"
  .venv/bin/pip install --quiet opencv-python-headless rawpy numpy
fi
(sleep 1.5 && open http://localhost:8400) &
exec .venv/bin/python server.py
