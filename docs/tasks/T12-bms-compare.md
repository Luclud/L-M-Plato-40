# T12 — BMS alarms vs our episodes + look-alike report

**Lane:** C presentation · **Priority:** P1 · **Depends on:** T01–T03 for meaningful numbers (can start on DQ) · **Score impact:** robustness (7), demo (8), insights C2

## Goal
Quantify where our agents beat the BMS: incidents we detect that the BMS missed or raised late, BMS alarms we judged false/benign,
alarm volume reduction (episodes vs raw alarms). Feeds the pitch, viewer and insights C2.

## Approach
`analysis/bms_compare.py <decisions.jsonl> <data_dir> --out out/bms_compare.json`: pair BMS raise/clear into alarm episodes per
(asset, code); match to our episodes by asset + time overlap; for labelled incidents (quickstart labels / dev weak labels) compare
first-detection times (ours vs BMS); summarise per alarm code: raised, matched, unmatched (likely false/benign), median lead time.

## Acceptance criteria
Output JSON + a markdown summary table (`out/bms_compare.md`) that the viewer and `insights.md` can embed; e.g. quickstart R104:
ours vs BMS 01:45Z vs work order 22:30Z.

## Files to touch
`analysis/bms_compare.py`, `tests/test_bms_compare.py` (small).
