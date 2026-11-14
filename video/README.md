# Demo video (T27)

`scripts/make_video.py` turns `video/storyboard.yaml` into the jury video. The storyboard is the single source of
truth for scenes, order, URLs, actions, durations and voice-over text. The script never duplicates any of that.

```bash
pip install -r viewer/requirements.txt -r video/requirements.txt
python -m playwright install chromium          # once; ffmpeg must be on PATH
python scripts/make_video.py --cut short       # ~2:10, no terminal scene
python scripts/make_video.py --cut submission  # <= 3:00 (the script fails if longer)
# after the holdout run, fill the one holdout sentence of the Proof scene with real numbers:
python scripts/make_video.py --cut submission --holdout "it found 6 of 6 incidents with zero violations"
```

Options: `--voice auto|human|tts|none` (default `auto`), `--holdout "<text>"`, `--keep` (keep the per-scene clips
in `out/video/<cut>_work/`), `--storyboard <path>`.

## What it does
1. Runs `python run.py data/quickstart --out out/qs.jsonl` (fresh decisions) and deletes `out/video_approvals.json`,
   so the staffing approval starts pending.
2. Runs the terminal scene's commands for real. If `data/holdout/` does not exist it uses `data/dev`
   (output to `out/video/decisions_dev.jsonl`) and the scene's `voiceover_if_dev_fallback` text. Output shown is the
   real output, trimmed to its last lines; `RESULT: PASS` is always kept. The typing is compressed in time.
3. Builds the voice per scene (see below). A scene lasts `max(storyboard seconds, voice + 0.5 s)`.
4. Starts the viewer (`streamlit run viewer/app.py` on the storyboard port, or a free one if it's busy, with
   `PLATO40_NO_LLM=1` and `PLATO40_APPROVALS=out/video_approvals.json`) and records one clip per scene with
   Playwright Chromium (`record_video_dir`) at 1920×1080. The storyboard `zoom` is applied as device scale
   (viewport 1920/zoom × 1080/zoom CSS px, rendered at 1920×1080), so text is sharp. Timing starts only after the
   scene's first `wait_for_text` is visible and Streamlit has stopped running. A visible cursor dot moves to every
   hovered or clicked element. Actions: `wait_for_text`, `scroll_to_text` (smooth), `hover`, `highlight_text`,
   `click_button`, `click_button_near_text` (then highlights the "recorded: …" mock result), `pause`,
   `scroll_to_top`, `next_url_at_seconds` (switches to the second of `urls`). A selector that is not found prints a
   `WARN` and the scene continues.
5. Assembles with ffmpeg: 1.5 s title card ("Steward — runs the building, answers to people" /
   "PLATO-40 is a fictional building · synthetic data · simulated effects"), all scenes, H.264 + AAC, 30 fps,
   `-preset veryfast`, 1 s fade from and to black, captions burned in (white on a dark translucent box,
   bottom centre).

## Outputs (`out/video/`, git-ignored — never commit the mp4 files)
| File | What |
|---|---|
| `steward_<cut>.mp4` | video with voice and burned-in captions |
| `steward_<cut>_captions_only.mp4` | same picture, no audio |
| `<cut>.srt` | captions (≤ 2 lines × 42 chars, per sentence, timed to the voice) |
| `<cut>_voiceover.txt` | final spoken text with timestamps and the voice source per scene |
| `<cut>_contact_sheet.png` | middle frame of every scene, labelled with the scene id |

## Voice
- `--voice auto`: `video/vo/<scene id>.wav` if it exists (human recording), else Piper TTS, else captions only.
- `--voice human`: only the recordings; fails if one is missing. `--voice tts`: Piper for every scene.
  `--voice none`: no audio (captions timed by text length).
- The spoken text is the storyboard text after placeholder substitution: `[HOLDOUT]` is replaced by `--holdout`
  verbatim; if it is empty, the whole sentence containing it is dropped. Nothing is added or rephrased.

**TTS engine:** `piper-tts==1.2.0`, the MIT-licensed [Piper](https://github.com/rhasspy/piper) release (1.3+ moved
to GPL-3.0; the script also works with it if installed instead).
**TTS voice:** Piper (offline, runs on CPU) with the voice
**`en_US-ljspeech-high`** (US English, female, 22.05 kHz) from
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/high). The voice
model is trained on the [LJ Speech dataset](https://keithito.com/LJ-Speech-Dataset/), which is in the **public
domain**, so the generated audio can be redistributed. It's downloaded once into `out/video/voices/` (not committed).
Speech rate: `length_scale 0.9` (`SPEECH_RATE` in the script).

**Recording a human voice:** put one mono WAV per scene into `video/vo/` named after the scene id (`building.wav`,
`name.wav`, `terminal.wav`, `water.wav`, `surge.wav`, `heating.wav`, `learned.wav`, `proof.wav`), read exactly the
storyboard text, then run with `--voice auto` or `--voice human`. Captions are then spread over each recording by
sentence length.
