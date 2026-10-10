#!/usr/bin/env sh
set -eu
cd -- "$(dirname -- "$0")"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python KecheenokV3.py
else
    exec python3 KecheenokV3.py
fi
