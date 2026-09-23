#!/bin/zsh
# Dvoklik: pravi lokalno okruženje (samo prvi put), pokreće server i otvara pregledač.
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo "Prvo pokretanje: instaliram python-gphoto2 u .venv…"
  python3 -m venv .venv && .venv/bin/pip install --quiet gphoto2 || exit 1
fi
(sleep 1.5 && open http://localhost:8400) &
exec .venv/bin/python server.py
