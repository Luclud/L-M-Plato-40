#!/usr/bin/env python3
"""Steward demo video from video/storyboard.yaml (ticket T27).

    python scripts/make_video.py --cut short
    python scripts/make_video.py --cut submission --holdout "it found 6 incidents with zero safety violations"

Records the real viewer (on a fresh quickstart run) scene by scene with Playwright Chromium, renders the terminal
scene from the real command output, adds a voice track (human recording, offline Piper TTS or none), burns in
captions and assembles everything with ffmpeg. Outputs go to out/video/ (git-ignored). See video/README.md.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STORYBOARD = ROOT / "video" / "storyboard.yaml"
VO_DIR = ROOT / "video" / "vo"
OUT = ROOT / "out" / "video"

W, H, FPS = 1920, 1080, 30
RATE = 48000
TITLE = "Steward · Runs the building, answers to people"
TITLE_SUB = "PLATO-40 is a fictional building · synthetic data · simulated effects"
TITLE_S, FADE_S, TAIL_S, GAP_S = 1.5, 1.0, 0.5, 0.2
SPEECH_RATE = 0.9  # Piper length_scale (<1 = faster); keeps the short cut near 2:10
MAX_SUBMISSION_S = 180.0
CAPTION_WIDTH, CAPTION_LINES = 42, 2
PIPER_VOICE = "en_US-ljspeech-high"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high/"
BRAND = "#1f6f50"


def log(msg: str) -> None:
    print(f"[video] {msg}", flush=True)


# ---------------------------------------------------------------- storyboard text

def load_storyboard(path: Path = STORYBOARD) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def fill_placeholders(text: str, placeholders: dict) -> str:
    """[KEY] -> value; an empty value drops the whole sentence containing [KEY] (never a guessed number)."""
    out = []
    for s in split_sentences(text):
        for key, value in (placeholders or {}).items():
            if f"[{key}]" in s:
                s = s.replace(f"[{key}]", str(value).strip()) if str(value or "").strip() else ""
        if s:
            out.append(s)
    return " ".join(out)


def scene_text(scene: dict, placeholders: dict, dev_fallback: bool = False) -> str:
    text = scene.get("voiceover_if_dev_fallback") if dev_fallback and scene.get("voiceover_if_dev_fallback") \
        else scene.get("voiceover", "")
    return fill_placeholders(text, placeholders)


def _fits(words: list[str], width: int, lines: int) -> bool:
    return len(textwrap.wrap(" ".join(words), width, break_long_words=False, break_on_hyphens=False)) <= lines


def caption_chunks(sentence: str, width: int = CAPTION_WIDTH, lines: int = CAPTION_LINES) -> list[str]:
    """Split a sentence into caption cards of <= `lines` x `width` chars, balanced (no 2-word orphan card)."""
    words, chunks = sentence.split(), []
    while words:
        n = 1
        while n < len(words) and _fits(words[:n + 1], width, lines):
            n += 1
        rest = words[n:]
        if rest and _fits(rest, width, lines):  # last two cards: split evenly, prefer a clause boundary
            both = words
            cands = [k for k in range(1, len(both)) if _fits(both[:k], width, lines) and _fits(both[k:], width, lines)]
            k = min(cands, key=lambda k: (abs(len(" ".join(both[:k])) - len(" ".join(both[k:])))
                                          - (12 if both[k - 1][-1] in ",;:" else 0)))
            chunks += [both[:k], both[k:]]
            break
        chunks.append(words[:n])
        words = rest
    return [_balanced(" ".join(c), width, lines) for c in chunks]


def _balanced(text: str, width: int, lines: int) -> str:
    """Narrowest wrap that still fits in `lines` lines -> two lines of similar length."""
    if len(text) <= width:
        return text
    for w in range(min(width, len(text)) // lines, width + 1):
        rows = textwrap.wrap(text, w, break_long_words=False, break_on_hyphens=False)
        if len(rows) <= lines:
            return "\n".join(rows)
    return "\n".join(textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False))


def caption_cues(sentence_times: list[tuple[str, float, float]]) -> list[tuple[float, float, str]]:
    """Sentence timings -> caption cues (<= 2 lines x 42 chars), split by character share within a sentence."""
    cues = []
    for text, a, b in sentence_times:
        chunks = caption_chunks(text)
        total = sum(len(c) for c in chunks) or 1
        t = a
        for c in chunks:
            d = (b - a) * len(c) / total
            cues.append((t, t + d, c))
            t += d
    return cues


def srt_time(s: float) -> str:
    ms = max(0, int(round(s * 1000)))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(cues: list[tuple[float, float, str]]) -> str:
    return "".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(cues, 1))


def proportional_times(sentences: list[str], start: float, end: float) -> list[tuple[str, float, float]]:
    total = sum(len(s) for s in sentences) or 1
    out, t = [], start
    for s in sentences:
        d = (end - start) * len(s) / total
        out.append((s, t, t + d))
        t += d
    return out


# ---------------------------------------------------------------- audio

def subtitle_times(subs: list[dict], seconds: float) -> list[tuple[str, float, float]]:
    """Storyboard `subtitles`: each line verbatim from its `at` until the next line's `at` (last: scene end)."""
    ats = [float(x["at"]) for x in subs] + [float(seconds)]
    return [(str(x["text"]), ats[i], ats[i + 1]) for i, x in enumerate(subs)]


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def to_std_wav(src: Path, dst: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", str(RATE), "-ac", "1",
                    "-sample_fmt", "s16", str(dst)], check=True)
    return dst


def write_track(parts: list[tuple[Path | None, float]], dst: Path) -> None:
    """Concatenate (wav or None=silence, seconds) parts into one 48 kHz mono track, padding/cutting each part."""
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(1), out.setsampwidth(2), out.setframerate(RATE)
        for path, seconds in parts:
            n = int(round(seconds * RATE))
            data = b""
            if path:
                with wave.open(str(path)) as w:
                    data = w.readframes(w.getnframes())[: n * 2]
            out.writeframes(data + b"\0\0" * (n - len(data) // 2))


def piper_model(cache: Path) -> Path | None:
    model = cache / f"{PIPER_VOICE}.onnx"
    try:
        for f in (model, model.with_suffix(".onnx.json")):
            if not f.exists():
                cache.mkdir(parents=True, exist_ok=True)
                log(f"downloading Piper voice {f.name} ...")
                urllib.request.urlretrieve(PIPER_URL + f.name, f.with_suffix(f.suffix + ".part"))
                f.with_suffix(f.suffix + ".part").rename(f)
        return model
    except Exception as e:  # offline or blocked -> captions only
        log(f"WARN Piper voice unavailable ({e})")
        return None


class Piper:
    def __init__(self, model: Path):
        from piper import PiperVoice
        self.voice = PiperVoice.load(str(model))

    def _say(self, text: str, w) -> None:
        if hasattr(self.voice, "synthesize_wav"):      # piper-tts >= 1.3 (GPL engine), if installed instead
            from piper import SynthesisConfig
            self.voice.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=SPEECH_RATE))
        else:                                          # piper-tts 1.2.0 (MIT engine, pinned)
            self.voice.synthesize(text, w, length_scale=SPEECH_RATE)

    def scene(self, sentences: list[str], dst: Path, tmp: Path) -> list[tuple[str, float, float]]:
        """One wav per sentence, joined with short gaps -> exact sentence timings for the captions."""
        parts, times, t = [], [], 0.0
        for i, s in enumerate(sentences):
            raw = tmp / f"{dst.stem}_{i}.wav"
            with wave.open(str(raw), "wb") as w:
                self._say(s, w)
            std = to_std_wav(raw, tmp / f"{dst.stem}_{i}_48k.wav")
            d = wav_seconds(std)
            parts += [(std, d), (None, GAP_S)]
            times.append((s, t, t + d))
            t += d + GAP_S
        write_track(parts[:-1], dst)
        return times


# ---------------------------------------------------------------- browser recording

CURSOR_JS = """
(() => {
  const mk = () => {
    if (document.getElementById('__vcur')) return;
    const d = document.createElement('div'); d.id = '__vcur';
    d.style.cssText = 'position:fixed;left:' + (window.__vx || -50) + 'px;top:' + (window.__vy || -50) + 'px;' +
      'width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;background:rgba(25,25,25,.55);' +
      'border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.35),0 2px 8px rgba(0,0,0,.35);' +
      'z-index:2147483647;pointer-events:none;transition:left .7s ease-in-out,top .7s ease-in-out,transform .15s';
    document.documentElement.appendChild(d);
  };
  window.__cursorTo = (x, y) => { mk(); const d = document.getElementById('__vcur'); d.style.left = x + 'px'; d.style.top = y + 'px'; };
  window.__cursorClick = () => { const d = document.getElementById('__vcur'); if (!d) return;
    d.style.transform = 'scale(.65)'; setTimeout(() => d.style.transform = '', 200); };
  document.addEventListener('DOMContentLoaded', mk);
})();
"""

# Most specific visible element whose text contains `needle` (case-insensitive); optional: a button near a text.
FIND_JS = """
([needle, mode, near]) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = e => (e.innerText || (e.tagName === 'INPUT' ? e.value : '') || '').trim();  // selectbox value = input
  const has = (e, n) => txt(e).toLowerCase().includes(n.toLowerCase());
  let best = null, bestScore = Infinity;
  if (mode === 'button') {
    for (const b of document.querySelectorAll('button')) {
      if (!vis(b) || !has(b, needle)) continue;
      let a = b, score = Infinity;
      for (let i = 0; i < 30 && a; i++, a = a.parentElement) if (has(a, near)) { score = txt(a).length; break; }
      if (score < bestScore) { best = b; bestScore = score; }
    }
  } else if (mode === 'click') {
    const sel = 'button, a, label, summary, [role=button], [role=tab], [role=option], [role=switch], [role=checkbox],' +
                ' [data-baseweb=tag], [data-testid=stExpander] details > summary';
    for (const e of document.querySelectorAll(sel)) {
      if (!vis(e) || !has(e, needle)) continue;
      const score = txt(e).length - (txt(e).toLowerCase() === needle.toLowerCase() ? 1e6 : 0);
      if (score < bestScore) { best = e; bestScore = score; }
    }
  } else {
    for (const e of document.querySelectorAll('body *')) {
      if (e.id === '__vcur' || ['SCRIPT', 'STYLE'].includes(e.tagName) || !vis(e) || !has(e, needle)) continue;
      // exact text first; among equals prefer what appeared since the last click (e.g. the opened room panel)
      const score = txt(e).length - (txt(e).toLowerCase() === needle.toLowerCase() ? 1e6 : 0)
                    - (e.hasAttribute('data-vold') ? 0 : 1e4);
      if (score < bestScore) { best = e; bestScore = score; }
    }
  }
  if (!best) return null;
  const id = 'v' + Math.random().toString(36).slice(2);
  best.setAttribute('data-vid', id);
  return id;
}
"""

SCROLL_JS = """
([id, top]) => {
  const el = document.querySelector('[data-vid="' + id + '"]');
  let c = el.parentElement;
  while (c && !(/(auto|scroll)/.test(getComputedStyle(c).overflowY) && c.scrollHeight > c.clientHeight)) c = c.parentElement;
  c = c || document.scrollingElement;
  const y = top ? 0 : el.getBoundingClientRect().top - c.getBoundingClientRect().top + c.scrollTop - 110;
  c.scrollTo({top: Math.max(0, y), behavior: 'smooth'});
}
"""

MARK_OLD_JS = "() => document.querySelectorAll('body *').forEach(e => e.setAttribute('data-vold', ''))"

SCROLL_BY_JS = """
(dy) => { const m = document.querySelector('section.stMain, [data-testid="stMain"]');
  let c = m; while (c && !(c.scrollHeight > c.clientHeight && /(auto|scroll)/.test(getComputedStyle(c).overflowY)))
    c = c.parentElement;
  (c || document.scrollingElement).scrollBy({top: dy, behavior: 'smooth'}); }
"""

SCROLL_TOP_JS = """
() => { document.querySelectorAll('*').forEach(c => { if (c.scrollTop > 0) c.scrollTo({top: 0, behavior: 'smooth'}); });
        window.scrollTo({top: 0, behavior: 'smooth'}); }
"""

HIGHLIGHT_JS = """
([id, ms]) => { const el = document.querySelector('[data-vid="' + id + '"]'); const old = el.style.cssText;
  el.style.outline = '3px solid rgba(240,160,40,.95)'; el.style.outlineOffset = '4px'; el.style.borderRadius = '6px';
  el.style.transition = 'outline-color .3s'; setTimeout(() => { el.style.cssText = old; }, ms); }
"""

ACTION_COST = {"hover": 1.8, "scroll_to_text": 1.6, "highlight_text": 1.8, "click_button": 3.0,
               "click_button_near_text": 7.0, "wait_for_text": 0.3, "scroll_to_top": 1.5, "click_text": 2.6,
               "click_key": 2.6, "type_text": 2.0, "scroll_by": 1.2}
TYPE_CPS = 25  # type_text speed, characters per second


@dataclass
class Segment:
    video: Path | None = None
    start: float = 0.0
    seconds: float = 0.0
    image: Path | None = None


@dataclass
class Clip:
    scene: str
    seconds: float
    segments: list[Segment] = field(default_factory=list)


def quoted(spec: str) -> str:
    q = re.findall(r'"([^"]+)"', str(spec))
    return q[-1] if q else str(spec)


class Browser:
    def __init__(self, base_url: str, zoom: float, tmp: Path):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch()
        self.base_url, self.zoom, self.tmp = base_url.rstrip("/"), float(zoom or 1), tmp
        self.cursor = (W / self.zoom * 0.62, H / self.zoom * 0.58)

    def close(self):
        self.browser.close()
        self.pw.stop()

    def context(self, record: bool = True):
        vp = {"width": round(W / self.zoom), "height": round(H / self.zoom)}
        kw = dict(viewport=vp, device_scale_factor=self.zoom)
        if record:
            # Playwright records CSS pixels (device scale is ignored): record the viewport size, encode_segment
            # scales it to W x H, otherwise the page sits in the top-left corner with a grey margin
            kw.update(record_video_dir=str(self.tmp / "raw"), record_video_size=vp)
        ctx = self.browser.new_context(**kw)
        ctx.add_init_script(f"window.__vx={self.cursor[0]};window.__vy={self.cursor[1]};" + CURSOR_JS)
        return ctx

    # -- helpers
    def _find(self, page, needle: str, mode: str = "text", near: str = "", timeout: float = 15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            vid = page.evaluate(FIND_JS, [needle, mode, near])
            if vid:
                return page.locator(f'[data-vid="{vid}"]'), vid
            page.wait_for_timeout(250)
        raise LookupError(f"text not found: {needle!r}" + (f" near {near!r}" if near else ""))

    def _settle(self, page, extra: float = 0.8):
        """Wait until Streamlit has finished running the script (no running/status widget) and charts drew."""
        page.wait_for_timeout(300)
        try:
            page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]') && "
                                   "!document.querySelector('.stSpinner, [data-testid=\"stSpinner\"]')",
                                   timeout=30000)
        except Exception:
            log("WARN viewer still busy after 30 s")
        page.wait_for_timeout(int(extra * 1000))

    def _move_to(self, page, loc, hold: float = 0.9):
        loc.scroll_into_view_if_needed(timeout=5000)
        box = loc.bounding_box()
        if not box:
            return
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.evaluate(f"window.__cursorTo({x},{y})")
        page.wait_for_timeout(800)
        page.mouse.move(x, y)
        self.cursor = (x, y)
        page.wait_for_timeout(int(hold * 1000))

    def _click(self, page, loc, vid: str | None = None) -> None:
        """Visible click: cursor dot glides to the element, presses, Streamlit reruns."""
        page.evaluate(MARK_OLD_JS)
        box = loc.bounding_box() if vid else None
        if vid and not (box and 110 <= box["y"] and box["y"] + box["height"] <= page.viewport_size["height"]):
            page.evaluate(SCROLL_JS, [vid, False])  # below the sticky top bar, not under it
            page.wait_for_timeout(900)
        else:
            loc.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(300)
        self._move_to(page, loc, hold=0.4)
        page.evaluate("window.__cursorClick()")
        loc.click(timeout=5000)
        self._settle(page, 0.6)

    def _input(self, page, label: str):
        """Text input / textarea / chat input by placeholder, aria-label or widget label."""
        cands = [page.get_by_placeholder(label), page.get_by_label(label),
                 page.locator(f'[data-testid="stChatInput"] textarea[placeholder*="{label}"]')]
        end = time.monotonic() + 8
        while time.monotonic() < end:
            for c in cands:
                for i in range(c.count()):
                    if c.nth(i).is_visible():
                        return c.nth(i)
            page.wait_for_timeout(250)
        raise LookupError(f"input not found: {label!r}")

    def open(self, ctx, url: str, ready_text: str | None):
        page = ctx.new_page()
        t0 = time.monotonic()
        page.goto(self.base_url + url, wait_until="domcontentloaded", timeout=60000)
        page.locator("section.stMain, [data-testid='stMain'], .stApp").first.wait_for(timeout=60000)
        if ready_text:
            try:
                self._find(page, ready_text, timeout=30)
            except LookupError as e:
                log(f"WARN {e}")
        self._settle(page, 1.2)
        page.evaluate(f"window.__cursorTo({self.cursor[0]},{self.cursor[1]})")
        return page, time.monotonic() - t0

    def act(self, page, action: dict) -> None:
        (kind, arg), = action.items()
        try:
            if kind == "wait_for_text":
                self._find(page, str(arg), timeout=20)
            elif kind == "pause":
                page.wait_for_timeout(int(float(arg) * 1000))
            elif kind == "hover":
                loc, _ = self._find(page, quoted(arg), timeout=6)
                self._move_to(page, loc, hold=1.0)
            elif kind == "scroll_to_text":
                _, vid = self._find(page, str(arg), timeout=6)
                page.evaluate(SCROLL_JS, [vid, False])
                page.wait_for_timeout(1600)
            elif kind == "scroll_to_top":
                page.evaluate(SCROLL_TOP_JS)
                page.wait_for_timeout(1500)
            elif kind == "highlight_text":
                loc, vid = self._find(page, str(arg), timeout=6)
                loc.scroll_into_view_if_needed(timeout=5000)
                page.evaluate(HIGHLIGHT_JS, [vid, 1500])
                page.wait_for_timeout(1800)
            elif kind in ("click_button", "click_button_near_text"):
                label, near = (arg["button"], arg["text"]) if isinstance(arg, dict) else (str(arg), "")
                loc, vid = self._find(page, label, mode="button", near=near or label, timeout=8)
                page.evaluate(SCROLL_JS, [vid, False])
                page.wait_for_timeout(1000)
                self._move_to(page, loc, hold=0.4)
                page.evaluate(MARK_OLD_JS)
                page.evaluate("window.__cursorClick()")
                loc.click(timeout=5000)
                self._settle(page, 0.6)
                if near:  # show the result where the button was ("Approved (mock), nothing was executed")
                    try:
                        try:
                            res, rid = self._find(page, "(mock)", timeout=1.2)  # page has settled already
                        except LookupError:
                            try:  # the decided card left the "Pending" list: open "Decided" to show it
                                tab, tid = self._find(page, "Decided", mode="click", timeout=2)
                                log(f"{label} near {near!r}: card moved to Decided -> showing it there")
                                self._click(page, tab, tid)
                                res, rid = self._find(page, "(mock)", timeout=3)
                            except LookupError:
                                res, rid = self._find(page, "recorded:", timeout=2)
                        page.evaluate(SCROLL_JS, [rid, False])
                        page.wait_for_timeout(900)
                        page.evaluate(HIGHLIGHT_JS, [rid, 1800])
                        page.wait_for_timeout(300)  # the scene's remaining seconds hold the result on screen
                    except LookupError:
                        pass
            elif kind == "click_text":
                loc, vid = self._find(page, str(arg), mode="click", timeout=8)
                self._click(page, loc, vid)
            elif kind == "click_key":
                box = page.locator(f".st-key-{arg}").first
                box.wait_for(state="visible", timeout=8000)
                inner = box.locator("button, [role=button], input, label, a")
                self._click(page, inner.first if inner.count() else box)
            elif kind == "type_text":
                loc = self._input(page, str(arg["into"]))
                self._move_to(page, loc, hold=0.3)
                loc.click(timeout=5000)
                loc.press_sequentially(str(arg["text"]), delay=int(1000 / TYPE_CPS))
                if arg.get("submit"):
                    page.wait_for_timeout(300)
                    loc.press("Enter")
                self._settle(page, 0.6)
            elif kind == "scroll_by":
                page.evaluate(SCROLL_BY_JS, int(arg))
                page.wait_for_timeout(1200)
            elif kind == "next_url_at_seconds":
                pass  # handled by record_scene
            else:
                log(f"WARN unknown action {kind}")
        except Exception as e:  # a missing selector must not kill the video; the warning names it
            log(f"WARN action {kind}={arg!r}: {str(e).splitlines()[0]}")

    def record_scene(self, scene: dict, seconds: float) -> Clip:
        actions = list(scene.get("actions") or [])
        urls = scene.get("urls") or [scene["url"]]
        split = next((float(a["next_url_at_seconds"]) for a in actions if "next_url_at_seconds" in a), None)
        groups = [actions]
        if split is not None and len(urls) > 1:
            i = next(i for i, a in enumerate(actions) if "next_url_at_seconds" in a)
            groups = [actions[:i], actions[i + 1:]]
            lengths = [split, seconds - split]
        else:
            lengths = [seconds]
        ctx = self.context()
        clip, pages = Clip(scene["id"], seconds), []
        ready = next((a["wait_for_text"] for a in actions if "wait_for_text" in a), None)
        for url, acts, length in zip(urls, groups, lengths):
            page, offset = self.open(ctx, url, ready)
            t0 = time.monotonic()
            est = sum(ACTION_COST.get(next(iter(a)), 0) + float(a.get("pause", 0)) for a in acts)
            gap = min(3.0, max(0.4, (length - est - 1.0) / (len(acts) + 1))) if acts else 0
            for a in acts:
                if next(iter(a)) not in ("pause", "wait_for_text"):
                    page.wait_for_timeout(int(gap * 1000))
                self.act(page, a)
            used = time.monotonic() - t0
            if used > length:
                log(f"WARN scene {scene['id']}: actions took {used:.1f}s > {length:.1f}s (clip is cut)")
            page.wait_for_timeout(int(max(0.0, length - used + 0.4) * 1000))
            pages.append(page)
            clip.segments.append(Segment(start=offset, seconds=length))
        paths = [Path(p.video.path()) for p in pages]
        ctx.close()
        for seg, p in zip(clip.segments, paths):
            seg.video = p
        return clip

    def record_html(self, scene_id: str, doc: str, seconds: float) -> Clip:
        ctx = self.context()
        page = ctx.new_page()
        t0 = time.monotonic()
        page.set_content(doc, wait_until="load")
        page.wait_for_timeout(500)
        offset = time.monotonic() - t0
        page.evaluate("window.__start && window.__start()")
        page.wait_for_timeout(int((seconds + 0.4) * 1000))
        path = Path(page.video.path())
        ctx.close()
        return Clip(scene_id, seconds, [Segment(path, offset, seconds)])

    def still(self, doc: str, dst: Path) -> Path:
        ctx = self.context(record=False)
        page = ctx.new_page()
        page.set_content(doc, wait_until="load")
        page.wait_for_timeout(300)
        page.screenshot(path=str(dst))
        ctx.close()
        # viewport (W/zoom, rounded) x zoom can be 1 px off (1670 x 1.15 = 1920.5 -> 1921): force exactly W x H
        from PIL import Image
        with Image.open(dst) as im:
            fixed = im.convert("RGB").resize((W, H), Image.LANCZOS) if im.size != (W, H) else None
        if fixed is not None:
            fixed.save(dst)
        return dst


# ---------------------------------------------------------------- terminal + title pages

def title_html() -> str:
    return f"""<html><body style="margin:0;background:#0f1f19;height:100vh;display:flex;align-items:center;
justify-content:center;font-family:'DejaVu Sans',Arial,sans-serif;color:#fff"><div style="text-align:center">
<div style="display:inline-flex;align-items:center;gap:22px"><div style="width:74px;height:74px;border-radius:16px;
background:{BRAND};display:flex;align-items:center;justify-content:center;font-size:44px;font-weight:700">S</div>
<div style="font-size:54px;font-weight:700;letter-spacing:.5px">{html.escape(TITLE)}</div></div>
<div style="margin-top:26px;font-size:24px;color:#b9cfc5">{html.escape(TITLE_SUB)}</div></div></body></html>"""


def trim_output(text: str, keep: int = 9) -> list[str]:
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    out = lines[-keep:]
    result = [ln for ln in lines if ln.startswith("RESULT:")]
    if result and result[-1] not in out:
        out = out[1:] + [result[-1]]
    return (["…"] if len(lines) > len(out) else []) + out


def run_terminal_commands(scene: dict) -> tuple[list[dict], bool]:
    holdout = (ROOT / "data" / "holdout").is_dir()
    runs = []
    for raw in scene.get("commands") or []:
        cmd = str(raw).split("#")[0].strip()
        if not holdout:
            cmd = cmd.replace("decisions_holdout.jsonl", "out/video/decisions_dev.jsonl").replace(
                "data/holdout", "data/dev")
        args = cmd.split()
        if args and args[0] == "python":
            args[0] = sys.executable
        log(f"terminal: {cmd}")
        t = time.monotonic()
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        runs.append({"cmd": cmd, "lines": trim_output(p.stdout + p.stderr), "elapsed": time.monotonic() - t,
                     "code": p.returncode})
        if p.returncode != 0:
            raise SystemExit(f"terminal command failed ({p.returncode}): {cmd}\n{p.stderr[-2000:]}")
    if not any("RESULT: PASS" in ln for r in runs for ln in r["lines"]):
        raise SystemExit("terminal scene: validate.py did not print RESULT: PASS")
    return runs, not holdout


def terminal_html(runs: list[dict], seconds: float) -> str:
    """Dark terminal; commands are typed, output appears line by line. Real output, compressed in time."""
    type_s, line_s = 1.4, 0.09
    fixed = sum(type_s + 0.4 + line_s * len(r["lines"]) for r in runs)
    wait = max(1.0, seconds - 2.5 - 0.6 - fixed)
    total_el = sum(r["elapsed"] for r in runs) or 1
    steps = []
    for r in runs:
        steps.append({"cmd": r["cmd"], "lines": r["lines"], "wait": wait * r["elapsed"] / total_el})
    return f"""<html><head><style>
body{{margin:0;background:#0b0f14;font-family:'DejaVu Sans Mono',monospace;color:#d6dde6}}
.win{{margin:34px 40px;border-radius:12px;background:#0f141b;box-shadow:0 0 0 1px #263040;height:calc(100vh - 68px);overflow:hidden}}
.bar{{height:34px;background:#1a212b;border-radius:12px 12px 0 0;display:flex;align-items:center;padding:0 14px;gap:8px;color:#8b97a7;font-size:14px}}
.dot{{width:12px;height:12px;border-radius:50%}} #t{{padding:18px 22px;font-size:17px;line-height:1.45;white-space:pre-wrap}}
.p{{color:#59d185}} .c{{color:#fff}} .ok{{color:#59d185;font-weight:700}} .dim{{color:#9aa6b5}}
.cur{{display:inline-block;width:9px;background:#d6dde6;animation:b 1s steps(1) infinite}} @keyframes b{{50%{{opacity:0}}}}
</style></head><body><div class="win"><div class="bar"><span class="dot" style="background:#ff5f56"></span>
<span class="dot" style="background:#ffbd2e"></span><span class="dot" style="background:#27c93f"></span>
<span style="margin-left:10px">steward — bash</span></div><div id="t"></div></div><script>
const steps = {json.dumps(steps)};
const T = document.getElementById('t'), sleep = ms => new Promise(r => setTimeout(r, ms));
const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
async function run() {{
  for (const s of steps) {{
    const line = document.createElement('div'); T.appendChild(line);
    for (let i = 0; i <= s.cmd.length; i++) {{
      line.innerHTML = '<span class="p">$ </span><span class="c">' + esc(s.cmd.slice(0, i)) + '</span><span class="cur">&nbsp;</span>';
      await sleep({type_s * 1000} / Math.max(1, s.cmd.length));
    }}
    line.innerHTML = '<span class="p">$ </span><span class="c">' + esc(s.cmd) + '</span>';
    const wait = document.createElement('div'); wait.className = 'dim'; T.appendChild(wait);
    const t0 = Date.now(); const spin = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';
    while (Date.now() - t0 < s.wait * 1000) {{ wait.textContent = spin[Math.floor((Date.now() - t0) / 90) % 10] + ' running'; await sleep(90); }}
    wait.remove();
    for (const l of s.lines) {{
      const d = document.createElement('div'); d.className = l.startsWith('RESULT: PASS') ? 'ok' : '';
      d.textContent = l; T.appendChild(d); await sleep({line_s * 1000});
    }}
    await sleep(400);
  }}
  const end = document.createElement('div'); end.innerHTML = '<span class="p">$ </span><span class="cur">&nbsp;</span>'; T.appendChild(end);
}}
window.__start = run;
</script></body></html>"""


# ---------------------------------------------------------------- ffmpeg

def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def encode_segment(seg: Segment, dst: Path) -> Path:
    vf = f"scale={W}:{H}:flags=lanczos,fps={FPS},format=yuv420p,tpad=stop_mode=clone:stop_duration={seg.seconds}"
    src = ["-loop", "1", "-i", str(seg.image)] if seg.image else ["-ss", f"{seg.start:.3f}", "-i", str(seg.video)]
    ff(*src, "-t", f"{seg.seconds:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-r", str(FPS), str(dst))
    return dst


def subtitle_filter(srt: Path) -> str:
    style = ("FontName=DejaVu Sans,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,"
             "BackColour=&H90000000,BorderStyle=3,Outline=6,Shadow=0,Alignment=2,MarginV=22")
    return f"subtitles=filename='{srt.as_posix()}':force_style='{style}'"


def contact_sheet(video: Path, marks: list[tuple[str, float]], dst: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    tw, th, cols = 640, 360, 3
    rows = (len(marks) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), "#111")
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    with tempfile.TemporaryDirectory() as td:
        for i, (sid, t) in enumerate(marks):
            png = Path(td) / f"{i}.png"
            ff("-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={tw}:{th}", str(png))
            im = Image.open(png).convert("RGB")
            d = ImageDraw.Draw(im)
            label = f"{i + 1}. {sid}  ({t:.0f}s)"
            x0, y0, x1, y1 = d.textbbox((12, 10), label, font=font)
            d.rectangle((x0 - 8, y0 - 6, x1 + 8, y1 + 6), fill="#000000")
            d.text((12, 10), label, font=font, fill="#ffffff")
            sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
    sheet.save(dst)
    return dst


# ---------------------------------------------------------------- viewer process

def free_port(preferred: int) -> int:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_viewer(cfg: dict) -> tuple[subprocess.Popen, str]:
    url = urlparse(cfg.get("base_url", "http://localhost:8501"))
    port = free_port(url.port or 8501)
    if port != (url.port or 8501):
        log(f"port {url.port} is busy -> viewer on {port}")
    approvals = ROOT / cfg.get("approvals_file", "out/video_approvals.json")
    approvals.unlink(missing_ok=True)
    env = dict(os.environ, PLATO40_APPROVALS=str(approvals), PLATO40_NO_LLM="1", PLATO40_DEFAULT_DATASET="off")
    if not cfg.get("run_pipeline", True):
        env["STEWARD_GUIDED_DEMO"] = "0"
    cmd = [sys.executable, "-m", "streamlit", "run", "viewer/app.py", "--server.headless", "true",
           "--server.port", str(port), "--browser.gatherUsageStats", "false", "--",
           "--decisions", cfg.get("decisions", "out/qs.jsonl"), "--data", cfg.get("data", "data/quickstart")]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://localhost:{port}"
    for _ in range(120):
        try:
            if urllib.request.urlopen(base + "/_stcore/health", timeout=2).status == 200:
                return proc, base
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("viewer did not start")


# ---------------------------------------------------------------- main

def voice_mode(requested: str, scene_id: str, piper_ok: bool) -> str:
    human = (VO_DIR / f"{scene_id}.wav").exists()
    if requested == "human":
        if not human:
            raise SystemExit(f"--voice human: missing {VO_DIR / (scene_id + '.wav')}")
        return "human"
    if requested == "auto":
        return "human" if human else "tts" if piper_ok else "none"
    if requested == "tts" and not piper_ok:
        raise SystemExit("--voice tts: Piper or its voice is not available (pip install -r video/requirements.txt)")
    return requested


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", default="short", help="a cut name from the storyboard's `cuts`")
    ap.add_argument("--voice", choices=["auto", "human", "tts", "none"], default="auto")
    ap.add_argument("--holdout", default=None, help="text for [HOLDOUT]; empty drops that sentence")
    ap.add_argument("--keep", action="store_true", help="keep per-scene clips in out/video/<cut>_work/")
    ap.add_argument("--storyboard", type=Path, default=STORYBOARD)
    args = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH")

    sb = load_storyboard(args.storyboard)
    cfg, scenes = sb["viewer"], {s["id"]: s for s in sb["scenes"]}
    placeholders = dict(sb.get("placeholders") or {})
    if args.holdout is not None:
        placeholders["HOLDOUT"] = args.holdout
    if args.cut not in sb["cuts"]:
        raise SystemExit(f"--cut {args.cut!r}: not in storyboard cuts {sorted(sb['cuts'])}")
    order = sb["cuts"][args.cut]
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / f"{args.cut}_work"
    shutil.rmtree(work, ignore_errors=True)
    (work / "raw").mkdir(parents=True)

    # 1. fresh pipeline output for the viewer; terminal scene runs its own commands for real
    if cfg.get("run_pipeline", True):
        log(f"python run.py {cfg['data']} --out {cfg['decisions']}")
        subprocess.run([sys.executable, "run.py", cfg["data"], "--out", cfg["decisions"]], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)
    else:
        log(f"run_pipeline: false -> using {cfg['decisions']} and {cfg['data']} as they are")
        for f in (cfg["decisions"], cfg["data"]):
            if not (ROOT / f).exists():
                raise SystemExit(f"missing {f} (run_pipeline: false needs it)")
    terminal = {}
    for sid in order:
        if scenes[sid].get("kind") == "terminal":
            terminal[sid] = run_terminal_commands(scenes[sid])

    # 2. voice per scene -> scene lengths and sentence timings
    piper = None
    if args.voice == "tts" or (args.voice == "auto" and any(not (VO_DIR / f"{s}.wav").exists() for s in order)):
        try:
            model = piper_model(OUT / "voices")
            piper = Piper(model) if model else None
        except ImportError:
            log("WARN piper-tts not installed -> captions only")
    plan = []
    for sid in order:
        sc = scenes[sid]
        subs = sc.get("subtitles")
        if subs:
            sentences = [str(x["text"]) for x in subs]
        else:
            text = scene_text(sc, placeholders, dev_fallback=terminal.get(sid, (None, False))[1])
            sentences = split_sentences(text)
        mode = voice_mode(args.voice, sid, piper is not None)
        wav, times = None, []
        if mode == "human":
            wav = to_std_wav(VO_DIR / f"{sid}.wav", work / f"{sid}_vo.wav")
            times = proportional_times(sentences, 0.0, wav_seconds(wav))
        elif mode == "tts":
            wav = work / f"{sid}_vo.wav"
            times = piper.scene(sentences, wav, work)
        voice_s = wav_seconds(wav) if wav else 0.0
        seconds = max(float(sc["seconds"]), voice_s + TAIL_S if wav else 0.0)
        if subs and not wav:
            times = subtitle_times(subs, seconds)
        elif not wav:
            times = proportional_times(sentences, 0.3, seconds - 0.5)
        plan.append({"id": sid, "mode": mode, "wav": wav, "seconds": round(seconds, 2), "times": times})
        log(f"scene {sid}: {mode}, voice {voice_s:.1f}s -> {seconds:.1f}s")
    total = TITLE_S + sum(p["seconds"] for p in plan)
    log(f"cut {args.cut}: {total:.1f}s")
    if args.cut != "short" and total > MAX_SUBMISSION_S:
        raise SystemExit(f"{args.cut} cut is {total:.1f}s > {MAX_SUBMISSION_S:.0f}s; shorten the storyboard")

    # 3. record
    proc, base = start_viewer(cfg)
    clips: list[Clip] = []
    try:
        br = Browser(base, cfg.get("zoom", 1.0), work)
        try:
            title_png = br.still(title_html(), work / "title.png")
            for p in plan:
                sc = scenes[p["id"]]
                log(f"recording {p['id']} ({p['seconds']:.1f}s)")
                if sc.get("kind") == "terminal":
                    clips.append(br.record_html(p["id"], terminal_html(terminal[p["id"]][0], p["seconds"]),
                                                p["seconds"]))
                else:
                    clips.append(br.record_scene(sc, p["seconds"]))
        finally:
            br.close()
    finally:
        proc.terminate()
        proc.wait(timeout=20)

    # 4. assemble: title + scenes (video), voice track, captions, fades
    parts = [encode_segment(Segment(image=title_png, seconds=TITLE_S), work / "000_title.mp4")]
    for i, c in enumerate(clips, 1):
        for j, seg in enumerate(c.segments):
            parts.append(encode_segment(seg, work / f"{i:03d}_{c.scene}_{j}.mp4"))
    (work / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    joined = work / "joined.mp4"
    ff("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(joined))

    cues, vo_lines, marks, t = [], [], [], TITLE_S
    for p in plan:
        marks.append((p["id"], t + p["seconds"] / 2))
        vo_lines.append(f"[{t:06.2f}] {p['id']} ({p['mode']}, {p['seconds']:.1f}s)")
        for s, a, b in p["times"]:
            vo_lines.append(f"  {t + a:06.2f}–{t + b:06.2f}  {s}")
            cues += caption_cues([(s, t + a, t + b)])
        t += p["seconds"]
    srt = OUT / f"{args.cut}.srt"
    srt.write_text(to_srt(cues), encoding="utf-8")
    (OUT / f"{args.cut}_voiceover.txt").write_text(
        f"Steward demo video, cut '{args.cut}', {total:.1f}s (title card {TITLE_S}s)\n\n" + "\n".join(vo_lines) + "\n",
        encoding="utf-8")

    fade = f"fade=t=in:st=0:d={FADE_S},fade=t=out:st={total - FADE_S:.3f}:d={FADE_S}"
    captioned = work / "captioned.mp4"
    ff("-i", str(joined), "-vf", f"{subtitle_filter(srt)},{fade}", "-t", f"{total:.3f}", "-c:v", "libx264",
       "-preset", "veryfast", "-crf", "20", "-r", str(FPS), "-pix_fmt", "yuv420p", "-an", str(captioned))
    silent = OUT / f"steward_{args.cut}_captions_only.mp4"
    ff("-i", str(captioned), "-c", "copy", "-movflags", "+faststart", str(silent))
    final = OUT / f"steward_{args.cut}.mp4"
    if any(p["wav"] for p in plan):
        track = work / "voice.wav"
        write_track([(None, TITLE_S)] + [(p["wav"], p["seconds"]) for p in plan], track)
        ff("-i", str(captioned), "-i", str(track), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
           "-b:a", "160k", "-af", f"afade=t=out:st={total - FADE_S:.3f}:d={FADE_S}", "-t", f"{total:.3f}",
           "-movflags", "+faststart", str(final))
    else:
        shutil.copy(silent, final)
    contact_sheet(final, marks, OUT / f"{args.cut}_contact_sheet.png")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    log(f"done: {final.relative_to(ROOT)} ({total:.1f}s), {silent.relative_to(ROOT)}, {srt.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
