#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
ENTRYPOINT_NAME="hpc-model-utils"

FORCE=0
QUIET=0

usage() {
	cat <<EOF
Usage: ./setup.sh [options]

Options:
	-f, --force     Recreate virtual environment even if it exists.
	-q, --quiet     Reduced output.
	-h, --help      Show this help.
EOF
}

log() { [ "$QUIET" -eq 1 ] || echo -e "$*"; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		-f|--force) FORCE=1; shift ;;
		-q|--quiet) QUIET=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) echo "Unknown option: $1" >&2; usage; exit 1 ;;
	esac
done

# Check for uv
if ! command -v uv >/dev/null 2>&1; then
	echo "Error: uv is not installed. Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
	exit 1
fi

if [[ -d "$VENV_DIR" && $FORCE -eq 1 ]]; then
	log "Removing existing virtual environment (force requested)..."
	rm -rf "$VENV_DIR"
fi

log "Installing project with uv..."
uv sync

# Determine a suitable directory on PATH for symlink
choose_link_dir() {
	if [[ -d "$HOME/.local/bin" || ! -e "$HOME/.local/bin" ]]; then
		mkdir -p "$HOME/.local/bin"
		echo "$HOME/.local/bin"
		return 0
	fi
	IFS=":" read -r -a path_parts <<< "$PATH"
	for candidate in "${path_parts[@]}"; do
		if [[ -n "$candidate" && -d "$candidate" && -w "$candidate" ]]; then
			echo "$candidate"
			return 0
		fi
	done
	return 1
}

LINK_DIR="$(choose_link_dir || true)"

if [[ -z "${LINK_DIR:-}" ]]; then
	echo "Warning: No writable directory on PATH found for symlink. Skipping symlink creation." >&2
	echo "You can manually link: ln -s '$VENV_DIR/bin/$ENTRYPOINT_NAME' /some/dir/on/PATH/$ENTRYPOINT_NAME" >&2
else
	ln -sf "$VENV_DIR/bin/$ENTRYPOINT_NAME" "$LINK_DIR/$ENTRYPOINT_NAME"
	log "Created symlink: $LINK_DIR/$ENTRYPOINT_NAME -> $VENV_DIR/bin/$ENTRYPOINT_NAME"
	case ":$PATH:" in
		*":$LINK_DIR:"*) : ;;
		*) echo "Note: $LINK_DIR is not currently on PATH. Add the following line to your shell profile:"; echo "  export PATH=\"$LINK_DIR:\$PATH\"" ;;
	esac
fi

log "Setup complete. You can now run: $ENTRYPOINT_NAME --version"
