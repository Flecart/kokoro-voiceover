#!/usr/bin/env bash
# kokoro-voiceover uninstaller — removes everything install.sh created.
#
#   ./uninstall.sh          # asks for confirmation
#   ./uninstall.sh -y       # no prompt
#   curl -fsSL https://raw.githubusercontent.com/Flecart/kokoro-voiceover/main/uninstall.sh | bash -s -- -y
#
# System packages (ffmpeg, python3-venv, curl) are left alone, since other
# software may depend on them. Honors KOKORO_TTS_HOME / KOKORO_TTS_BIN like install.sh.
set -euo pipefail

MARKER="# kokoro-voiceover"
APP="${KOKORO_TTS_HOME:-$HOME/.local/share/kokoro-tts}"
BIN="${KOKORO_TTS_BIN:-$HOME/.local/bin}"
YES=0
case "${1:-}" in -y|--yes) YES=1 ;; esac

ours() { [ -f "$1" ] && grep -qF "$MARKER" "$1"; }

targets=()
if [ -e "$APP" ]; then targets+=("$APP  (venv + ~350 MB of model files)"); fi
if ours "$BIN/tts"; then targets+=("$BIN/tts"); fi
rcs=()
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if ours "$rc"; then rcs+=("$rc"); targets+=("PATH line in $rc"); fi
done
link="${TMPDIR:-/tmp}/kokoro-espeak-data-$(id -u)"
if [ -e "$link" ] || [ -L "$link" ]; then targets+=("$link  (espeak data copy)"); fi

if [ ${#targets[@]} -eq 0 ]; then
  echo "kokoro-voiceover is not installed (nothing found in $APP or $BIN)."
  exit 0
fi

echo "This will remove:"
printf '  - %s\n' "${targets[@]}"
if [ "$YES" -ne 1 ]; then
  ans=""
  if [ -t 0 ]; then
    read -r -p "Proceed? [y/N] " ans || true
  elif { : </dev/tty; } 2>/dev/null; then   # e.g. piped from curl: ask on the terminal
    read -r -p "Proceed? [y/N] " ans </dev/tty || true
  else
    echo "No terminal to confirm on; re-run with -y to proceed."; exit 1
  fi
  case "$ans" in y|Y|yes|YES) ;; *) echo "Aborted."; exit 1 ;; esac
fi

rm -rf "$APP"
if ours "$BIN/tts"; then rm -f "$BIN/tts"; fi
for rc in "${rcs[@]}"; do
  sed -i "\|$MARKER\$|d" "$rc"
done
rm -rf "$link"

echo "kokoro-voiceover removed."
if [ -f "$BIN/tts" ]; then
  echo "note: $BIN/tts still exists but was not created by this installer, so it was kept."
fi
