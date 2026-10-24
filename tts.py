#!/usr/bin/env python3
"""kokoro-voiceover: local, offline text-to-speech for video narration (Kokoro-82M, ONNX, CPU).

Usage:
  tts "Hello world" -o out.wav
  tts -f script.txt -o narration.mp3 --voice af_heart --speed 1.0
  tts -f script.txt -o narration.mp3 --json narration.json --srt narration.srt
  tts --stream play "Speak this right away."
  tts --stream wav -f script.txt | ffplay -nodisp -autoexit -
  some-llm-command | tts --stream play -f -
  tts --list-voices

By default (--split auto) each line of the input is one segment, except that
hard-wrapped prose (lines broken mid-sentence, as in text copied from a PDF) is
rejoined and cut at sentence boundaries instead. Segments are synthesized
separately and joined with a short pause; their start/end times go to --json /
--srt so cuts and captions line up.

With --stream, audio goes out as it is generated (to the speakers, or to stdout
for piping), in chunks sized so playback doesn't run dry, and no file is written
unless -o is given.
"""
import argparse, codecs, json, os, queue, re, shlex, shutil, struct, subprocess, sys, tempfile, threading, time
from collections import deque
from pathlib import Path

__version__ = "0.3.0"

HERE = Path(__file__).resolve().parent
MODEL = Path(os.environ.get("KOKORO_TTS_MODEL", HERE / "models" / "kokoro-v1.0.onnx"))
VOICES = Path(os.environ.get("KOKORO_TTS_VOICES", HERE / "models" / "voices-v1.0.bin"))
SR = 24000  # Kokoro's output sample rate

# Voice-id prefix -> espeak language code
LANG_BY_PREFIX = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
                  "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}

# Players tried in order by --stream play: (command, expects a WAV header?)
PLAYERS = [
    (["paplay", "--raw", "--format=s16le", f"--rate={SR}", "--channels=1"], False),
    (["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", "1"], False),
    (["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", "-"], True),
]

# Chunk sizes, in characters. Kokoro sounds best on inputs of roughly 100-200 tokens:
# very short ones come out flat, very long ones rush.
PACK_LIMIT = 220      # pack whole sentences into chunks up to this long
LONG_LINE = 300       # a script line longer than this is cut into sentence chunks
LONG_SENTENCE = 300   # a sentence longer than this is cut at clause boundaries
CLAUSE_PAUSE = 0.08   # silence where a sentence was cut at a comma, colon, etc.

# End of a sentence: terminal punctuation, optional closing quote/bracket.
SENTENCE_END = r"[.!?…]+[\"'”’)\]]*"
SENTENCE_BREAK = re.compile(rf"{SENTENCE_END}(?=\s)")
LINE_DONE = re.compile(rf"(?:{SENTENCE_END}|[:;])\s*$")
CLAUSE_BREAK = re.compile(r"[,;:]\s+|\s+[—–-]{1,2}\s+|—")
LIST_ITEM = re.compile(r"^(?:[-*•]|\d+[.)])\s")


def espeak_config():
    """espeak-ng can't use a data directory whose path is ~160+ characters: it falls back
    to a build-time default that doesn't exist and aborts. It also resolves symlinks, so a
    short link doesn't help. When the installed path is long, keep a copy (~19 MB) at a
    short location instead."""
    from importlib.metadata import version
    import espeakng_loader
    from kokoro_onnx.config import EspeakConfig
    data = espeakng_loader.get_data_path()
    if len(data) <= 150:
        return EspeakConfig(data_path=data)
    uid = os.getuid() if hasattr(os, "getuid") else 0
    short = Path(tempfile.gettempdir()) / f"kokoro-espeak-data-{uid}"
    stamp = f"{data}\n{version('espeakng-loader')}"

    def up_to_date():
        try:
            return not short.is_symlink() and (short / ".source").read_text() == stamp
        except OSError:
            return False

    try:
        if not up_to_date():
            tmp = short.with_name(f"{short.name}.tmp{os.getpid()}")
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(data, tmp)
            (tmp / ".source").write_text(stamp)
            if short.is_symlink() or short.is_file():
                short.unlink()
            shutil.rmtree(short, ignore_errors=True)
            os.replace(tmp, short)
        return EspeakConfig(data_path=str(short))
    except OSError as e:
        print(f"warning: could not copy espeak data to {short} ({e}); "
              "if synthesis fails, reinstall with a shorter KOKORO_TTS_HOME", file=sys.stderr)
        return EspeakConfig(data_path=data)


def load_model():
    from kokoro_onnx import Kokoro
    for p in (MODEL, VOICES):
        if not p.exists():
            sys.exit(f"error: model file not found: {p}\nRe-run install.sh to download it.")
    return Kokoro(str(MODEL), str(VOICES), espeak_config=espeak_config())


# ---------------------------------------------------------------- text -> units
#
# A "unit" is (text, kind): the text to synthesize in one call, and what kind of
# boundary precedes it, which sets the silence inserted before it:
#   "segment"  -> --pause           (between script lines and paragraphs)
#   "sentence" -> --sentence-pause  (between sentences inside a paragraph)
#   "clause"   -> CLAUSE_PAUSE      (where a long sentence was cut at a comma)

def clean(s):
    return re.sub(r"\s+", " ", s).strip()


def continues(a, b):
    """Does line `a` run on into line `b`, i.e. was the text hard-wrapped mid-sentence?"""
    a, b = a.rstrip(), b.lstrip()
    if not a or not b or LINE_DONE.search(a) or LIST_ITEM.match(b):
        return False
    if re.search(r"[,(/&\-–—]$", a):
        return True
    letter = re.search(r"[^\W\d_]", b)
    if letter and letter.group().islower():   # next line starts mid-sentence
        return True
    return len(a) >= 40 and re.search(r"\w$", a) is not None   # long line cut at a word


def join_wrapped(a, b):
    a, b = a.rstrip(), b.lstrip()
    if re.search(r"[^\W\d_]-$", a) and b[:1].islower():   # word hyphenated across lines
        return a + b
    return f"{a} {b}"


def split_sentences(text):
    parts, last = [], 0
    for m in SENTENCE_BREAK.finditer(text):
        parts.append(text[last:m.end()])
        last = m.end()
    parts.append(text[last:])
    return [clean(p) for p in parts if p.strip()]


def clause_cuts(text):
    return [m.end() for m in CLAUSE_BREAK.finditer(text) if 20 <= m.end() <= len(text) - 15]


def split_long(sentence, limit=LONG_SENTENCE):
    """Cut an overlong sentence at the clause boundary nearest its middle, recursively."""
    if len(sentence) <= limit:
        return [sentence]
    cuts = clause_cuts(sentence)
    if not cuts:
        return [sentence]
    c = min(cuts, key=lambda x: abs(x - len(sentence) / 2))
    return split_long(clean(sentence[:c]), limit) + split_long(clean(sentence[c:]), limit)


def pack(pieces, limit=PACK_LIMIT):
    chunks = []
    for p in pieces:
        if chunks and len(chunks[-1]) + 1 + len(p) <= limit:
            chunks[-1] += " " + p
        else:
            chunks.append(p)
    return chunks


def prose_chunks(text):
    return pack([piece for s in split_sentences(text) for piece in split_long(s)])


def paragraphs(text):
    text = text.replace("\r\n", "\n")
    return [[l.strip() for l in p.split("\n") if l.strip()]
            for p in re.split(r"\n\s*\n", text) if p.strip()]


def logical_lines(lines):
    """Rejoin a paragraph's hard-wrapped lines. Returns (line, is_prose) pairs; a paragraph
    with no wrapped line is a script, and its lines are kept exactly as written."""
    if not any(continues(a, b) for a, b in zip(lines, lines[1:])):
        return [(l, False) for l in lines]
    out, last = [lines[0]], lines[0]
    for b in lines[1:]:
        if continues(last, b) or LINE_DONE.search(last):
            out[-1] = join_wrapped(out[-1], b)
        else:                       # e.g. a heading inside the paragraph
            out.append(b)
        last = b
    return [(l, True) for l in out]


def plan(text, split):
    """The whole text as a list of units (see above)."""
    if split == "line":
        return [(clean(l), "segment") for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    if split == "paragraph":
        return [(clean(" ".join(p)), "segment") for p in paragraphs(text)]
    if split == "sentence":
        return [(s, "segment") for s in split_sentences(text)]
    units = []  # auto
    for lines in paragraphs(text):
        for line, prose in logical_lines(lines):
            chunks = prose_chunks(line) if prose or len(line) > LONG_LINE else [clean(line)]
            for i, c in enumerate(chunks):
                units.append((c, "segment" if i == 0 else "sentence"))
    return units


def sentence_units(units):
    """Break units into single sentences, which the streaming chunker re-packs."""
    for text, kind in units:
        for j, s in enumerate(split_sentences(text)):
            yield s, kind if j == 0 else "sentence"


def units_from_stdin():
    """Sentences from stdin, each yielded as soon as it is complete, so text produced
    incrementally (e.g. by an LLM) starts playing before it ends. Hard-wrapped lines are
    rejoined, which needs a peek at the first letter of the next line."""
    fd = sys.stdin.fileno()
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf, eof, kind = "", False, "segment"
    while True:
        if not eof:
            data = os.read(fd, 65536)
            eof = not data
            buf += dec.decode(data, final=eof)
        while True:
            nl = buf.find("\n")
            m = SENTENCE_BREAK.search(buf)
            if m and (nl == -1 or m.end() <= nl):           # a sentence ends on this line
                piece, buf = buf[:m.end()], buf[m.end():].lstrip(" \t")
                if piece.strip():
                    yield clean(piece), kind
                    kind = "sentence"
                if buf.startswith("\n"):                    # ...and the line ends with it
                    buf, kind = buf[1:], "segment"
                continue
            if nl == -1:
                break
            line, rest = buf[:nl], buf[nl + 1:]
            if not line.strip():                            # blank line
                buf, kind = rest, "segment"
                continue
            nxt = rest.lstrip(" \t")
            if not nxt and not eof:
                break                                       # wait for the next line
            if nxt and not nxt.startswith("\n") and continues(line, nxt):
                buf = join_wrapped(line, rest)              # hard wrap: keep reading
                continue
            yield clean(line), kind                         # a line that stands alone
            buf, kind = rest, "segment"
        if eof:
            if buf.strip():
                yield clean(buf), kind
            return


class Chunker:
    """Decides what to synthesize next while streaming. Each chunk should finish
    generating before the audio already queued runs out: when there is slack, whole
    sentences are packed together (better prosody); when there isn't (e.g. right at the
    start), a long sentence is cut at a comma so the first part is ready in time."""

    def __init__(self, source, can_peek):
        self.src, self.back, self.can_peek = iter(source), deque(), can_peek
        self.rate = 0.02  # seconds of compute per character; refined as chunks are made

    def observe(self, chars, seconds):
        self.rate = 0.5 * self.rate + 0.5 * seconds / max(chars, 1)

    def _take(self):
        return self.back.popleft() if self.back else next(self.src, None)

    def _peek(self):
        if not self.back:
            nxt = next(self.src, None)
            if nxt is None:
                return None
            self.back.append(nxt)
        return self.back[0]

    def next(self, ahead):
        unit = self._take()
        if unit is None:
            return None
        text, kind = unit
        budget = max(0.8 * ahead, 1.0) / self.rate   # characters we can afford right now
        if self.can_peek:
            while (nxt := self._peek()) and nxt[1] == "sentence" \
                    and len(text) + 1 + len(nxt[0]) <= min(PACK_LIMIT, budget):
                text = f"{text} {self._take()[0]}"
        if len(text) > budget and (cuts := clause_cuts(text)):
            fit = [c for c in cuts if c <= budget]
            c = max(fit) if fit else min(cuts)
            self.back.appendleft((clean(text[c:]), "clause"))
            text = clean(text[:c])
        return text, kind


# ---------------------------------------------------------------- audio output

def wav_stream_header(sr):
    """WAV header for a stream of unknown length (sizes set to the maximum)."""
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))


def pcm16(audio):
    import numpy as np
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class StreamSink:
    """Sends 16-bit PCM to a player or to stdout from a background thread, so the next
    chunk is synthesized while the current one plays. Output starts when release() is
    called, which lets the caller hold back the first chunk until enough audio is queued.
    It tracks how much audio is queued ahead of playback, and when playing it feeds
    silence if synthesis falls behind, so a slow chunk becomes a slightly longer pause
    instead of a buffer underrun."""

    def __init__(self, mode, verbose=True):
        self.proc, self.broken = None, False
        self.written, self.queued, self.started, self.filled = 0.0, 0.0, None, 0.0
        self.fill = mode == "play"
        self.released = threading.Event()
        if mode == "play":
            custom = os.environ.get("TTS_PLAYER")
            if custom:
                cmd, wav = shlex.split(custom), True
            else:
                pulse = bool(os.environ.get("PULSE_SERVER")) or Path(
                    os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "pulse", "native").exists()
                found = [(c, w) for c, w in PLAYERS
                         if shutil.which(c[0]) and (c[0] != "paplay" or pulse)]
                if not found:
                    sys.exit("error: no audio player found (tried paplay, aplay, ffplay). "
                             "Install one, e.g. `sudo apt install pulseaudio-utils`, set TTS_PLAYER, "
                             "or pipe instead: tts --stream wav ... | <player>")
                cmd, wav = found[0]
            self.player = cmd[0]
            if verbose:
                print(f"playing via {self.player}", file=sys.stderr)
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            except OSError as e:
                sys.exit(f"error: could not start player {cmd[0]}: {e}")
            self.out = self.proc.stdin
        else:
            if sys.stdout.isatty():
                sys.exit(f"error: --stream {mode} writes binary audio to stdout; pipe it to a player, "
                         "e.g. | ffplay -nodisp -autoexit -   (or use --stream play)")
            self.out, wav = sys.stdout.buffer, mode == "wav"
        self.q = queue.Queue()
        if wav:
            self._send(wav_stream_header(SR))
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def ahead(self):
        """Seconds of audio queued but (assuming real-time playback) not yet played."""
        if self.started is None:
            return self.queued
        return max(0.0, self.written - (time.monotonic() - self.started))

    def release(self):
        self.released.set()

    def _send(self, b):
        if self.broken:
            return
        try:
            self.out.write(b)
            self.out.flush()
        except (BrokenPipeError, OSError):
            self.broken = True  # player closed or downstream pipe ended

    def _run(self):
        while True:
            timeout = None
            if self.fill and self.started is not None:
                timeout = max(self.ahead() - 0.1, 0.01)   # wake just before playback runs dry
            try:
                item = self.q.get(timeout=timeout)
            except queue.Empty:
                self._send(b"\0\0" * int(0.1 * SR))       # nothing ready: keep the player fed
                self.written += 0.1
                self.filled += 0.1
                continue
            if item is None:
                break
            gap, pcm = item
            gap = max(0.0, gap - self.filled)             # silence already played counts
            self.filled = 0.0
            if self.started is None:
                self.released.wait()
                self.started = time.monotonic()
            self._send(b"\0\0" * int(gap * SR) + pcm)
            self.written += gap + len(pcm) / 2 / SR

    def write(self, audio, gap):
        self.queued += gap + len(audio) / SR
        self.q.put((gap, pcm16(audio)))

    def close(self):
        self.release()
        self.q.put(None)
        self.thread.join()
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            if self.proc.wait() != 0:
                sys.exit(f"error: audio player {self.player} exited with code {self.proc.returncode}. "
                         "Set TTS_PLAYER to a command that plays WAV from stdin, or use --stream wav | <player>")
        elif self.broken:  # downstream closed early (e.g. `| head`): exit quietly
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())

    def abort(self):
        if self.proc:
            self.proc.terminate()


def save_audio(path, audio, sr):
    import soundfile as sf
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".wav":
        sf.write(out, audio, sr)
        return
    tmp = out.with_name(out.stem + ".tmp.wav")
    sf.write(tmp, audio, sr)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), str(out)], check=True)
    except FileNotFoundError:
        sys.exit("error: ffmpeg not found; install it or use a .wav output")
    finally:
        tmp.unlink(missing_ok=True)


def resolve_voice(k, spec):
    """'af_heart' or a weighted blend like 'af_heart:70,af_bella:30'."""
    import numpy as np
    if ":" not in spec and "," not in spec:
        return spec, spec
    parts = [p.split(":") for p in spec.split(",")]
    names = [p[0].strip() for p in parts]
    weights = np.array([float(p[1]) if len(p) > 1 else 1.0 for p in parts])
    weights = weights / weights.sum()
    return sum(w * k.get_voice_style(n) for w, n in zip(weights, names)), names[0]


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(prog="tts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="text to speak (or use -f)")
    ap.add_argument("-f", "--file", help="read text from a file ('-' for stdin)")
    ap.add_argument("-o", "--out",
                    help="output file (default: out.wav, or none with --stream); .wav is written directly, "
                         "other extensions (.mp3, .m4a, .ogg, .flac) via ffmpeg")
    ap.add_argument("--stream", choices=["play", "wav", "raw"],
                    help="send audio out as it is generated, without saving a file: "
                         "'play' = to the speakers, 'wav' = WAV stream on stdout, "
                         "'raw' = raw 16-bit 24 kHz mono PCM on stdout")
    ap.add_argument("-v", "--voice", default="af_heart",
                    help="voice id, or a weighted blend like 'af_heart:70,af_bella:30' (default: af_heart)")
    ap.add_argument("-s", "--speed", type=float, default=1.0, help="speaking rate, e.g. 0.9 or 1.15 (default: 1.0)")
    ap.add_argument("-l", "--lang", help="espeak language code (default: inferred from the voice prefix)")
    ap.add_argument("--split", choices=["auto", "line", "paragraph", "sentence"], default="auto",
                    help="how to cut the input into segments: auto = one per line, but hard-wrapped prose is "
                         "rejoined and cut at sentences; line = every line exactly as written (default: auto)")
    ap.add_argument("--pause", type=float, default=0.35,
                    help="seconds of silence between lines / paragraphs (default: 0.35)")
    ap.add_argument("--sentence-pause", type=float, default=0.2,
                    help="seconds of silence between chunks of the same paragraph (default: 0.2)")
    ap.add_argument("--json", help="write segment timings as JSON")
    ap.add_argument("--srt", help="write segment timings as SRT captions")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    ap.add_argument("--list-voices", action="store_true", help="print available voice ids and exit")
    ap.add_argument("--version", action="version", version=f"kokoro-voiceover {__version__}")
    a = ap.parse_args()

    if a.list_voices:
        print("\n".join(sorted(load_model().get_voices())))
        return

    lazy = a.stream and a.file == "-"   # stream stdin as it arrives
    if not lazy:
        if a.file:
            try:
                text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
            except OSError as e:
                ap.error(f"can't read {a.file}: {e.strerror}")
        elif a.text:
            text = a.text
        else:
            ap.error("give TEXT or -f FILE")
        units = plan(text, a.split)
        if not units:
            ap.error("input text is empty")
    out_path = a.out or (None if a.stream else "out.wav")

    import numpy as np

    k = load_model()
    unknown = [n.split(":")[0].strip() for n in a.voice.split(",")
               if n.split(":")[0].strip() not in set(k.get_voices())]
    if unknown:
        ap.error(f"unknown voice: {', '.join(unknown)} (see: tts --list-voices)")
    voice, first = resolve_voice(k, a.voice)
    lang = a.lang or LANG_BY_PREFIX.get(first[0], "en-us")
    pauses = {"segment": a.pause, "sentence": a.sentence_pause, "clause": CLAUSE_PAUSE}

    sink = StreamSink(a.stream, verbose=not a.quiet) if a.stream else None
    if sink:
        chunker = Chunker(units_from_stdin() if lazy else sentence_units(units), can_peek=not lazy)
        next_unit = lambda: chunker.next(sink.ahead())
    else:
        it = iter(units)
        next_unit = lambda: next(it, None)

    chunks, timeline, t = [], [], 0.0
    try:
        while (unit := next_unit()) is not None:
            s, kind = unit
            # Start playing once the queued audio, plus the pause before this chunk (which
            # absorbs a short stall) and a quarter second, covers the time to generate it.
            if sink and not sink.released.is_set() and timeline and \
                    sink.queued + pauses[kind] + 0.25 >= len(s) * chunker.rate:
                sink.release()
            t0 = time.monotonic()
            audio, sr = k.create(s, voice=voice, speed=a.speed, lang=lang)
            if sink:
                chunker.observe(len(s), time.monotonic() - t0)
            audio = audio.astype(np.float32)
            gap = pauses[kind] if timeline else 0.0
            t += gap
            dur = len(audio) / sr
            timeline.append({"index": len(timeline), "text": s, "start": round(t, 3), "end": round(t + dur, 3)})
            t += dur
            if out_path:
                chunks += [np.zeros(int(gap * SR), dtype=np.float32), audio]
            if sink:
                sink.write(audio, gap)
                if lazy:
                    sink.release()  # more text may be slow to arrive: start right away
                if sink.broken:
                    break
            if not a.quiet:
                n = f"{len(timeline)}" if sink else f"{len(timeline)}/{len(units)}"
                print(f"[{n}] {dur:5.2f}s  {s[:70]}", file=sys.stderr)
    except KeyboardInterrupt:
        if sink:
            sink.abort()
        sys.exit(130)
    if sink:
        sink.close()
    if not timeline:
        ap.error("input text is empty")

    if out_path:
        save_audio(out_path, np.concatenate(chunks), SR)
    if a.json:
        Path(a.json).write_text(json.dumps({
            "audio": out_path, "sample_rate": SR, "duration": round(t, 3),
            "voice": a.voice, "lang": lang, "speed": a.speed, "pause": a.pause,
            "sentence_pause": a.sentence_pause, "segments": timeline},
            indent=2, ensure_ascii=False), encoding="utf-8")
    if a.srt:
        Path(a.srt).write_text("\n".join(
            f"{x['index'] + 1}\n{srt_time(x['start'])} --> {srt_time(x['end'])}\n{x['text']}\n"
            for x in timeline), encoding="utf-8")
    if not a.quiet:
        msg = f"wrote {out_path}" if out_path else f"streamed ({a.stream})"
        print(f"{msg} ({t:.1f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
