#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo 'Run setup.command first.'
    exit 1
fi
exec .venv/bin/python -m lecture_script
