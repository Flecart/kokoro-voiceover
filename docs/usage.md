# Usage

```
tts [TEXT] [-f FILE] [-o OUT] [--stream {play,wav,raw}] [-v VOICE] [-s SPEED] [-l LANG]
    [--split {auto,line,paragraph,sentence}] [--pause SEC] [--sentence-pause SEC]
    [--json FILE] [--srt FILE] [-q] [--list-voices] [--version]
```

| Option | Default | What it does |
|---|---|---|
| `TEXT` | | Text to speak. Use `-f` for anything longer than a line. |
| `-f, --file` | | Read the script from a file; `-f -` reads stdin. |
| `-o, --out` | `out.wav` (none with `--stream`) | Output file. `.wav` is written directly; `.mp3`, `.m4a`, `.ogg`, `.flac` go through ffmpeg. |
| `--stream` | | Send audio out as it's generated, without saving a file: `play` to the speakers, `wav` or `raw` to stdout. See [Streaming](#streaming). |
| `-v, --voice` | `af_heart` | Voice id, or a weighted blend (see below). |
| `-s, --speed` | `1.0` | Speaking rate. 0.9 is calmer, 1.15 brisker. |
| `-l, --lang` | from voice | espeak language code. Normally inferred from the voice's first letter. |
| `--split` | `auto` | How the text is cut into segments. `auto`: one per line, but hard-wrapped prose is rejoined and cut at sentences (see below). `line`: every line exactly as written. `paragraph`: one per blank-line-separated paragraph. `sentence`: one per sentence. |
| `--pause` | `0.35` | Seconds of silence between lines and paragraphs. |
| `--sentence-pause` | `0.2` | Seconds of silence between chunks of the same paragraph. |
| `--json` | | Write segment timings as JSON. |
| `--srt` | | Write segment timings as SRT captions. |
| `-q, --quiet` | | No progress output. |
| `--list-voices` | | Print every voice id and exit. |

## Writing the script

One line per segment works best: a segment is one caption, one timing entry, and the natural place for a cut.

**Pasted prose is fine too.** Text copied from a PDF, a LaTeX source or an email is often hard-wrapped: broken into fixed-width lines mid-sentence. In the default `auto` mode, `tts` spots this and rejoins the lines, then cuts the paragraph at sentence boundaries into chunks of one or two sentences (up to ~220 characters, the size Kokoro reads most naturally). A line counts as wrapped when it doesn't end in `.`, `!`, `?`, `:` or `;` and either the next line starts with a lowercase letter or the line itself is long (40+ characters). Paragraphs without wrapped lines are left exactly as written, so a one-sentence-per-line script behaves as you'd expect; headings and bulleted items stay on their own. Use `--split line` to turn this off.

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
  "sentence_pause": 0.2,
  "segments": [
    { "index": 0, "text": "This is a test of the local voice pipeline.", "start": 0.0, "end": 2.56 },
    { "index": 1, "text": "It runs fully offline on the CPU, with no API key and no usage limits.", "start": 2.91, "end": 8.158 }
  ]
}
```

Times are in seconds from the start of the audio file. `end` is when the speech stops; a pause follows it (`pause` between lines and paragraphs, `sentence_pause` between chunks of one paragraph) before the next segment's `start`.

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

`--stream` sends audio out while it is being generated, a chunk at a time, and saves nothing unless you also pass `-o`. The flag takes one value:

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

A sentence counts as complete at `.`, `!`, `?` or `…` followed by a space, or at the end of a line that isn't hard-wrapped (to tell, `tts` waits for the first letter of the next line).

**How the chunks are chosen.** Kokoro generates a whole chunk in one pass (it is not autoregressive), so audio arrives a chunk at a time rather than word by word, and each chunk has to finish generating before the audio already queued runs out. `tts` measures how fast your machine generates and sizes every chunk to fit:

- When there is slack, it packs whole sentences together (up to ~220 characters), which sounds closer to a file render.
- When there isn't, typically right at the start, it cuts a long sentence at a comma, colon or dash, so the first part is ready in time. It never cuts mid-phrase.
- Playback starts with the first chunk unless the next one clearly won't be ready in time, in which case it waits for it. On a typical CPU this costs nothing; on a very slow one it trades a later start for no stall.
- If generation still falls behind, `play` feeds silence to the player, so a late chunk becomes a slightly longer pause at a sentence or clause boundary rather than a buffer underrun.

**How fast it starts.** Measured with a 63-second hard-wrapped text and a player that consumes audio in real time:

| CPU | First audio after launch | Gaps during playback |
|---|---|---|
| 2 cores | ~2 s (≈1 s of it loading the model) | none (≤0.3 s of extra pause in total) |
| 1 core | ~2 s | none (≤0.3 s of extra pause in total) |
| 1 core shared with a busy process (≈ half speed) | ~6.5 s (holds until it can keep up) | none |

For comparison, kokoro-onnx's own `create_stream` packs text up to the model's 510-token limit and took ~6.6 s after loading to produce its first audio on 2 cores.

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
