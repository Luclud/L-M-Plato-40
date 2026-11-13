#!/usr/bin/env bash
# Release check (T13): prove that a fresh clone works and produces the root deliverables.
#
#   scripts/release_check.sh [options]
#
# Steps: git clone (temp dir) -> venv -> pip install -r requirements-dev.txt -> pytest -q
#        -> run.py data/dev --out decisions.jsonl -> validate.py PASS -> eval/evaluate.py summary
#        -> root files present (LICENSE, README.md, .env.example, insights.md, decisions.jsonl), no .env committed.
# Exits non-zero on any failure. Full log: out/release_check.log in the source repo (override: RELEASE_CHECK_LOG).
#
# Options:
#   --repo PATH|URL   repository to clone (default: the repo containing this script, at its HEAD commit)
#   --ref REF         branch/tag/commit to check out (default: HEAD of the source repo)
#   --data DIR        dataset for the deliverable run, relative to the clone (default: data/dev)
#   --holdout DIR     also run + validate a holdout dataset (git-ignored, so give a path outside the clone,
#                     relative paths are resolved against the source repo) -> decisions_holdout.jsonl
#   --copy-back       copy the regenerated root decisions*.jsonl (+ *_dq.json) into the source repo
#   --python EXE      python used to create the venv (default: python3, else python)
#   --max-seconds N   warn if the whole check takes longer (default: 300)
#   --keep            keep the temp clone (path is printed)
#   -h, --help        show this help
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="" REF="" DATA="data/dev" HOLDOUT="" COPY_BACK=0 KEEP=0 MAX_SECONDS=300
PY="$(command -v python3 || command -v python || true)"

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:?--repo needs a value}"; shift 2 ;;
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --data) DATA="${2:?--data needs a value}"; shift 2 ;;
    --holdout) HOLDOUT="${2:?--holdout needs a value}"; shift 2 ;;
    --copy-back) COPY_BACK=1; shift ;;
    --python) PY="${2:?--python needs a value}"; shift 2 ;;
    --max-seconds) MAX_SECONDS="${2:?--max-seconds needs a value}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[[ -n "$PY" ]] || { echo "FAIL: no python3/python found (use --python)" >&2; exit 1; }

if [[ -z "$REPO" ]]; then
  REPO="$SRC"
  if [[ -z "$REF" ]]; then REF="$(git -C "$SRC" rev-parse HEAD)"; fi
  if [[ -n "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]]; then
    echo "WARNING: uncommitted changes in $SRC are NOT part of this check (it tests the committed $REF)"
  fi
fi
if [[ -n "$HOLDOUT" && "$HOLDOUT" != /* ]]; then HOLDOUT="$SRC/$HOLDOUT"; fi

LOG="${RELEASE_CHECK_LOG:-$SRC/out/release_check.log}"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/plato40-release.XXXXXX")"
CLONE="$WORK/repo"
cleanup() { if [[ $KEEP -eq 1 ]]; then echo "kept clone: $CLONE"; else rm -rf "$WORK"; fi; }
trap cleanup EXIT

T0=$SECONDS
WARNINGS=()
step() {  # step "title" cmd args...
  local title="$1"; shift
  local t=$SECONDS
  printf '==> %-44s ' "$title"
  echo "==> $title: $*" >> "$LOG"
  if "$@" >> "$LOG" 2>&1; then
    echo "ok ($((SECONDS - t)) s)"
  else
    echo "FAIL ($((SECONDS - t)) s)"
    echo "---- last 40 log lines ($LOG) ----"; tail -n 40 "$LOG"
    echo "RELEASE CHECK: FAIL ($title)"
    exit 1
  fi
}

echo "release check: $REPO @ ${REF:-default branch} -> $CLONE"
step "git clone" git clone --quiet "$REPO" "$CLONE"
if [[ -n "$REF" ]]; then step "git checkout $REF" git -C "$CLONE" -c advice.detachedHead=false checkout --quiet "$REF"; fi
cd "$CLONE"
echo "commit: $(git log -1 --format='%h %s')"

step "venv ($("$PY" --version 2>&1))" "$PY" -m venv .venv
if [[ -x .venv/bin/python ]]; then VPY="$CLONE/.venv/bin/python"; else VPY="$CLONE/.venv/Scripts/python.exe"; fi
step "pip install -r requirements-dev.txt" "$VPY" -m pip install --disable-pip-version-check -q -r requirements-dev.txt
step "pytest -q" env -u RELEASE_CHECK_E2E "$VPY" -m pytest -q   # no recursion into the opt-in e2e test

DEV_OUT="$CLONE/out/release_dev_eval.txt"
mkdir -p out
cp decisions.jsonl out/committed_decisions.jsonl 2>/dev/null || true
step "run.py $DATA --out decisions.jsonl" "$VPY" run.py "$DATA" --out decisions.jsonl
step "validate.py decisions.jsonl $DATA" bash -c '"$0" validate.py decisions.jsonl "$1" | tee "$2"' "$VPY" "$DATA" "$WORK/validate_dev.txt"
grep -q '^RESULT: PASS' "$WORK/validate_dev.txt" || { echo "RELEASE CHECK: FAIL (validate did not print RESULT: PASS)"; exit 1; }
step "eval/evaluate.py decisions.jsonl" bash -c '"$0" eval/evaluate.py decisions.jsonl --data "$1" > "$2"' "$VPY" "$DATA" "$DEV_OUT"

if [[ -n "$HOLDOUT" ]]; then
  [[ -d "$HOLDOUT" ]] || { echo "RELEASE CHECK: FAIL (holdout dir not found: $HOLDOUT)"; exit 1; }
  step "run.py holdout --out decisions_holdout.jsonl" "$VPY" run.py "$HOLDOUT" --out decisions_holdout.jsonl
  step "validate.py decisions_holdout.jsonl" bash -c '"$0" validate.py decisions_holdout.jsonl "$1" | tee "$2"' "$VPY" "$HOLDOUT" "$WORK/validate_holdout.txt"
  grep -q '^RESULT: PASS' "$WORK/validate_holdout.txt" || { echo "RELEASE CHECK: FAIL (holdout validate not PASS)"; exit 1; }
fi

check_root() {
  local missing=0 f
  for f in LICENSE README.md .env.example insights.md decisions.jsonl; do
    if [[ ! -s "$f" ]]; then echo "missing or empty at repo root: $f"; missing=1
    elif ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "not committed: $f"; missing=1; fi
  done
  local envs
  envs="$(git ls-files | grep -E '(^|/)\.env(\..*)?$' | grep -vE '(^|/)\.env\.example$' || true)"
  if [[ -n "$envs" ]]; then echo "secret env file(s) committed: $envs"; missing=1; fi
  return $missing
}
step "root deliverables + no .env committed" check_root

grep -q 'MIT' LICENSE || WARNINGS+=("LICENSE does not mention MIT")
if [[ -f out/committed_decisions.jsonl ]] && ! cmp -s out/committed_decisions.jsonl decisions.jsonl; then
  WARNINGS+=("committed root decisions.jsonl differs from a fresh run -> re-run with --copy-back and commit it")
fi
if ! git rev-parse -q --verify refs/tags/pre-holdout >/dev/null; then
  WARNINGS+=("tag pre-holdout not found in the clone (create it before the holdout release, see docs/RELEASE.md)")
fi

echo
echo "---- validate ($DATA) ----"
grep -E '^(decisions:|VIOLATIONS|warnings|RESULT)' "$WORK/validate_dev.txt"
echo "---- eval summary ($DATA) ----"
grep -E '^(Incidents detected|Episodes not matching)' "$DEV_OUT" || head -n 3 "$DEV_OUT"
if [[ -n "$HOLDOUT" ]]; then
  echo "---- validate (holdout) ----"
  grep -E '^(decisions:|VIOLATIONS|warnings|RESULT)' "$WORK/validate_holdout.txt"
fi

if [[ $COPY_BACK -eq 1 ]]; then
  for f in decisions.jsonl decisions_dq.json decisions_holdout.jsonl decisions_holdout_dq.json; do
    if [[ -f "$f" ]]; then cp "$f" "$SRC/$f"; echo "copied $f -> $SRC/$f"; fi
  done
fi

ELAPSED=$((SECONDS - T0))
if (( ELAPSED > MAX_SECONDS )); then WARNINGS+=("took ${ELAPSED} s > ${MAX_SECONDS} s budget"); fi
for w in "${WARNINGS[@]+"${WARNINGS[@]}"}"; do echo "WARNING: $w"; done
echo "RELEASE CHECK: PASS in ${ELAPSED} s (log: $LOG)"
