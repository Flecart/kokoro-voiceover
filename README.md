# kokoro-voiceover

A local, offline `tts` command for narrating videos. It wraps [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), a small open-weight text-to-speech model (Apache-2.0), running on CPU through [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx).

- **Free and offline**: no API key, no quota, no account. After install, nothing leaves your machine.
- **Fast on CPU**: about 2× faster than real time on two cores; no GPU needed.
- **Built for video**: alongside the audio it writes per-segment timings (JSON) and captions (SRT), so cuts, slides and subtitles line up with the narration.
- **~50 voices**: American and British English, Italian, Spanish, French, Portuguese, Hindi, Japanese, Mandarin. Voices can be blended.

## Install

Linux (tested on Ubuntu 22.04+ / Debian). Needs Python 3.10–3.13; the installer adds `ffmpeg` and `python3-venv` with `apt` if they're missing.

```bash
git clone https://github.com/Flecart/kokoro-voiceover.git
cd kokoro-voiceover
./install.sh
```

or in one line:

```bash
curl -fsSL https://raw.githubusercontent.com/Flecart/kokoro-voiceover/main/install.sh | bash
```

This puts a virtualenv and the model (~350 MB, checksum-verified) in `~/.local/share/kokoro-tts` and the `tts` command in `~/.local/bin`, then runs a smoke test. Re-run it any time to upgrade.

## Use

```bash
tts "Hello there." -o hello.mp3
tts -f script.txt -o narration.mp3 --json narration.json --srt narration.srt
tts -f script.txt -v if_sara -o narrazione.mp3        # Italian
tts -f script.txt -v "af_heart:70,af_bella:30" -s 1.1 -o blend.wav
tts --list-voices
```

Or skip the file and stream the audio as it's generated, sentence by sentence:

```bash
tts --stream play "Straight to the speakers."                  # play | wav | raw
tts --stream wav -f script.txt | ffplay -nodisp -autoexit -    # WAV on stdout for piping
some-llm-command | tts --stream play -f -                      # speaks while the text is still arriving
```

Each line of the script becomes one segment (`--split paragraph` or `--split sentence` to change that), joined by a 0.35 s pause (`--pause`). The JSON looks like:

```json
{ "duration": 12.945, "segments": [
    { "index": 0, "text": "This is a test of the local voice pipeline.", "start": 0.0, "end": 2.56 },
    { "index": 1, "text": "It runs fully offline on the CPU.", "start": 2.91, "end": 8.158 } ] }
```

Put the narration on a video:

```bash
ffmpeg -i video.mp4 -i narration.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -shortest out.mp4
```

More in the docs:

- [docs/usage.md](docs/usage.md): every option, the timing format, video recipes, pronunciation tips, streaming (`--stream`)
- [docs/voices.md](docs/voices.md): voices by language, with quality grades
- [docs/troubleshooting.md](docs/troubleshooting.md): common errors and fixes

## Uninstall

```bash
./uninstall.sh            # or: curl -fsSL https://raw.githubusercontent.com/Flecart/kokoro-voiceover/main/uninstall.sh | bash -s -- -y
```

It removes the install directory, the `tts` command and the PATH line the installer added. It leaves `ffmpeg` and `python3-venv` installed, since other software may use them.

## Credits and license

- Model: [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), Apache-2.0.
- ONNX runtime wrapper and model export: [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), MIT.
- Phonemization: [phonemizer](https://github.com/bootphon/phonemizer) and [espeak-ng](https://github.com/espeak-ng/espeak-ng) (both GPL-3.0), installed as pip dependencies, not bundled here.

The code in this repository is MIT-licensed; see [LICENSE](LICENSE).
