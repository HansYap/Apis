#!/bin/sh
# Start Apis on Linux. The system Python is needed: GTK's Python bindings (gi) come from dnf, not pip.
cd "$(dirname "$(readlink -f "$0")")" && exec /usr/bin/python3 app.py "$@"
