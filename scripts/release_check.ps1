<#
.SYNOPSIS
  Release check (T13), Windows/PowerShell twin of scripts/release_check.sh.
.DESCRIPTION
  git clone (temp dir) -> venv -> pip install -r requirements-dev.txt -> pytest -q
  -> run.py data/dev --out decisions.jsonl -> validate.py PASS -> eval/evaluate.py summary
  -> root files present (LICENSE, README.md, .env.example, insights.md, decisions.jsonl), no .env committed.
  Exits non-zero on any failure. Full log: out\release_check.log in the source repo (override: $env:RELEASE_CHECK_LOG).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release_check.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release_check.ps1 -Holdout data\holdout -CopyBack
#>
param(
  [string]$Repo = "",          # path or URL to clone (default: this repo at its HEAD commit)
  [string]$Ref = "",           # branch/tag/commit to check out (default: HEAD of the source repo)
  [string]$Data = "data/dev",  # dataset for the deliverable run, relative to the clone
  [string]$Holdout = "",       # optional holdout dir (relative paths resolve against the source repo)
  [switch]$CopyBack,           # copy regenerated decisions*.jsonl (+ *_dq.json) into the source repo
  [string]$Python = "",        # python used to create the venv (default: py -3 / python / python3)
  [int]$MaxSeconds = 300,      # warn if the whole check takes longer
  [switch]$Keep                # keep the temp clone
)
$ErrorActionPreference = "Stop"
$Src = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Watch = [System.Diagnostics.Stopwatch]::StartNew()
$Warnings = New-Object System.Collections.Generic.List[string]

if (-not $Python) {
  foreach ($c in @("python", "python3", "py")) { if (Get-Command $c -ErrorAction SilentlyContinue) { $Python = $c; break } }
  if (-not $Python) { Write-Host "FAIL: no python found (use -Python)"; exit 1 }
}
if (-not $Repo) {
  $Repo = $Src
  if (-not $Ref) { $Ref = (& git -C $Src rev-parse HEAD).Trim() }
  if (& git -C $Src status --porcelain --untracked-files=no) {
    Write-Host "WARNING: uncommitted changes in $Src are NOT part of this check (it tests the committed $Ref)"
  }
}
if ($Holdout -and -not [System.IO.Path]::IsPathRooted($Holdout)) { $Holdout = Join-Path $Src $Holdout }

$Log = if ($env:RELEASE_CHECK_LOG) { $env:RELEASE_CHECK_LOG } else { Join-Path $Src "out/release_check.log" }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Log) | Out-Null
Set-Content -Path $Log -Value "" -Encoding utf8
$Work = Join-Path ([System.IO.Path]::GetTempPath()) ("plato40-release-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$Clone = Join-Path $Work "repo"
New-Item -ItemType Directory -Force -Path $Work | Out-Null

function Finish([int]$code) {
  if ($Keep) { Write-Host "kept clone: $Clone" } else { Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue }
  exit $code
}

# Step "title" { native command }  -> runs it, appends output to the log, fails on non-zero exit code.
function Step([string]$Title, [scriptblock]$Cmd, [string]$Tee = "") {
  $t = [System.Diagnostics.Stopwatch]::StartNew()
  Write-Host -NoNewline ("==> {0,-44} " -f $Title)
  Add-Content -Path $Log -Value "==> $Title"
  $global:LASTEXITCODE = 0
  $ok = $true
  try {
    $out = & $Cmd 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { $ok = $false }
  } catch { $out = @("$_"); $ok = $false }
  if ($out) { Add-Content -Path $Log -Value $out }
  if ($Tee) { Set-Content -Path $Tee -Value $out -Encoding utf8 }
  $s = [int]$t.Elapsed.TotalSeconds
  if ($ok) { Write-Host "ok ($s s)" } else {
    Write-Host "FAIL ($s s)"
    Write-Host "---- last 40 log lines ($Log) ----"
    Get-Content $Log -Tail 40 | ForEach-Object { Write-Host $_ }
    Write-Host "RELEASE CHECK: FAIL ($Title)"
    Finish 1
  }
}

Write-Host "release check: $Repo @ $(if ($Ref) { $Ref } else { 'default branch' }) -> $Clone"
Step "git clone" { git clone --quiet $Repo $Clone }
if ($Ref) { Step "git checkout $Ref" { git -C $Clone -c advice.detachedHead=false checkout --quiet $Ref } }
Set-Location $Clone
Write-Host "commit: $(git log -1 --format='%h %s')"

$pyVer = (& $Python --version 2>&1 | Out-String).Trim()
Step "venv ($pyVer)" { & $Python -m venv .venv }
$VPy = if (Test-Path ".venv/Scripts/python.exe") { Join-Path $Clone ".venv/Scripts/python.exe" } else { Join-Path $Clone ".venv/bin/python" }
Step "pip install -r requirements-dev.txt" { & $VPy -m pip install --disable-pip-version-check -q -r requirements-dev.txt }
Remove-Item Env:RELEASE_CHECK_E2E -ErrorAction SilentlyContinue   # no recursion into the opt-in e2e test
Step "pytest -q" { & $VPy -m pytest -q }

New-Item -ItemType Directory -Force -Path "out" | Out-Null
if (Test-Path "decisions.jsonl") { Copy-Item "decisions.jsonl" "out/committed_decisions.jsonl" }
$ValDev = Join-Path $Work "validate_dev.txt"
$EvalDev = Join-Path $Work "eval_dev.txt"
Step "run.py $Data --no-llm --out decisions.jsonl" { & $VPy run.py $Data --no-llm --out decisions.jsonl }
Step "validate.py decisions.jsonl $Data" { & $VPy validate.py decisions.jsonl $Data } $ValDev
if (-not (Select-String -Path $ValDev -Pattern '^RESULT: PASS' -Quiet)) { Write-Host "RELEASE CHECK: FAIL (validate did not print RESULT: PASS)"; Finish 1 }
Step "eval/evaluate.py decisions.jsonl" { & $VPy eval/evaluate.py decisions.jsonl --data $Data } $EvalDev

$ValHo = Join-Path $Work "validate_holdout.txt"
if ($Holdout) {
  if (-not (Test-Path $Holdout -PathType Container)) { Write-Host "RELEASE CHECK: FAIL (holdout dir not found: $Holdout)"; Finish 1 }
  Step "run.py holdout --no-llm --out decisions_holdout.jsonl" { & $VPy run.py $Holdout --no-llm --out decisions_holdout.jsonl }
  Step "validate.py decisions_holdout.jsonl" { & $VPy validate.py decisions_holdout.jsonl $Holdout } $ValHo
  if (-not (Select-String -Path $ValHo -Pattern '^RESULT: PASS' -Quiet)) { Write-Host "RELEASE CHECK: FAIL (holdout validate not PASS)"; Finish 1 }
}

Step "root deliverables + no .env committed" {
  $problems = @()
  foreach ($f in @("LICENSE", "README.md", ".env.example", "insights.md", "decisions.jsonl")) {
    if (-not (Test-Path $f) -or (Get-Item -Force $f).Length -eq 0) { $problems += "missing or empty at repo root: $f" }
    else { git ls-files --error-unmatch $f *> $null; if ($LASTEXITCODE -ne 0) { $problems += "not committed: $f" } }
  }
  $envs = git ls-files | Where-Object { $_ -match '(^|/)\.env(\..*)?$' -and $_ -notmatch '(^|/)\.env\.example$' }
  if ($envs) { $problems += "secret env file(s) committed: $($envs -join ', ')" }
  $problems
  $global:LASTEXITCODE = if ($problems.Count) { 1 } else { 0 }
}

if (-not (Select-String -Path "LICENSE" -Pattern 'MIT' -Quiet)) { $Warnings.Add("LICENSE does not mention MIT") }
if ((Test-Path "out/committed_decisions.jsonl") -and
    ((Get-FileHash "out/committed_decisions.jsonl").Hash -ne (Get-FileHash "decisions.jsonl").Hash)) {
  $Warnings.Add("committed root decisions.jsonl differs from a fresh run -> re-run with -CopyBack and commit it")
}
git rev-parse -q --verify refs/tags/pre-holdout *> $null
if ($LASTEXITCODE -ne 0) { $Warnings.Add("tag pre-holdout not found in the clone (create it before the holdout release, see docs/RELEASE.md)") }

Write-Host ""
Write-Host "---- validate ($Data) ----"
Select-String -Path $ValDev -Pattern '^(decisions:|VIOLATIONS|warnings|RESULT)' | ForEach-Object { Write-Host $_.Line }
Write-Host "---- eval summary ($Data) ----"
Select-String -Path $EvalDev -Pattern '^(Incidents detected|Episodes not matching)' | ForEach-Object { Write-Host $_.Line }
if ($Holdout) {
  Write-Host "---- validate (holdout) ----"
  Select-String -Path $ValHo -Pattern '^(decisions:|VIOLATIONS|warnings|RESULT)' | ForEach-Object { Write-Host $_.Line }
}

if ($CopyBack) {
  foreach ($f in @("decisions.jsonl", "decisions_dq.json", "decisions_holdout.jsonl", "decisions_holdout_dq.json")) {
    if (Test-Path $f) { Copy-Item $f (Join-Path $Src $f) -Force; Write-Host "copied $f -> $(Join-Path $Src $f)" }
  }
}

$Elapsed = [int]$Watch.Elapsed.TotalSeconds
if ($Elapsed -gt $MaxSeconds) { $Warnings.Add("took $Elapsed s > $MaxSeconds s budget") }
foreach ($w in $Warnings) { Write-Host "WARNING: $w" }
Write-Host "RELEASE CHECK: PASS in $Elapsed s (log: $Log)"
Set-Location $Src
Finish 0
