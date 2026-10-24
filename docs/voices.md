# Voices

Run `tts --list-voices` for the ids your install has. The first letter of an id sets the language, the second the gender (`f`/`m`).

Grades are from the model authors' [VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) and reflect how much and how clean the training data for each voice was. Listening preference is subjective, so try a few.

| Language | Voices (grade) | `-l` code |
|---|---|---|
| American English | **af_heart (A)**, **af_bella (A-)**, af_nicole (B-), af_aoede (C+), af_kore (C+), af_sarah (C+), am_fenrir (C+), am_michael (C+), am_puck (C+), af_alloy (C), af_nova (C), af_sky (C-), af_jessica (D), af_river (D), am_echo (D), am_eric (D), am_liam (D), am_onyx (D), am_santa (D-), am_adam (F+) | `en-us` |
| British English | bf_emma (B-), bf_isabella (C), bm_fable (C), bm_george (C), bm_lewis (D+), bf_alice (D), bf_lily (D), bm_daniel (D) | `en-gb` |
| Italian | if_sara (C), im_nicola (C) | `it` |
| French | ff_siwis (B-) | `fr-fr` |
| Spanish | ef_dora, em_alex, em_santa (ungraded) | `es` |
| Brazilian Portuguese | pf_dora, pm_alex, pm_santa (ungraded) | `pt-br` |
| Hindi | hf_alpha, hf_beta, hm_omega, hm_psi (C) | `hi` |
| Japanese | jf_alpha (C+), jf_gongitsune (C), jf_tebukuro (C), jf_nezumi (C-), jm_kumo (C-) | `ja` |
| Mandarin Chinese | zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang (D) | `cmn` |

Good starting points:

- **English narration:** `af_heart`, then `af_bella`. For a male voice, `am_michael` or `am_fenrir`; for British, `bf_emma` or `bm_george`.
- **Italian:** `if_sara` or `im_nicola`. Both are intelligible and natural enough for narration, but a step below the top English voices.

English is by far the best-supported language. Upstream Kokoro uses dedicated tokenizers for Japanese and Mandarin, while this tool phonemizes every language with espeak-ng, so expect those two to sound weaker than upstream demos.

To hear several voices quickly:

```bash
for v in af_heart af_bella am_michael bf_emma bm_george; do
  tts -q "This is $v reading a line of narration." -v "$v" -o "sample_$v.mp3"
done
```
