# Sprint 1 — Drive_min Calibration v2 IMPLEMENTATION report (2026-05-27)

**Status:** CODE DEPLOYED to working tree, NIE restartowane services, NIE committowane.
Flag MAIN default OFF (shadow first 7d). Adrian review przed flip ON.

**Spec source:** `/tmp/drive_min_calibration_design.md` (Alt A — pos_source offset + floor guard).
**Empirical base:** `/tmp/drive_min_bias_report.txt` (n=3013, median |residual| 13.64 → 7.88 min target).

---

## 1. Pliki zmienione / dodane

| File | Type | Diff summary |
|---|---|---|
| `/root/.openclaw/workspace/scripts/dispatch_v2/drive_min_calibration.py` | **NEW** | Moduł kalibracji (~140 linii): `OFFSET_TABLE` (8 cells), `FLOOR_MIN=8.0`, `CALIBRATION_VERSION="v1_2026-05-27"`, `compute_pos_source_offset()`, `apply_calibration()` zwraca `(calibrated, debug_dict)`. Pure functions — no I/O. |
| `/root/.openclaw/workspace/scripts/dispatch_v2/auto_proximity_classifier.py` | MODIFIED | +3 helpery: `_peak_window_for(now)`, `_append_drive_min_calibration_shadow(entry)` (fail-safe JSONL append), `_maybe_apply_drive_min_calibration(metrics, cs, flags, now, order_id, courier_id, tier)`. Hook w `_build_context` po extract metrics. `_build_context` & `build_context_for_logging` przyjmują `now`. `classify_auto_route` przekazuje `now`. |
| `/root/.openclaw/workspace/scripts/flags.json` | MODIFIED | +2 flagi: `ENABLE_DRIVE_MIN_CALIBRATION_V2=false` (main path, default OFF), `ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW=true` (shadow log zawsze ON). |
| `/root/.openclaw/workspace/scripts/dispatch_v2/tests/test_drive_min_calibration_v2.py` | **NEW** | 26 pytest tests (~290 linii). |

**Wynik decyzji `common.py` (design §3.1):** NIE modyfikowany. Kalibracja działa na `metrics["drive_min"]` w klasyfikatorze AUTO/ACK/ALERT — to single entry-point gdzie raw drive_min staje się decyzją routingu. Solver/scoring (`dispatch_pipeline.py:2617`) emituje raw drive_min do metrics; klasyfikator nadpisuje przed C1-C6 gating + edge detection. Per design §5.6, raw value przeżywa w `drive_min_raw` w enriched metrics dla backwards-compat datasetu LGBM v1.1.

## 2. Backupy (24h retention, naming convention pre-drive-calib-2026-05-27)

```
/root/.openclaw/workspace/scripts/dispatch_v2/auto_proximity_classifier.py.bak-pre-drive-calib-2026-05-27
/root/.openclaw/workspace/scripts/flags.json.bak-pre-drive-calib-2026-05-27
```

(drive_min_calibration.py = nowy plik, backup N/A.)

## 3. Tests result

```
$ /root/.openclaw/venvs/dispatch/bin/python -m pytest dispatch_v2/tests/test_drive_min_calibration_v2.py -v
============================== 26 passed in 0.37s ==============================
```

**Coverage:**

- 10 testów `compute_pos_source_offset` (każda komórka OFFSET_TABLE + 2 edge: unknown enum, None).
- 7 testów `apply_calibration` (basic, floor-hit-low-raw-no-offset, floor-NOT-hit-when-offset-lifts, high-raw-high-offset, None raw → defensive 0, empty ctx, peak_window forward-compat).
- 6 testów integracji `_maybe_apply_drive_min_calibration` (flag OFF noop, flag OFF SHADOW ON logs, flag ON substitutes, floor hit logs floor_applied=True, no drive_min propagates, pos_source fallback z courier_state).
- 3 testy regresji empirycznej (OFFSET_TABLE matches report, FLOOR_MIN=8, VERSION tag).

**Brak regresji:** `pytest dispatch_v2/tests/test_auto_proximity_classifier.py -v` → **21/21 PASSED** (existing classifier suite, no breakage).

## 4. Offset table — empirical values per pos_source

(median Δ z `/tmp/drive_min_bias_report.txt` §1.2, n=3013, aplikowane jako additive offset)

| pos_source              | n      | offset_min | uzasadnienie |
|-------------------------|--------|------------|----------------------------------------------------|
| `no_gps`                | 1797   | **+6.5**   | Synthetic BIALYSTOK_CENTER + max(15,prep) buforuje. Najlepszy source |
| `pre_shift`             | 455    | +15.3      | Kurier nie zaczął, eta synthetic |
| `gps`                   | 41     | +35.1      | Parked-fresh GPS slip-through (pos_age ≤5 ale w rzeczywistości stary) |
| `last_assigned_pickup`  | 317    | +30.9      | Pozycja = przeszły pickup, kurier pojechał dalej |
| `last_picked_up_pickup` | 194    | +34.7      | Jak wyżej + bag carry. Worst stale-pos source |
| `last_picked_up_delivery` | 16   | +30.5      | Mała próbka — ostrożny offset |
| `post_wave`             | 193    | +30.9      | Pozycja po komicie fali — pozycja przeszłości |
| `last_picked_up_interp` | 0      | +10.0      | **Placeholder** dla F4-K2 LIVE. Re-calibrate po 30d shadow |

**FLOOR_MIN = 8.0 min** — physical floor: parking + entry + DWELL + handover. Step 1.10 pokazał constant +33 min bias dla bucket ≤5 (99.8% under-est).

## 5. Shadow logging

**Path:** `/root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl`
**Override (tests / replay):** env `DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH`.
**Sample entry:**

```json
{"ts":"2026-05-28T12:34:56+00:00","order_id":"469112","courier_id":"123","pos_source":"last_picked_up_pickup","tier":"std+","peak_window":true,"raw_drive_min":18.4,"offset_applied":34.7,"calibrated_drive_min":53.1,"floor_applied":false,"calibration_version":"v1_2026-05-27","main_path_active":false}
```

**Trwałość:** append-only JSONL, fail-safe — wyjątek I/O nie blokuje classifier (lekcja #149: nigdy nie blokuj dispatchu na log). Plan logrotate weekly (design §5.5; do uruchomienia po flip).

## 6. Verification — local-test bez restart

Adrian może odpalić shadow tryb local-test bez restartu produkcji:

```bash
# A. Verify import + constants (zero side-effect, NIE pobiera live data)
/root/.openclaw/venvs/dispatch/bin/python -c "
from dispatch_v2 import drive_min_calibration as dmc
print('OFFSET_TABLE:', dmc.OFFSET_TABLE)
print('FLOOR_MIN:', dmc.FLOOR_MIN)
cal, dbg = dmc.apply_calibration(18.0, {'pos_source':'last_picked_up_pickup','tier':'std+'})
print('Sample: raw=18.0, last_picked_up_pickup →', cal, dbg)
"

# B. Verify full suite z subset isolation
/root/.openclaw/venvs/dispatch/bin/python -m pytest dispatch_v2/tests/test_drive_min_calibration_v2.py dispatch_v2/tests/test_auto_proximity_classifier.py -v

# C. Replay smoke (zero production write) — verify shadow log writes do tmp
TMPDIR=$(mktemp -d)
DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH=$TMPDIR/sim.jsonl /root/.openclaw/venvs/dispatch/bin/python -c "
import os
from dispatch_v2 import auto_proximity_classifier as apc
class FakeCS:
    def __init__(self):
        self.pos_source='last_picked_up_pickup'; self.tier_bag='std+'
out = apc._maybe_apply_drive_min_calibration(
    metrics={'drive_min':18.0,'pos_source':'last_picked_up_pickup'},
    cs=FakeCS(),
    flags={'ENABLE_DRIVE_MIN_CALIBRATION_V2':False,'ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW':True},
    now=None, order_id='SIM-1', courier_id='C-SIM', tier='std+',
)
print('Output drive_min (main NOT swapped):', out['drive_min'])
print('Output drive_min_calibrated:', out['drive_min_calibrated'])
"
echo '=== Shadow log entry ==='
cat $TMPDIR/sim.jsonl
rm -rf $TMPDIR
```

Pozwala potwierdzić: A) constants OK, B) tests green, C) shadow log structure poprawne, BEZ żadnego touch na `/dispatch_state/*` produkcji.

## 7. Rollback procedure

**A. Soft (hot-reload, ~5s, zero restart):**

```bash
python3 -c "
import json, tempfile, os
p='/root/.openclaw/workspace/scripts/flags.json'
d=json.load(open(p))
d['ENABLE_DRIVE_MIN_CALIBRATION_V2']=False
fd,t=tempfile.mkstemp(dir=os.path.dirname(p))
open(fd,'w').write(json.dumps(d, indent=2, ensure_ascii=False))
os.replace(t,p)
"
```

(`ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW` zostaje True — shadow log nadal pisany dla post-mortem.)

**B. Full revert do stanu sprzed sprintu (restore .bak):**

```bash
cp /root/.openclaw/workspace/scripts/dispatch_v2/auto_proximity_classifier.py.bak-pre-drive-calib-2026-05-27 \
   /root/.openclaw/workspace/scripts/dispatch_v2/auto_proximity_classifier.py
cp /root/.openclaw/workspace/scripts/flags.json.bak-pre-drive-calib-2026-05-27 \
   /root/.openclaw/workspace/scripts/flags.json
rm /root/.openclaw/workspace/scripts/dispatch_v2/drive_min_calibration.py
# Restart only if classifier code path actively used live:
# sudo systemctl restart dispatch-shadow dispatch-panel-watcher
# (dispatch-telegram WYMAGA Adrian ACK)
```

## 8. Pre-deploy checklist dla Adrian (pre-flip MAIN ON)

Przed `ENABLE_DRIVE_MIN_CALIBRATION_V2=true` (po ≥7d shadow):

1. **Shadow KPI verify** — `drive_min_calibration_log_v2.jsonl` ma >150 entries/dzień przez 5+ dni, median(|calibrated-actual|) stabilnie <8 min (target backfill 7.88).
2. **Per-pos_source residual** — sprawdź czy żaden mapped pos_source nie ma residual >12 min dla n≥50 (design §4.3 alarm trigger).
3. **Floor hit rate** — `<20%` (jeśli więcej → podnieść FLOOR_MIN albo zbadać chain_eta).
4. **R6 cascade obs** — design §5.2 hipoteza: KOORD trigger rate dla `last_picked_up_pickup` wzrośnie 5-10pp. Verify żaden throughput cliff (R6_HARD_BAG_MIN=35 może wymagać tymczasowego bumpu do 38).
5. **Replay tools backward-compat** — `sequential_replay.py --calibration-version=raw` zachowuje stare wartości; nowe analizy używają `v1`. Jeśli nie ma flagi w replay tools, BEFORE flip dorobić (Sprint follow-up).

**Po flip:**
- Telegram digest watch 24h post-flip — top1 divergence rate <25% (design §5.1).
- Override rate trend (operator zmienia kuriera) — expect spadek 5-10pp w tygodniu.

---

## Lekcje (post-sprint, do `lessons.md` candidate)

- **#150** (lessons.md candidate): Multi-feature offset tables NIE są lepsze niż single-feature gdy R² < 0.1. Empirical evaluation (Step 1.11 tabela) MUSI poprzedzać feature engineering — fragmentacja zjada zysk.
- **#151** (feedback_rules.md candidate): Shadow log z fail-safe try/except w hot path (lekcja #75 + #149) — log error MUSI być swallowed, nigdy nie blokuje routingu.
- Application of lekcja #144 (scoping metryk): `drive_min_raw` zostaje w enriched metrics, `drive_min` to teraz "active value" — downstream consumer może wybrać który czyta (LGBM v1.1 → raw, telegram display → calibrated po flip).

---

**END OF REPORT**
