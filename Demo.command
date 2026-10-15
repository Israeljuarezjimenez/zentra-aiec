#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Primero instala las dependencias con: uv sync'
  read '?Pulsa Enter para cerrar.'
  exit 1
fi
.venv/bin/python -m aiec demo
