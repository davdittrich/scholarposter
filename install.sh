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

# Copy docs (the install banner references them)
if command -v rsync &>/dev/null; then
    rsync -a --delete "$SOURCE_DIR/docs/" "$DEST_DIR/docs/"
else
    rm -rf "$DEST_DIR/docs"
    cp -r "$SOURCE_DIR/docs" "$DEST_DIR/docs"
fi

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

# ── Make command available on PATH ────────────────────────────────────────────
SYMLINK_DIR="$HOME/.local/bin"
mkdir -p "$SYMLINK_DIR"
ln -sf "$VENV/bin/scholarposter" "$SYMLINK_DIR/scholarposter"
echo "  Symlinked scholarposter → $SYMLINK_DIR/scholarposter"

if [[ ":$PATH:" != *":$SYMLINK_DIR:"* ]]; then
    # Detect shell rc file and appropriate syntax
    SHELL_RC=""
    EXPORT_LINE=""
    case "$(basename "${SHELL:-bash}")" in
        zsh)
            SHELL_RC="$HOME/.zshrc"
            EXPORT_LINE='export PATH="$HOME/.local/bin:$PATH"'
            ;;
        bash)
            SHELL_RC="$HOME/.bashrc"
            EXPORT_LINE='export PATH="$HOME/.local/bin:$PATH"'
            ;;
        fish)
            SHELL_RC="$HOME/.config/fish/config.fish"
            EXPORT_LINE='set -gx PATH $HOME/.local/bin $PATH'
            ;;
        *)
            echo "  ⚠  Unknown shell '${SHELL:-}'. Add ~/.local/bin to your PATH manually:"
            echo "       export PATH=\"\$HOME/.local/bin:\$PATH\""
            ;;
    esac

    if [[ -n "$SHELL_RC" ]]; then
        if [[ ! -f "$SHELL_RC" ]]; then
            # Create parent dir + rc file if missing (e.g., fresh fish: ~/.config/fish/ may not exist)
            mkdir -p "$(dirname "$SHELL_RC")"
            touch "$SHELL_RC"
        fi
        if ! grep -qF '.local/bin' "$SHELL_RC"; then
            echo "" >> "$SHELL_RC"
            echo "# Added by scholarposter installer" >> "$SHELL_RC"
            echo "$EXPORT_LINE" >> "$SHELL_RC"
            echo "  Added ~/.local/bin to PATH in $SHELL_RC"
        fi
    fi

    export PATH="$SYMLINK_DIR:$PATH"
    echo "  Open a new terminal for PATH changes to take effect."
fi

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
   Command: scholarposter (symlinked to $SYMLINK_DIR/scholarposter)

Next steps:
  1. Set up Mastodon:
       scholarposter auth mastodon --config $DEST_DIR/config.toml

  2. Set up Bluesky (in $DEST_DIR/.env):
       BLUESKY_EMAIL=your@email.com
       BLUESKY_PASSWORD=xxxx-xxxx-xxxx-xxxx
       (Create app password at bsky.app → Settings → App Passwords)

  3. Set up LinkedIn:
       a. Edit $DEST_DIR/.env — fill in LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET
          (see docs/auth-linkedin.md for LinkedIn Developer app setup)
       b. scholarposter auth linkedin --config $DEST_DIR/config.toml

  4. (Optional) Set up summarization — see docs/summarization.md

  5. Test:
       scholarposter run --config $DEST_DIR/config.toml --dry-run

  6. Add to cron (crontab -e):
       */30 * * * * $SYMLINK_DIR/scholarposter run --config $DEST_DIR/config.toml >> $DEST_DIR/scholarposter.log 2>&1

To upgrade: re-run this script with the same DEST_DIR.
EOF
