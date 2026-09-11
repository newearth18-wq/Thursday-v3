#!/usr/bin/env bash
# Builds the sidecar Tauri embeds so a packaged install starts its own backend (Sprint 83).
#
# PyInstaller does not cross-compile: this has to run on each OS whose installer needs one,
# which is why it is a script CI calls per-platform rather than a single "build everything"
# step. Cargo's build.rs copies whatever `bundle.externalBin` names on *every* `cargo
# build` once that key is set in tauri.conf.json — so a missing binary here fails
# compilation, not just packaging. That is deliberate: a broken sidecar build is caught by
# `cargo check`, not discovered the first time someone runs the installer.
set -euo pipefail
cd "$(dirname "$0")/.."

TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
if [[ -z "$TRIPLE" ]]; then
    echo "could not determine the Rust host triple (is rustc on PATH?)" >&2
    exit 1
fi

OUT="apps/desktop/src-tauri/binaries"
EXT=""
[[ "$TRIPLE" == *windows* ]] && EXT=".exe"
TARGET="$OUT/thursday-backend-${TRIPLE}${EXT}"

mkdir -p "$OUT"

pyinstaller --noconfirm --distpath "$OUT" --workpath installer/build \
    installer/thursday-backend.spec

# PyInstaller names its output from the spec regardless of platform; Tauri's sidecar
# resolution needs the target-triple suffix (see tauri-plugin-shell's `relative_command_path`).
BUILT="$OUT/thursday-backend${EXT}"
if [[ "$BUILT" != "$TARGET" ]]; then
    mv "$BUILT" "$TARGET"
fi

echo "sidecar built: $TARGET"
