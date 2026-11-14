"""T27: the storyboard is consistent and the video helpers keep the honesty rules (no invented numbers)."""
import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
SB = yaml.safe_load((ROOT / "video" / "storyboard.yaml").read_text(encoding="utf-8"))
SCENES = {s["id"]: s for s in SB["scenes"]}


def _mv():
    spec = importlib.util.spec_from_file_location("make_video", ROOT / "scripts" / "make_video.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_video"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cuts_reference_existing_scenes():
    assert set(SB["cuts"]) >= {"short", "submission"}
    for cut, order in SB["cuts"].items():
        assert order and all(sid in SCENES for sid in order), cut
        assert len(order) == len(set(order)), cut


def test_every_scene_has_voiceover_and_length():
    for sid, sc in SCENES.items():
        assert str(sc.get("voiceover", "")).strip(), sid
        assert float(sc["seconds"]) > 0, sid
        if sc.get("kind") == "terminal":
            assert sc.get("commands") and str(sc.get("voiceover_if_dev_fallback", "")).strip()
        else:
            assert sc.get("url") or sc.get("urls"), sid


@pytest.mark.parametrize("cut", ["short", "submission"])
def test_cut_fits_three_minutes(cut):
    assert sum(float(SCENES[s]["seconds"]) for s in SB["cuts"][cut]) <= 180


def test_holdout_placeholder_only_in_proof():
    for sid, sc in SCENES.items():
        has = "[HOLDOUT]" in yaml.safe_dump(sc)
        assert has == (sid == "proof"), sid


def test_empty_holdout_drops_the_sentence_and_value_is_used_verbatim():
    mv = _mv()
    proof = SCENES["proof"]
    dropped = mv.scene_text(proof, {"HOLDOUT": ""})
    assert "[HOLDOUT]" not in dropped and "holdout" not in dropped.lower()
    original = set(re.findall(r"\d[\d,.]*", proof["voiceover"]))
    assert set(re.findall(r"\d[\d,.]*", dropped)) <= original  # no invented numbers
    filled = mv.scene_text(proof, {"HOLDOUT": "it found 6 of 6 incidents"})
    assert "it found 6 of 6 incidents" in filled and "[HOLDOUT]" not in filled


def test_terminal_dev_fallback_text():
    mv = _mv()
    sc = SCENES["terminal"]
    assert mv.scene_text(sc, {}, dev_fallback=True) == mv.fill_placeholders(sc["voiceover_if_dev_fallback"], {})
    assert mv.scene_text(sc, {}, dev_fallback=False) == mv.fill_placeholders(sc["voiceover"], {})


def test_captions_are_short_and_cover_the_text():
    mv = _mv()
    for sc in SCENES.values():
        for s in mv.split_sentences(mv.scene_text(sc, SB.get("placeholders") or {})):
            chunks = mv.caption_chunks(s)
            assert " ".join(c.replace("\n", " ") for c in chunks) == s
            for c in chunks:
                rows = c.split("\n")
                assert len(rows) <= 2 and all(len(r) <= 42 for r in rows), c


def test_srt_timing_is_monotonic():
    mv = _mv()
    cues = mv.caption_cues(mv.proportional_times(["First sentence here.", "A second, longer sentence " * 4], 1.0, 9.0))
    assert all(a < b for a, b, _ in cues) and all(cues[i][1] <= cues[i + 1][0] + 1e-9 for i in range(len(cues) - 1))
    srt = mv.to_srt(cues)
    assert srt.startswith("1\n00:00:01,000 --> ") and "\n\n2\n" in srt


def test_trim_output_keeps_result_line():
    mv = _mv()
    out = "\n".join(["RESULT: PASS (0 safety/contract violations)"] + [f"warning {i}" for i in range(30)])
    lines = mv.trim_output(out, keep=5)
    assert "RESULT: PASS (0 safety/contract violations)" in lines and len(lines) <= 6


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_title_card_and_assembly(tmp_path):
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("PIL")
    mv = _mv()
    try:
        br = mv.Browser("http://localhost", 1.15, tmp_path)
    except Exception as e:  # browser binaries not installed
        pytest.skip(f"chromium unavailable: {e}")
    try:
        png = br.still(mv.title_html(), tmp_path / "title.png")
    finally:
        br.close()
    clip = mv.encode_segment(mv.Segment(image=png, seconds=1.0), tmp_path / "title.mp4")
    srt = tmp_path / "c.srt"
    srt.write_text(mv.to_srt([(0.1, 0.9, "Steward")]), encoding="utf-8")
    out = tmp_path / "out.mp4"
    mv.ff("-i", str(clip), "-vf", mv.subtitle_filter(srt), "-c:v", "libx264", "-preset", "veryfast", str(out))
    sheet = mv.contact_sheet(out, [("title", 0.5)], tmp_path / "sheet.png")
    from PIL import Image
    assert Image.open(png).size == (1920, 1080) and sheet.exists()
