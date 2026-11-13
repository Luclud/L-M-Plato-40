"""T13: release check scripts (fast checks; full run is opt-in via RELEASE_CHECK_E2E=1)."""
import os
import shutil
import subprocess

import pytest

from conftest import ROOT

SH = ROOT / "scripts" / "release_check.sh"
PS1 = ROOT / "scripts" / "release_check.ps1"
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


def _sh(*args, timeout=60, log=None):
    env = dict(os.environ, RELEASE_CHECK_LOG=str(log)) if log else None
    return subprocess.run([BASH, str(SH), *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env)


def test_scripts_and_runbook_exist():
    assert SH.exists() and PS1.exists()
    runbook = (ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8")
    for must in ("release_check", "pre-holdout", "decisions_holdout.jsonl", "Changes after pre-holdout"):
        assert must in runbook
    assert "Changes after pre-holdout" in (ROOT / "README.md").read_text(encoding="utf-8")


@needs_bash
def test_sh_syntax_and_help():
    assert subprocess.run([BASH, "-n", str(SH)], capture_output=True).returncode == 0
    r = _sh("--help")
    assert r.returncode == 0 and "--holdout" in r.stdout and "--copy-back" in r.stdout


@needs_bash
def test_sh_unknown_option_fails():
    assert _sh("--bogus").returncode != 0


@needs_bash
def test_sh_fails_on_bad_repo(tmp_path):
    r = _sh("--repo", str(tmp_path / "does-not-exist"), log=tmp_path / "release_check.log")
    assert r.returncode != 0
    assert "RELEASE CHECK: FAIL (git clone)" in r.stdout


def test_root_deliverables_committed():
    """What release_check verifies at the root (fast pre-check, without a clone)."""
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    for f in ("LICENSE", "README.md", ".env.example", "insights.md", "decisions.jsonl"):
        assert f in tracked, f"{f} not committed at repo root"
    assert not [f for f in tracked if os.path.basename(f).startswith(".env") and not f.endswith(".env.example")]


@pytest.mark.skipif(PWSH is None, reason="pwsh not available")
def test_ps1_parses():
    cmd = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
           f"'{PS1}', [ref]$null, [ref]$e) | Out-Null; if ($e) {{ $e; exit 1 }}")
    r = subprocess.run([PWSH, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_bash
@pytest.mark.skipif(os.environ.get("RELEASE_CHECK_E2E") != "1", reason="set RELEASE_CHECK_E2E=1 (clones + pip install)")
def test_sh_full_release_check(tmp_path):
    r = _sh(timeout=600, log=tmp_path / "release_check.log")
    assert r.returncode == 0, r.stdout[-3000:]
    assert "RELEASE CHECK: PASS" in r.stdout
