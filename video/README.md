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
   `PLATO40_NO_LLM=1` unless `--copilot`, and `PLATO40_APPROVALS=out/video_approvals.json`) and records one clip per scene with
   Playwright Chromium (`record_video_dir`). The storyboard `zoom` sets the viewport to 1920/zoom × 1080/zoom CSS
   px; Playwright records CSS pixels, so the clip is recorded at that size and scaled to 1920×1080 (full frame, no
   grey margin). Timing starts only after the
   scene's first `wait_for_text` is visible and Streamlit has stopped running. A visible cursor dot moves to every
   hovered or clicked element. Actions: `wait_for_text`, `scroll_to_text` (smooth), `hover`, `highlight_text`,
   `click_button`, `click_button_near_text` (then highlights the "recorded: …" mock result), `pause`,
   `scroll_to_top`, `next_url_at_seconds` (switches to the second of `urls`). A selector that is not found prints a
   `WARN` and the scene continues.
5. Assembles with ffmpeg: 1.5 s title card ("Steward · Runs the building, answers to people" /
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
- `--voice elevenlabs`: [ElevenLabs](https://elevenlabs.io) text-to-speech for every scene (`pip install elevenlabs`,
  `ELEVENLABS_API_KEY` in the environment with the *Text to Speech* permission; never committed). Default voice George
  (pre-made, `ELEVENLABS_VOICE_ID` overrides), model `eleven_multilingual_v2` (`ELEVENLABS_MODEL`), expressive settings
  in `ELEVEN_SETTINGS`. Each sentence is cached in `out/video/voices/elevenlabs/`, so a re-render costs no characters.
  Audio generated with ElevenLabs is subject to the ElevenLabs terms of the account used (commercial use needs a paid
  plan).
- Scenes with storyboard `subtitles`: each spoken line starts at its `at` cue (or right after the previous line if
  that is still speaking), so the voice stays on the action it describes, as long as the scene keeps its storyboard
  length; the captions follow the voice. `VOICE_TEMPO` (e.g. `1.1`) speeds up synthetic speech.
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

## Final cut (T32)
`video/storyboard_final.yaml` is the final jury video: the real viewer pages on the **Holdout** year, every tab once
(except the hidden Guided demo), subtitles only, ≤ 3:00. The T27 storyboard and its cuts are unchanged.

```bash
# needs data/holdout/ (not in git, never commit it) next to the committed decisions_holdout.jsonl
python scripts/make_video.py --storyboard video/storyboard_final.yaml --cut final --voice none --copilot
# -> out/video/steward_final.mp4, final.srt, final_contact_sheet.png
```

- `--copilot`: the viewer may use the LLM key (`LLM_PROVIDER=anthropic`, `LLM_API_KEY`, `LLM_MODEL` from `.env` or the
  environment; never committed), so "Ask the building" answers live and the "AI copilot is off" banner is not shown.
  Without the flag the recorder forces `PLATO40_NO_LLM=1` (cached/offline answers, banner visible). The `ask` scene
  (23 s) leaves ~5 s per answer; use a model that accepts `temperature` (the published video used `claude-haiku-4-5`).

- `viewer.run_pipeline: false`: `run.py` is **not** run; the viewer opens `decisions_holdout.jsonl` and
  `data/holdout` exactly as configured (the frozen holdout run is never regenerated). The viewer starts with
  `PLATO40_DEFAULT_DATASET=off` (the `--decisions`/`--data` arguments decide the dataset) and
  `STEWARD_GUIDED_DEMO=0` (no Guided demo tab).
- `--cut` accepts any cut defined in the storyboard; every cut except `short` must stay ≤ 180 s.
- Scene `subtitles: [{at: <s from scene start>, text: "..."}]`: each line is shown verbatim from its `at` until the
  next line's `at` (the last one until the scene ends). A line longer than 2 × 42 characters is split into
  consecutive cards, never shortened. With `--voice none` no TTS runs.
- Extra actions: `click_text: "<visible text>"` (most specific clickable element: button, tab, link, label, chip),
  `click_key: "<streamlit key>"` (the widget inside `.st-key-<key>`), `type_text: {into: "<placeholder or label>",
  text: "...", submit: true|false}` (typed character by character), `scroll_by: <px>` (smooth). All of them move
  the visible cursor first. `click_button_near_text` now highlights the mock result line
  ("Approved (mock), nothing was executed"); if the card left the "Pending" list it opens "Decided" to show it.
  Text lookups prefer what appeared since the last click (e.g. the opened room panel over the KPI row).
- Every subtitle number must match the Holdout UI (facts table in `docs/tasks/T32-final-video.md`); if one does
  not, only that number is removed from the subtitle.
