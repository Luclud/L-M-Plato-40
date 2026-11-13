# T27 — Generate the Steward demo video automatically

**Lane:** C presentation · **Priority:** P0 · **Depends on:** viewer on main (T17–T25, #40, #41) ·
**Score impact:** demo & presentation (20); the submission requires a 3-minute end-to-end video

## Goal
One command turns `video/storyboard.yaml` into finished demo videos of the real Steward viewer: a ~2:00 cut and a
≤ 3:00 submission cut, with burned-in captions and a voice track. The storyboard is the source of truth (scenes, URLs,
actions, durations, voice-over text); humans edit only that file. Nothing in the video may be invented: every screen
is the real viewer on the real pipeline output, every number comes from the storyboard text.

## Command
```bash
pip install -r video/requirements.txt && python -m playwright install chromium   # + ffmpeg on PATH
python scripts/make_video.py --cut short                    # -> out/video/steward_short.mp4
python scripts/make_video.py --cut submission --holdout "it found 6 incidents with zero safety violations"
```
Options: `--cut short|submission` · `--voice auto|human|tts|none` (default auto) · `--holdout "<text>"` (fills
`[HOLDOUT]`; empty → that whole sentence is dropped, never a guessed number) · `--keep` (keep per-scene clips).

## Do
1. **Prepare:** `python run.py data/quickstart --out out/qs.jsonl` fresh; start the viewer
   (`streamlit run viewer/app.py --server.headless true -- --decisions out/qs.jsonl --data data/quickstart`) with
   `PLATO40_APPROVALS=out/video_approvals.json` deleted first (the staffing request must be pending on camera); wait
   until the page answers; stop it at the end.
2. **Record each browser scene** with Playwright Chromium (`record_video_dir`, 1920×1080, `zoom` from the storyboard
   via CSS), one clip per scene. Implement the storyboard actions: `wait_for_text`, `scroll_to_text` (smooth),
   `hover`, `highlight_text` (subtle outline for ~1.5 s), `click_button`, `click_button_near_text`, `pause`,
   `scroll_to_top`, `next_url_at_seconds`. Headless shows no mouse pointer → inject a visible cursor dot that moves
   smoothly to each hovered/clicked element. Trim each clip to the scene length; wait for Streamlit to finish
   rendering (no spinners) before the clip's timer starts.
3. **Terminal scene** (`kind: terminal`): run the listed commands for real (fallback `data/dev` + the scene's
   `voiceover_if_dev_fallback` if `data/holdout` is missing), capture the real output (trim long logs to the last lines
   that matter, always keep `RESULT: PASS`), render it as a terminal-styled page (dark, monospace, typed command,
   output appearing line by line) and record it like a browser scene.
4. **Voice track per scene** (`--voice auto`): use `video/vo/<scene id>.wav` if a human recorded it (`human`); else
   offline TTS (`tts`) with **Piper** (MIT engine; use an English voice whose licence allows redistribution, write the
   voice name + licence in `video/README.md`); if neither is available, `none` = captions only. Scene length =
   max(storyboard `seconds`, voice length + 0.5 s); hold the last frame if the voice runs longer.
5. **Captions:** generate `out/video/<cut>.srt` from the voice-over text (≤ 2 lines, ≤ 42 characters per line, timed
   by sentence, aligned to the scene's audio), and burn them in (white text, dark translucent box, bottom centre).
6. **Assemble** with ffmpeg: H.264 + AAC, 1920×1080, 30 fps, `-preset veryfast`, 1 s fade from/to black, a 1.5 s title
   card "Steward — runs the building, answers to people" with the small line "PLATO-40 is a fictional building ·
   synthetic data · simulated effects". Fail if the `submission` cut is longer than 3:00.
7. **Review aid:** `out/video/<cut>_contact_sheet.png` (one frame from the middle of every scene, labelled with the
   scene id) and `out/video/<cut>_voiceover.txt` (final text with timestamps) for the PR.
8. **Deliver:** never commit the mp4 files (out/ is git-ignored). Upload `steward_short.mp4`, `steward_submission.mp4`
   and the `.srt` files as assets of a GitHub **draft release** named `demo-video` and put the links in the PR.

## Rules
- Only new files: `scripts/make_video.py`, `video/requirements.txt`, `video/README.md`, `video/vo/.gitkeep`,
  `tests/test_storyboard.py`. Do not change `viewer/`, `plato40/`, `run.py`, `validate.py`, `eval/`, `data/`.
  If a selector cannot be found reliably, use visible text (Playwright `get_by_text` / `get_by_role`), never a change
  to the viewer.
- Voice-over and captions use the storyboard text verbatim (placeholders filled); no new claims, numbers or names.
- Approve clicks go only to `out/video_approvals.json` (mock).
- Third-party tools MIT-compatible (Playwright Apache-2.0, PyYAML MIT, ffmpeg as an external binary, Piper MIT).

## Acceptance criteria
- `python scripts/make_video.py --cut short` and `--cut submission` both finish without manual steps on a clean
  checkout; `short` ≈ 2:00–2:15, `submission` ≤ 3:00.
- Every scene shows the page the storyboard names (check with the contact sheet); captions match the voice; the
  staffing approval visibly flips to "Approved (mock)"; the Proof scene shows PASS.
- `tests/test_storyboard.py`: the YAML loads, every scene of every cut exists and has voice-over, every cut's
  `seconds` sum ≤ 180, `[HOLDOUT]` appears only in the proof scene. pytest green (skip video tests when
  playwright/ffmpeg are missing).
- PR: contact sheets of both cuts, the voice used (human / Piper voice + licence / none), durations, release links.

## Files to touch
`scripts/make_video.py`, `video/requirements.txt`, `video/README.md`, `video/vo/.gitkeep`, `tests/test_storyboard.py`
(the storyboard `video/storyboard.yaml` already exists — edit only to fix a selector text, and say so in the PR).
