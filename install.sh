#!/usr/bin/env bash
# scholarposter installer
# Usage: ./install.sh [DEST_DIR]   (default: $HOME/scholarposter)
set -euo pipefail

trap 'echo "Installation failed at line $LINENO" >&2' ERR

# ── Resolve paths ─────────────────────────────────────────────────────────────
DEST_DIR="${1:-$HOME/scholarposter}"
DEST_DIR="$(realpath -m "$DEST_DIR")"           # absolute, may not exist yet
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"     # directory containing this script

echo "Installing scholarposter to: $DEST_DIR"

# ── Python version check ───────────────────────────────────────────────────────
PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
    echo "Error: python3 not found. Install Python 3.11 or later." >&2
    exit 1
fi

PY_VER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="${PY_VER#*.}"

if (( PY_MAJOR < 3 || ( PY_MAJOR == 3 && PY_MINOR < 11 ) )); then
    echo "Error: Python 3.11+ required, found $PY_VER" >&2
    exit 1
fi
echo "  Python $PY_VER — OK"

# ── Create destination directory ───────────────────────────────────────────────
mkdir -p "$DEST_DIR"

# ── Copy source files ──────────────────────────────────────────────────────────
echo "  Copying source files..."
if command -v rsync &>/dev/null; then
    rsync -a --delete "$SOURCE_DIR/scholarposter/" "$DEST_DIR/scholarposter/"
else
    rm -rf "$DEST_DIR/scholarposter"
    cp -r "$SOURCE_DIR/scholarposter" "$DEST_DIR/scholarposter"
fi

cp "$SOURCE_DIR/pyproject.toml"      "$DEST_DIR/pyproject.toml"
cp "$SOURCE_DIR/config.toml.example" "$DEST_DIR/config.toml.example"
cp "$SOURCE_DIR/.env.example"        "$DEST_DIR/.env.example"

# ── Create / reuse virtualenv ──────────────────────────────────────────────────
VENV="$DEST_DIR/.venv"
if [[ ! -d "$VENV" ]]; then
    echo "  Creating virtualenv..."
    "$PYTHON" -m venv --upgrade-deps "$VENV"
else
    echo "  Reusing existing virtualenv..."
fi

# ── Install package ────────────────────────────────────────────────────────────
echo "  Installing dependencies (this may take a minute)..."
"$VENV/bin/pip" install --upgrade --quiet -e "$DEST_DIR"

# ── Scaffold config files (never overwrite) ────────────────────────────────────
if [[ ! -f "$DEST_DIR/config.toml" ]]; then
    cp "$DEST_DIR/config.toml.example" "$DEST_DIR/config.toml"
    echo "  Created config.toml from example."
else
    echo "  config.toml already exists — not overwritten."
fi

if [[ ! -f "$DEST_DIR/.env" ]]; then
    cp "$DEST_DIR/.env.example" "$DEST_DIR/.env"
    echo "  Created .env from example."
else
    echo "  .env already exists — not overwritten."
fi

# ── Secure permissions ─────────────────────────────────────────────────────────
chmod 600 "$DEST_DIR/config.toml" "$DEST_DIR/.env"
# Secure any credential files if present
find "$DEST_DIR" -maxdepth 1 -name "*.secret" -exec chmod 600 {} \;

# ── Next-steps banner ──────────────────────────────────────────────────────────
cat <<EOF

✔  Installed to $DEST_DIR

Next steps:
  1. Authenticate with Mastodon:
       See docs/auth-mastodon.md
       Place pytooter_usercred.secret in $DEST_DIR

  2. Edit $DEST_DIR/config.toml
       Set [mastodon] instance and credentials_file path

  3. Fill in $DEST_DIR/.env
       Bluesky: BLUESKY_EMAIL and BLUESKY_PASSWORD (App Password)
       LinkedIn: see docs/auth-linkedin.md

  4. Test:
       $VENV/bin/scholarposter run --config $DEST_DIR/config.toml --dry-run

  5. Add to cron (crontab -e):
       */30 * * * * $VENV/bin/scholarposter run --config $DEST_DIR/config.toml >> $DEST_DIR/scholarposter.log 2>&1

To upgrade: re-run this script with the same DEST_DIR.
EOF
