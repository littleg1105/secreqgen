#!/bin/bash
# Fix macOS hidden flag on .pth files
# 
# macOS Sequoia (and possibly earlier) may set the hidden flag on files
# created by pip due to APFS compression. This prevents Python from
# processing .pth files, breaking editable installs.
#
# Run this after `pip install -e .` if imports fail.

VENV_PATH="${1:-.venv}"
SITE_PACKAGES="$VENV_PATH/lib/python*/site-packages"

if ! ls $SITE_PACKAGES/*.pth &> /dev/null; then
    echo "No .pth files found in $SITE_PACKAGES"
    exit 1
fi

echo "Removing hidden flag from .pth files..."
chflags nohidden $SITE_PACKAGES/*.pth 2>/dev/null

echo "Done. .pth files:"
ls -lO $SITE_PACKAGES/*.pth 2>/dev/null | grep -v "^total"
