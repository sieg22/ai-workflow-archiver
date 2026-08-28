#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo
echo "============================================================"
echo " AI WORKFLOW ARCHIVER v1.5.0 - macOS"
echo "============================================================"
echo
echo "Project backup folders will be created directly in:"
echo "$SCRIPT_DIR"
echo

PYTHON_BIN=""

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3 was not found."
    echo "Install Python 3.10+ and try again."
    echo
    read -r -n 1 -s -p "Press any key to close..."
    echo
    exit 1
fi

"$PYTHON_BIN" "$SCRIPT_DIR/archive_weavy.py" --interactive
EXITCODE=$?

if [ "$EXITCODE" -ne 0 ]; then
    echo
    echo "============================================================"
    echo " ARCHIVER STOPPED WITH AN ERROR"
    echo "============================================================"
    echo "Exit code: $EXITCODE"
    echo
    read -r -n 1 -s -p "Press any key to close..."
    echo
fi

exit "$EXITCODE"
