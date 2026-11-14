"""T32: the final storyboard (real pages, Holdout year, subtitles only) and the make_video.py extensions it uses."""
import importlib.util
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
SB = yaml.safe_load((ROOT / "video" / "storyboard_final.yaml").read_text(encoding="utf-8"))
SCENES = {s["id"]: s for s in SB["scenes"]}


def _mv():
    spec = importlib.util.spec_from_file_location("make_video", ROOT / "scripts" / "make_video.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_video"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_final_storyboard_loads_and_uses_frozen_holdout_run():
    v = SB["viewer"]
    assert v["run_pipeline"] is False
    assert v["decisions"] == "decisions_holdout.jsonl" and v["data"] == "data/holdout"
    order = SB["cuts"]["final"]
    assert order == ["building", "incident", "incidents", "housekeeping", "approvals", "ask", "learned", "proof",
                     "outro"]
    assert all(sid in SCENES for sid in order)


def test_every_scene_has_subtitles_sorted_inside_the_scene():
    for sid in SB["cuts"]["final"]:
        sc = SCENES[sid]
        ats = [float(x["at"]) for x in sc["subtitles"]]
        assert ats and ats == sorted(ats) and ats[0] >= 0, sid
        assert ats[-1] < float(sc["seconds"]), sid
        assert all(str(x["text"]).strip() for x in sc["subtitles"]), sid


def test_final_cut_fits_three_minutes():
    mv = _mv()
    assert mv.TITLE_S + sum(float(SCENES[s]["seconds"]) for s in SB["cuts"]["final"]) <= 180


def test_no_guided_demo_page():
    for sc in SB["scenes"]:
        for u in sc.get("urls") or [sc["url"]]:
            assert not u.startswith("/tour"), sc["id"]


def test_subtitle_times_are_verbatim_and_contiguous():
    mv = _mv()
    sc = SCENES["incident"]
    times = mv.subtitle_times(sc["subtitles"], sc["seconds"])
    assert [t for t, _, _ in times] == [x["text"] for x in sc["subtitles"]]
    assert times[0][1] == 0 and times[-1][2] == sc["seconds"]
    assert all(a[2] == b[1] for a, b in zip(times, times[1:]))
    cues = mv.caption_cues(times)  # long lines are split into cards, never shortened
    assert " ".join(c.replace("\n", " ") for _, _, c in cues).split() == " ".join(t for t, _, _ in times).split()
    assert all(len(r) <= mv.CAPTION_WIDTH for _, _, c in cues for r in c.split("\n"))


def test_title_has_no_em_dash():
    mv = _mv()
    assert mv.TITLE == "Steward · Runs the building, answers to people" and "—" not in mv.TITLE


def test_unknown_cut_is_rejected():
    mv = _mv()
    with pytest.raises(SystemExit, match="not in storyboard cuts"):
        mv.main(["--storyboard", str(ROOT / "video" / "storyboard_final.yaml"), "--cut", "nope", "--voice", "none"])


def test_voice_scene_starts_each_sentence_at_its_subtitle_cue(tmp_path):
    import wave
    mv = _mv()

    class FakeTTS:
        def sentence(self, text, raw, context):
            p = raw.with_suffix(".wav")
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(mv.RATE)
                w.writeframes(b"\0\0" * int(mv.RATE * 2.0))   # every sentence lasts 2 s
            return p

    dst = tmp_path / "s_vo.wav"
    times = mv.voice_scene(FakeTTS(), ["a", "b", "c"], dst, tmp_path, starts=[0.0, 5.0, 6.0], seconds=12)
    assert [round(a, 2) for _, a, _ in times] == [0.0, 5.0, 7.2]   # cue 6 is still busy -> right after "b"
    assert abs(mv.wav_seconds(dst) - 9.2) < 0.01
    tight = mv.voice_scene(FakeTTS(), ["a", "b", "c"], dst, tmp_path, starts=[0.0, 5.0, 6.0], seconds=8)
    assert [round(a, 2) for _, a, _ in tight] == [0.0, 3.3, 5.5]   # only 1.1 s of slack to wait for "b"
    assert [round(a, 2) for _, a, _ in mv.voice_scene(FakeTTS(), ["a", "b"], dst, tmp_path)] == [0.0, 2.2]
    assert [round(a, 2) for _, a, _ in mv.voice_scene(FakeTTS(), ["a", "b"], dst, tmp_path, starts=[0.0, 5.0])] \
        == [0.0, 5.0]


def test_elevenlabs_needs_a_key(monkeypatch, tmp_path):
    pytest.importorskip("elevenlabs")
    mv = _mv()
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ELEVENLABS_API_KEY"):
        mv.ElevenLabsTTS(tmp_path)
