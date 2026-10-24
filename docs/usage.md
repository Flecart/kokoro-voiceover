# Usage

```
tts [TEXT] [-f FILE] [-o OUT] [-v VOICE] [-s SPEED] [-l LANG]
    [--split {line,paragraph,sentence}] [--pause SEC]
    [--json FILE] [--srt FILE] [-q] [--list-voices] [--version]
```

| Option | Default | What it does |
|---|---|---|
| `TEXT` | | Text to speak. Use `-f` for anything longer than a line. |
| `-f, --file` | | Read the script from a file; `-f -` reads stdin. |
| `-o, --out` | `out.wav` | Output file. `.wav` is written directly; `.mp3`, `.m4a`, `.ogg`, `.flac` go through ffmpeg. |
| `-v, --voice` | `af_heart` | Voice id, or a weighted blend (see below). |
| `-s, --speed` | `1.0` | Speaking rate. 0.9 is calmer, 1.15 brisker. |
| `-l, --lang` | from voice | espeak language code. Normally inferred from the voice's first letter. |
| `--split` | `line` | How the script is cut into segments: one per line, per blank-line-separated paragraph, or per sentence. |
| `--pause` | `0.35` | Seconds of silence inserted between segments. |
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

The `tts` command renders the whole file, which is what a video pipeline wants. For live use (an assistant that should start talking quickly), synthesize sentence by sentence and play each chunk while the next is generated. Kokoro is not autoregressive, so a chunk is the smallest unit: audio arrives a sentence at a time, not word by word.

Measured on a 2-core cloud VM:

| Approach | First audio after | Keeps up with playback |
|---|---|---|
| kokoro-onnx's `create_stream` (packs up to the 510-token limit) | 6.6 s | yes |
| one `create` call per sentence | 1.3 s | yes (~2.4× real time) |

A minimal sentence-level loop, run with the installed environment (`~/.local/share/kokoro-tts/venv/bin/python`):

```python
import os, re, sys
sys.path.insert(0, os.path.expanduser("~/.local/share/kokoro-tts"))
from tts import load_model

k = load_model()
text = "First sentence. Second sentence. Third one."
for sentence in re.split(r"(?<=[.!?])\s+", text):
    audio, sr = k.create(sentence, voice="af_heart", lang="en-us")
    # hand `audio` (float32, mono, 24 kHz) to your player, e.g. sounddevice.play(audio, sr)
```

When the text itself comes from an LLM token by token, buffer tokens until a sentence ends, then synthesize that sentence.

## Environment variables

| Variable | Used by | Effect |
|---|---|---|
| `KOKORO_TTS_HOME` | install / uninstall | Install directory (default `~/.local/share/kokoro-tts`). |
| `KOKORO_TTS_BIN` | install / uninstall | Where the `tts` command goes (default `~/.local/bin`). |
| `PYTHON` | install | Python interpreter to build the venv from (3.10–3.13). |
| `KOKORO_TTS_MODEL`, `KOKORO_TTS_VOICES` | `tts` | Use model / voice files from another location. |
