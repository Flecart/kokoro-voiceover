# Usage

```
tts [TEXT] [-f FILE] [-o OUT] [--stream {play,wav,raw}] [-v VOICE] [-s SPEED] [-l LANG]
    [--split {line,paragraph,sentence}] [--pause SEC] [--sentence-pause SEC]
    [--json FILE] [--srt FILE] [-q] [--list-voices] [--version]
```

| Option | Default | What it does |
|---|---|---|
| `TEXT` | | Text to speak. Use `-f` for anything longer than a line. |
| `-f, --file` | | Read the script from a file; `-f -` reads stdin. |
| `-o, --out` | `out.wav` (none with `--stream`) | Output file. `.wav` is written directly; `.mp3`, `.m4a`, `.ogg`, `.flac` go through ffmpeg. |
| `--stream` | | Send audio out sentence by sentence as it's generated, without saving a file: `play` to the speakers, `wav` or `raw` to stdout. See [Streaming](#streaming). |
| `-v, --voice` | `af_heart` | Voice id, or a weighted blend (see below). |
| `-s, --speed` | `1.0` | Speaking rate. 0.9 is calmer, 1.15 brisker. |
| `-l, --lang` | from voice | espeak language code. Normally inferred from the voice's first letter. |
| `--split` | `line` | How the script is cut into segments: one per line, per blank-line-separated paragraph, or per sentence. |
| `--pause` | `0.35` | Seconds of silence inserted between segments. |
| `--sentence-pause` | `0.2` | With `--stream`: seconds between sentences inside one segment. |
| `--json` | | Write segment timings as JSON. |
| `--srt` | | Write segment timings as SRT captions. |
| `-q, --quiet` | | No progress output. |
| `--list-voices` | | Print every voice id and exit. |

## Writing the script

One line per segment works best: a segment is one caption, one timing entry, and the natural place for a cut.

- **Keep segments between roughly one clause and three sentences.** Very short inputs (a few words) can come out flat or clipped, and inputs over about 400 tokens tend to rush. If you need a one-word line, consider merging it with its neighbour.
- **Write things the way they should be said.** "2026" reads fine, but "v2.5", "e.g.", unit symbols and unusual acronyms may not. Spell them out: "version two point five", "for example", "A P I".
- **Punctuation drives prosody.** Commas give short breaths, full stops longer ones, question marks a rising tone.
- **Blank lines are ignored** in `line` mode, so you can space the script out freely.

## Voices

Pick one with `-v`; the language follows from the first letter of its id (`a` American English, `b` British English, `i` Italian, `e` Spanish, `f` French, `p` Brazilian Portuguese, `h` Hindi, `j` Japanese, `z` Mandarin). See [voices.md](voices.md) for the full list and quality grades.

Blend voices with weights, which are normalised so they don't need to sum to 100:

```bash
tts -f script.txt -v "af_heart:70,af_bella:30" -o blend.mp3
tts -f script.txt -v "am_michael,bm_george" -o even-mix.mp3     # equal weights
```

The language of a blend comes from the first voice; override it with `-l` if needed.

## Timing output

`--json narration.json` writes:

```json
{
  "audio": "narration.mp3",
  "sample_rate": 24000,
  "duration": 12.945,
  "voice": "af_heart",
  "lang": "en-us",
  "speed": 1.0,
  "pause": 0.35,
  "segments": [
    { "index": 0, "text": "This is a test of the local voice pipeline.", "start": 0.0, "end": 2.56 },
    { "index": 1, "text": "It runs fully offline on the CPU, with no API key and no usage limits.", "start": 2.91, "end": 8.158 }
  ]
}
```

Times are in seconds from the start of the audio file. `end` is when the speech stops; the pause follows it, so the next segment's `start` is `end + pause`.

`--srt narration.srt` writes the same segments as standard captions.

## Video recipes

**Add narration to a video** (keeps the video stream untouched):

```bash
ffmpeg -i video.mp4 -i narration.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -shortest out.mp4
```

**Burn the captions in:**

```bash
ffmpeg -i video.mp4 -i narration.mp3 -vf subtitles=narration.srt -map 0:v -map 1:a -c:a aac -shortest out.mp4
```

**Soft captions** (toggleable in the player):

```bash
ffmpeg -i video.mp4 -i narration.mp3 -i narration.srt -map 0:v -map 1:a -map 2 \
  -c:v copy -c:a aac -c:s mov_text -shortest out.mp4
```

**Slideshow: one image per segment, timed to the narration.** Name the images `slides/000.png`, `slides/001.png`, … in segment order, then:

```bash
python3 - narration.json slides <<'EOF'
import json, sys
t = json.load(open(sys.argv[1])); d = sys.argv[2]; segs = t["segments"]
with open("slides.txt", "w") as f:
    for i, s in enumerate(segs):
        end = segs[i + 1]["start"] if i + 1 < len(segs) else t["duration"]
        f.write(f"file '{d}/{i:03d}.png'\nduration {end - s['start']:.3f}\n")
    f.write(f"file '{d}/{len(segs) - 1:03d}.png'\n")  # concat demuxer needs the last file repeated
EOF
ffmpeg -f concat -safe 0 -i slides.txt -i narration.mp3 -vf "fps=30,format=yuv420p" \
  -c:v libx264 -c:a aac -shortest slideshow.mp4
```

Each slide stays on screen from the start of its segment until the next segment starts.

## Streaming

`--stream` sends audio out while it is being generated, one sentence at a time, and saves nothing unless you also pass `-o`. The flag takes one value:

| Value | Where the audio goes | Format |
|---|---|---|
| `--stream play` | your speakers | played through `paplay`, `aplay` or `ffplay` |
| `--stream wav` | stdout | WAV stream, 16-bit, 24 kHz, mono |
| `--stream raw` | stdout | headerless 16-bit little-endian PCM, 24 kHz, mono |

```bash
tts --stream play "Speak this right away."
tts --stream play -f script.txt -v if_sara                     # read a whole script aloud
tts --stream wav -f script.txt | ffplay -nodisp -autoexit -    # any player that reads WAV from stdin
tts --stream raw -f script.txt | aplay -q -t raw -f S16_LE -r 24000 -c 1
tts --stream play -f script.txt -o narration.mp3               # play and save at the same time
```

**Text that is still being written.** With `-f -`, `--stream` reads stdin as it arrives and speaks each sentence as soon as it is complete, so it can voice the output of another program (an LLM, a log, a chat) while that program is still writing:

```bash
some-llm-command "Explain photosynthesis briefly." | tts --stream play -f -
```

A sentence counts as complete at a newline, or at `.`, `!`, `?` or `…` followed by a space.

**How fast it starts.** Kokoro generates a whole sentence in one pass (it is not autoregressive), so audio arrives a sentence at a time rather than word by word. Measured on a 2-core cloud VM:

| | First audio after | Then |
|---|---|---|
| `--stream` (one sentence at a time) | ~2.5 s from launch: ~1 s loading the model, ~1.3 s for the first sentence | generation runs ~2× faster than playback, so there are no gaps |
| kokoro-onnx's own `create_stream` (packs up to the 510-token limit) | ~6.6 s after the model is loaded | |
| rendering the whole file first | when everything is done | |

Shorter first sentences start sooner. Streaming splits multi-sentence lines into separate sentences, so intonation across a long line can differ slightly from a file render.

**Which player `play` uses.** `paplay` if a PulseAudio or PipeWire server is running (the default on Ubuntu desktops), otherwise `aplay`, otherwise `ffplay`; the choice is printed to stderr. To use something else, set `TTS_PLAYER` to a command that plays WAV from stdin:

```bash
TTS_PLAYER="mpv --no-video --really-quiet -" tts --stream play "Hello."
```

**From Python.** To get the audio as arrays instead (to feed your own player or a network stream), use the installed environment (`~/.local/share/kokoro-tts/venv/bin/python`):

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/.local/share/kokoro-tts"))
from tts import load_model, split_sentences

k = load_model()
for sentence in split_sentences("First sentence. Second sentence. Third one."):
    audio, sr = k.create(sentence, voice="af_heart", lang="en-us")  # float32, mono, 24 kHz
```

## Environment variables

| Variable | Used by | Effect |
|---|---|---|
| `KOKORO_TTS_HOME` | install / uninstall | Install directory (default `~/.local/share/kokoro-tts`). |
| `KOKORO_TTS_BIN` | install / uninstall | Where the `tts` command goes (default `~/.local/bin`). |
| `PYTHON` | install | Python interpreter to build the venv from (3.10–3.13). |
| `KOKORO_TTS_MODEL`, `KOKORO_TTS_VOICES` | `tts` | Use model / voice files from another location. |
| `TTS_PLAYER` | `tts --stream play` | Command that plays WAV from stdin, used instead of the auto-detected player. |
