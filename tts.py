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

Each non-empty line of the input (or paragraph / sentence, see --split) becomes
one segment. Segments are synthesized separately and joined with a short pause;
their start/end times go to --json / --srt so cuts and captions line up.

With --stream, audio goes out sentence by sentence as it is generated (to the
speakers, or to stdout for piping) and no file is written unless -o is given.
"""
import argparse, codecs, json, os, queue, re, shlex, shutil, struct, subprocess, sys, tempfile, threading
from pathlib import Path

__version__ = "0.2.0"

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

# End of a sentence: terminal punctuation, optional closing quote/bracket, then whitespace.
SENTENCE_END = r"[.!?…]+[\"'”’)\]]*"


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


def clean(s):
    return re.sub(r"\s+", " ", s).strip()


def segments_from_text(text, split):
    text = text.replace("\r\n", "\n").strip()
    if split == "paragraph":
        parts = re.split(r"\n\s*\n", text)
    elif split == "line":
        parts = text.split("\n")
    else:  # sentence
        return split_sentences(text)
    return [clean(p) for p in parts if p.strip()]


def split_sentences(text):
    parts, last = [], 0
    for m in re.finditer(rf"{SENTENCE_END}\s+", text):
        parts.append(text[last:m.end()])
        last = m.end()
    parts.append(text[last:])
    return [clean(p) for p in parts if p.strip()]


def units_from_text(text, split):
    """(sentence, ends_segment) pairs for streaming a text that is already complete."""
    for seg in segments_from_text(text, split):
        sents = split_sentences(seg)
        for j, s in enumerate(sents):
            yield s, j == len(sents) - 1


def units_from_stdin():
    """(sentence, ends_line) pairs, each yielded as soon as it is complete on stdin,
    so text produced incrementally (e.g. by an LLM) starts playing before it ends."""
    fd = sys.stdin.fileno()
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    boundary = re.compile(rf"\n|{SENTENCE_END}[ \t]+")
    buf = ""
    while True:
        data = os.read(fd, 65536)
        buf += dec.decode(data, final=not data)
        while (m := boundary.search(buf)):
            piece, buf = buf[:m.end()], buf[m.end():]
            if piece.strip():
                yield clean(piece), m.group() == "\n"
        if not data:
            break
    if buf.strip():
        yield clean(buf), True


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


def wav_stream_header(sr):
    """WAV header for a stream of unknown length (sizes set to the maximum)."""
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))


class StreamSink:
    """Sends 16-bit PCM to a player or to stdout from a background thread, so the next
    sentence is synthesized while the current one plays."""

    def __init__(self, mode, verbose=True):
        self.proc, self.broken = None, False
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
            self.q.put(wav_stream_header(SR))
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while (b := self.q.get()) is not None:
            if self.broken:
                continue
            try:
                self.out.write(b)
                self.out.flush()
            except (BrokenPipeError, OSError):
                self.broken = True  # player closed or downstream pipe ended

    def write(self, audio):
        import numpy as np
        self.q.put((np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes())

    def close(self):
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


def main():
    ap = argparse.ArgumentParser(prog="tts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="text to speak (or use -f)")
    ap.add_argument("-f", "--file", help="read text from a file ('-' for stdin)")
    ap.add_argument("-o", "--out",
                    help="output file (default: out.wav, or none with --stream); .wav is written directly, "
                         "other extensions (.mp3, .m4a, .ogg, .flac) via ffmpeg")
    ap.add_argument("--stream", choices=["play", "wav", "raw"],
                    help="send audio out sentence by sentence as it is generated, without saving a file: "
                         "'play' = to the speakers, 'wav' = WAV stream on stdout, "
                         "'raw' = raw 16-bit 24 kHz mono PCM on stdout")
    ap.add_argument("-v", "--voice", default="af_heart",
                    help="voice id, or a weighted blend like 'af_heart:70,af_bella:30' (default: af_heart)")
    ap.add_argument("-s", "--speed", type=float, default=1.0, help="speaking rate, e.g. 0.9 or 1.15 (default: 1.0)")
    ap.add_argument("-l", "--lang", help="espeak language code (default: inferred from the voice prefix)")
    ap.add_argument("--split", choices=["line", "paragraph", "sentence"], default="line",
                    help="how to cut the input into segments (default: line)")
    ap.add_argument("--pause", type=float, default=0.35, help="seconds of silence between segments (default: 0.35)")
    ap.add_argument("--sentence-pause", type=float, default=0.2,
                    help="with --stream, seconds of silence between sentences of the same segment (default: 0.2)")
    ap.add_argument("--json", help="write segment timings as JSON")
    ap.add_argument("--srt", help="write segment timings as SRT captions")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    ap.add_argument("--list-voices", action="store_true", help="print available voice ids and exit")
    ap.add_argument("--version", action="version", version=f"kokoro-voiceover {__version__}")
    a = ap.parse_args()

    if a.list_voices:
        print("\n".join(sorted(load_model().get_voices())))
        return

    # Input, as (text unit, ends a segment) pairs. Streaming from stdin stays lazy.
    lazy = a.stream and a.file == "-"
    if lazy:
        units = units_from_stdin()
    else:
        if a.file:
            try:
                text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
            except OSError as e:
                ap.error(f"can't read {a.file}: {e.strerror}")
        elif a.text:
            text = a.text
        else:
            ap.error("give TEXT or -f FILE")
        units = (list(units_from_text(text, a.split)) if a.stream
                 else [(s, True) for s in segments_from_text(text, a.split)])
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

    sink = StreamSink(a.stream, verbose=not a.quiet) if a.stream else None
    chunks, timeline, t, prev_ends = [], [], 0.0, None
    total = None if lazy else len(units)
    try:
        for i, (s, ends) in enumerate(units):
            audio, sr = k.create(s, voice=voice, speed=a.speed, lang=lang)
            audio = audio.astype(np.float32)
            if prev_ends is not None:  # silence before every unit but the first
                gap = np.zeros(int((a.pause if prev_ends else a.sentence_pause) * SR), dtype=np.float32)
                t += len(gap) / SR
                audio_out = np.concatenate([gap, audio])
                if out_path:
                    chunks.append(gap)
            else:
                audio_out = audio
            dur = len(audio) / sr
            timeline.append({"index": i, "text": s, "start": round(t, 3), "end": round(t + dur, 3)})
            t += dur
            prev_ends = ends
            if out_path:
                chunks.append(audio)
            if sink:
                sink.write(audio_out)
                if sink.broken:
                    break
            if not a.quiet:
                n = f"{i + 1}/{total}" if total else f"{i + 1}"
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
        full = np.concatenate(chunks)
        save_audio(out_path, full, SR)
    if a.json:
        Path(a.json).write_text(json.dumps({
            "audio": out_path, "sample_rate": SR, "duration": round(t, 3),
            "voice": a.voice, "lang": lang, "speed": a.speed, "pause": a.pause,
            "segments": timeline}, indent=2, ensure_ascii=False), encoding="utf-8")
    if a.srt:
        Path(a.srt).write_text("\n".join(
            f"{x['index'] + 1}\n{srt_time(x['start'])} --> {srt_time(x['end'])}\n{x['text']}\n"
            for x in timeline), encoding="utf-8")
    if not a.quiet:
        msg = f"wrote {out_path}" if out_path else f"streamed ({a.stream})"
        print(f"{msg} ({t:.1f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
