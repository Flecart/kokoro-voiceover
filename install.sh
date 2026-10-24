#!/usr/bin/env bash
# kokoro-voiceover installer — sets up a local, offline `tts` command (Kokoro-82M, ONNX, CPU).
#
#   ./install.sh                     # from a clone of the repo
#   curl -fsSL https://raw.githubusercontent.com/Flecart/kokoro-voiceover/main/install.sh | bash
#
# Re-running it upgrades in place. Environment overrides:
#   KOKORO_TTS_HOME  install dir      (default: ~/.local/share/kokoro-tts)
#   KOKORO_TTS_BIN   command dir      (default: ~/.local/bin)
#   PYTHON           python to use    (default: python3; needs 3.10–3.13)
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/Flecart/kokoro-voiceover/main"
MODEL_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_SHA="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
VOICES_SHA="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
MARKER="# kokoro-voiceover"

APP="${KOKORO_TTS_HOME:-$HOME/.local/share/kokoro-tts}"
BIN="${KOKORO_TTS_BIN:-$HOME/.local/bin}"
PY="${PYTHON:-python3}"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. system dependencies -------------------------------------------------
missing=()
command -v "$PY" >/dev/null || missing+=(python3)
"$PY" -c 'import ensurepip, venv' 2>/dev/null || missing+=(python3-venv)
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
command -v curl   >/dev/null || missing+=(curl)
if [ ${#missing[@]} -gt 0 ]; then
  if command -v apt-get >/dev/null; then
    say "installing system packages: ${missing[*]}"
    SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq "${missing[@]}" >/dev/null
  else
    die "missing: ${missing[*]} — install them with your package manager and re-run"
  fi
fi

pyver=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
"$PY" -c 'import sys; sys.exit(not ((3,10) <= sys.version_info[:2] <= (3,13)))' \
  || die "Python $pyver found, but kokoro-onnx needs 3.10–3.13. Re-run with PYTHON=/path/to/python3.12 ./install.sh"

# --- 2. python environment ----------------------------------------------------
say "python $pyver environment in $APP"
mkdir -p "$APP/models" "$BIN"
[ -x "$APP/venv/bin/python" ] || "$PY" -m venv "$APP/venv"
"$APP/venv/bin/python" -m pip install -q --upgrade pip
"$APP/venv/bin/python" -m pip install -q --upgrade "kokoro-onnx>=0.4,<1" soundfile numpy

# --- 3. model files (~350 MB, verified by sha256) -----------------------------
fetch_model() {  # name sha
  local f="$APP/models/$1"
  if [ -s "$f" ] && echo "$2  $f" | sha256sum -c --status; then return; fi
  say "downloading $1"
  curl -fL --retry 3 --progress-bar -o "$f.part" "$MODEL_BASE/$1"
  echo "$2  $f.part" | sha256sum -c --status || { rm -f "$f.part"; die "checksum mismatch for $1"; }
  mv "$f.part" "$f"
}
fetch_model kokoro-v1.0.onnx "$MODEL_SHA"
fetch_model voices-v1.0.bin  "$VOICES_SHA"

# --- 4. the tts script ---------------------------------------------------------
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/tts.py" ] && grep -q "kokoro-voiceover" "$SRC_DIR/tts.py"; then
  say "installing tts.py from $SRC_DIR"
  cp "$SRC_DIR/tts.py" "$APP/tts.py"
else
  say "downloading tts.py"
  curl -fsSL "$REPO_RAW/tts.py" -o "$APP/tts.py"
fi

cat > "$BIN/tts" <<EOF
#!/usr/bin/env bash
$MARKER — remove with uninstall.sh
exec "$APP/venv/bin/python" "$APP/tts.py" "\$@"
EOF
chmod +x "$BIN/tts"
say "command installed: $BIN/tts"

# --- 5. PATH --------------------------------------------------------------------
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
      [ -f "$rc" ] || [ "$rc" = "$HOME/.bashrc" ] || continue
      grep -qF "$MARKER" "$rc" 2>/dev/null || echo "export PATH=\"$BIN:\$PATH\" $MARKER" >> "$rc"
    done
    warn "$BIN was not on your PATH; added it to your shell rc. Open a new terminal or run: source ~/.bashrc"
    ;;
esac

# --- 6. smoke test ----------------------------------------------------------------
say "smoke test"
tmpwav="$(mktemp --suffix=.wav)"
"$BIN/tts" -q "Kokoro voiceover is installed." -o "$tmpwav"
[ -s "$tmpwav" ] || die "smoke test produced no audio"
rm -f "$tmpwav"

say "done. Try:  tts \"Hello there.\" -o hello.mp3     |  tts --help  |  tts --list-voices"
