# Troubleshooting

**`tts: command not found` right after installing**
`~/.local/bin` wasn't on your PATH yet. The installer added it to `~/.bashrc`; open a new terminal or run `source ~/.bashrc`. You can always call it directly: `~/.local/bin/tts`.

**`Python 3.x found, but kokoro-onnx needs 3.10–3.13`**
Point the installer at a supported interpreter, e.g. `PYTHON=python3.12 ./install.sh`. On a system without one, [`uv python install 3.12`](https://docs.astral.sh/uv/) gives you one without touching the system Python.

**`Error processing file '/home/runner/work/espeakng-loader/.../phontab': No such file or directory`**
espeak-ng (used for pronunciation) can't read its data directory when the path is about 160 characters or longer; it falls back to a build-time default that doesn't exist on your machine. With a normal home folder the installed path is around 100 characters, so this only happens with a deep `$HOME` or `KOKORO_TTS_HOME`. When the path is long, `tts` copies the data (19 MB) to `/tmp/kokoro-espeak-data-<uid>` and uses that; a symlink isn't enough, because espeak resolves it. If you still see the error, the temp directory isn't writable or `TMPDIR` is itself very long: reinstall to a shorter path with `KOKORO_TTS_HOME=~/kokoro-tts ./install.sh`.

**`checksum mismatch` during install**
The model download was interrupted or altered. Re-run the installer; it retries and only keeps files whose sha256 matches.

**`--stream play`: `no audio player found` or `audio player … exited with code 1`**
`play` needs `paplay` (package `pulseaudio-utils`), `aplay` (`alsa-utils`) or `ffplay` (`ffmpeg`), and a working sound device. Ubuntu desktops have `paplay` already. Check the device with `speaker-test -t sine -l 1`. To use another player, set `TTS_PLAYER` to a command that reads WAV from stdin, or pipe: `tts --stream wav … | your-player`.

**`--stream wav writes binary audio to stdout`**
`wav` and `raw` print audio data, so `tts` refuses to dump it into a terminal. Pipe it into a player or another program, or use `--stream play`.

**`ffmpeg not found`**
Only needed for non-WAV output. Install it (`sudo apt install ffmpeg`) or write `.wav`.

**A word is mispronounced**
Write it the way it sounds: "v2.5" comes out better as "version two point five", and acronyms you want spelled letter by letter work best with spaces between the letters ("A P I").

**The voice sounds rushed or flat**
Very long segments (over ~400 tokens, a long paragraph) tend to rush; split them into more lines. Very short ones (a couple of words) can sound flat; merge them with a neighbouring line.

**Slow generation**
Kokoro runs on CPU at roughly 2× real time on two cores, more with extra cores. Close other heavy processes, or generate overnight in batch; the first call of a run includes about a second of model loading.

**Upgrade / reinstall**
Re-run `./install.sh` (or the curl one-liner). Model files that already pass the checksum are not downloaded again.
