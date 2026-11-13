# Data

PLATO-40 synthetic dataset, Proximata Hackathon '26 – Rhomberg track — licensed **CC BY 4.0**.
PLATO-40 is a fictional property; PLATO and CLARA are Rhomberg product names. No real data.

| Folder | Content |
|---|---|
| `quickstart/` | 21 days (Jan 2026), every scenario class at least once — develop here |
| `dev/` | 365 days (2026) — produce the root `decisions.jsonl` from this |
| `raw/PLATO40_v2_holdout_encrypted.7z` | holdout, password released Sat ~11:00 on Discord (never commit the password or decrypted copy unless allowed) |
| `raw/SHA256SUMS.txt` | organiser checksums of the original archives |

Each dataset folder contains the organiser `README.md` (column reference), `CHALLENGE.md`, `DECISION_CONTRACT.md`,
`manifest.json` and samples. **Never concatenate quickstart and dev** (independent runs). Our notes: `docs/DATA.md`.

Decrypt holdout: `7z x -p'<password>' data/raw/PLATO40_v2_holdout_encrypted.7z -odata/holdout`
