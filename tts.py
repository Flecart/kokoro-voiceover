#!/usr/bin/env python3
"""kokoro-voiceover: local, offline text-to-speech for video narration (Kokoro-82M, ONNX, CPU).

Usage:
  tts "Hello world" -o out.wav
  tts -f script.txt -o narration.mp3 --voice af_heart --speed 1.0
  tts -f script.txt -o narration.mp3 --json narration.json --srt narration.srt
  tts --list-voices

Each non-empty line of the input (or paragraph / sentence, see --split) becomes
one segment. Segments are synthesized separately and joined with a short pause;
their start/end times go to --json / --srt so cuts and captions line up.
"""
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

__version__ = "0.1.0"

HERE = Path(__file__).resolve().parent
MODEL = Path(os.environ.get("KOKORO_TTS_MODEL", HERE / "models" / "kokoro-v1.0.onnx"))
VOICES = Path(os.environ.get("KOKORO_TTS_VOICES", HERE / "models" / "voices-v1.0.bin"))

# Voice-id prefix -> espeak language code
LANG_BY_PREFIX = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
                  "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}


def espeak_config():
    """espeak-ng can't use a data directory whose path is ~160+ characters: it falls back
    to a build-time default that doesn't exist and aborts. It also resolves symlinks, so a
    short link doesn't help. When the installed path is long, keep a copy (~19 MB) at a
    short location instead."""
    import shutil
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


def segments_from_text(text, split):
    text = text.replace("\r\n", "\n").strip()
    if split == "paragraph":
        parts = re.split(r"\n\s*\n", text)
    elif split == "line":
        parts = text.split("\n")
    else:  # sentence
        parts = re.split(r"(?<=[.!?])\s+", text)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


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


def main():
    ap = argparse.ArgumentParser(prog="tts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="text to speak (or use -f)")
    ap.add_argument("-f", "--file", help="read text from a file ('-' for stdin)")
    ap.add_argument("-o", "--out", default="out.wav",
                    help="output file; .wav is written directly, other extensions (.mp3, .m4a, .ogg, .flac) via ffmpeg")
    ap.add_argument("-v", "--voice", default="af_heart",
                    help="voice id, or a weighted blend like 'af_heart:70,af_bella:30' (default: af_heart)")
    ap.add_argument("-s", "--speed", type=float, default=1.0, help="speaking rate, e.g. 0.9 or 1.15 (default: 1.0)")
    ap.add_argument("-l", "--lang", help="espeak language code (default: inferred from the voice prefix)")
    ap.add_argument("--split", choices=["line", "paragraph", "sentence"], default="line",
                    help="how to cut the input into segments (default: line)")
    ap.add_argument("--pause", type=float, default=0.35, help="seconds of silence between segments (default: 0.35)")
    ap.add_argument("--json", help="write segment timings as JSON")
    ap.add_argument("--srt", help="write segment timings as SRT captions")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    ap.add_argument("--list-voices", action="store_true", help="print available voice ids and exit")
    ap.add_argument("--version", action="version", version=f"kokoro-voiceover {__version__}")
    a = ap.parse_args()

    if a.list_voices:
        print("\n".join(sorted(load_model().get_voices())))
        return

    if a.file:
        try:
            text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
        except OSError as e:
            ap.error(f"can't read {a.file}: {e.strerror}")
    elif a.text:
        text = a.text
    else:
        ap.error("give TEXT or -f FILE")
    segs = segments_from_text(text, a.split)
    if not segs:
        ap.error("input text is empty")

    import numpy as np
    import soundfile as sf

    k = load_model()
    unknown = [n.split(":")[0].strip() for n in a.voice.split(",")
               if n.split(":")[0].strip() not in set(k.get_voices())]
    if unknown:
        ap.error(f"unknown voice: {', '.join(unknown)} (see: tts --list-voices)")
    voice, first = resolve_voice(k, a.voice)
    lang = a.lang or LANG_BY_PREFIX.get(first[0], "en-us")

    sr, chunks, timeline, t = 24000, [], [], 0.0
    for i, s in enumerate(segs):
        audio, sr = k.create(s, voice=voice, speed=a.speed, lang=lang)
        audio = audio.astype(np.float32)
        dur = len(audio) / sr
        timeline.append({"index": i, "text": s, "start": round(t, 3), "end": round(t + dur, 3)})
        chunks.append(audio)
        t += dur
        if i < len(segs) - 1:
            chunks.append(np.zeros(int(a.pause * sr), dtype=np.float32))
            t += a.pause
        if not a.quiet:
            print(f"[{i + 1}/{len(segs)}] {dur:5.2f}s  {s[:70]}", file=sys.stderr)

    full = np.concatenate(chunks)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".wav":
        sf.write(out, full, sr)
    else:
        tmp = out.with_name(out.stem + ".tmp.wav")
        sf.write(tmp, full, sr)
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), str(out)], check=True)
        except FileNotFoundError:
            sys.exit("error: ffmpeg not found; install it or use a .wav output")
        finally:
            tmp.unlink(missing_ok=True)

    if a.json:
        Path(a.json).write_text(json.dumps({
            "audio": str(out), "sample_rate": sr, "duration": round(len(full) / sr, 3),
            "voice": a.voice, "lang": lang, "speed": a.speed, "pause": a.pause,
            "segments": timeline}, indent=2, ensure_ascii=False), encoding="utf-8")
    if a.srt:
        Path(a.srt).write_text("\n".join(
            f"{x['index'] + 1}\n{srt_time(x['start'])} --> {srt_time(x['end'])}\n{x['text']}\n"
            for x in timeline), encoding="utf-8")
    if not a.quiet:
        print(f"wrote {out} ({len(full) / sr:.1f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
