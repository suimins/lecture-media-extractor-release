#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
if [ ! -x .venv/bin/python ]; then
    if command -v python3.12 >/dev/null 2>&1; then
        lecture_python=python3.12
    else
        lecture_python=python3
    fi
    "$lecture_python" -c 'import sys; assert sys.version_info >= (3, 11), "Install Python 3.12 or newer."'
    "$lecture_python" -m venv .venv
fi
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt --disable-pip-version-check
if ! .venv/bin/python -m lecture_script.runtime; then
    echo 'GPU setup did not complete. CPU mode remains available.'
    echo 'On macOS, install Apple Command Line Tools (xcode-select --install) and rerun setup for Metal.'
fi
.venv/bin/python install_chrome.py
echo 'Ready. Open install-guide.html to load the Chrome extension.'
