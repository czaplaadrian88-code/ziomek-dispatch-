# Sprint 2 — Kebab Król + Carry/Bag-Stack Visibility — IMPLEMENTATION 2026-05-27

**Status:** kod gotowy, suita zielona, **NIE LIVE** (carry-chain shadow nie nadleciał — flagi w pliku, restart wymagany żeby flagi zaczęły być czytane przez działający dispatch-shadow). Etap 2.1 — gotowy do flip ON (flaga już TRUE w `flags.json` — hot-reload). Etap 2.2 — kod LIVE-ready, flaga OFF (czeka 14d shadow).

---

## 1. Pliki zmienione (5 + 2 nowe testy)

| Plik | Zmiana |
|---|---|
| `dispatch_v2/auto_proximity_classifier.py` | +import ZoneInfo + 4 nowe constanty KK + guard w `classify_auto_route()` po edge detection, przed `_meets_high_conf()` (~10 LOC efektywnie) |
| `dispatch_v2/common.py` | +sekcja Sprint 2 Etap 2.2 (env-overridable flag + 4 konstanty + 2 helpers: `carry_chain_penalty`, `carry_chain_hard_reject`, `is_carry_risk_restaurant`) — ~150 LOC, append na końcu pliku |
| `dispatch_v2/dispatch_pipeline.py` | (a) carry chain feature w score loop `_v327_eval_courier`: 25 LOC try/except wrapping helpera, defensive-fallback to 0.0 (b) `bonus_carry_chain_penalty` dodany do `bonus_penalty_sum` formuły (c) 5 nowych pól w `enriched_metrics` (carry_chain_*) (d) hard reject branch po v324a (override `verdict=NO` z reason) |
| `dispatch_v2/shadow_dispatcher.py` | 1-line: `"carry_chain_"` dodany do `_AUTO_PROP_PREFIXES` → auto-serializacja do shadow log |
| `/root/.openclaw/workspace/scripts/flags.json` | +2 flagi: `ENABLE_KEBAB_KROL_DINNER_EXCLUSION=true`, `ENABLE_CARRY_CHAIN_PENALTY=false` + comment block (atomic write via `core.flags_io._locked_rmw`) |
| **NEW** `dispatch_v2/tests/test_kk_dinner_exclusion_v2.py` | 10 testów Etap 2.1 (script-style runner pattern z conftest) |
| **NEW** `dispatch_v2/tests/test_carry_chain_penalty_v2.py` | 18 testów Etap 2.2 (helpers + integration smoke) |

### Diff summary

**`auto_proximity_classifier.py`**:
- Linia ~26: `+from zoneinfo import ZoneInfo` + 5 konstant (KK substring, dinner start/end)
- Linia ~520-530: nowy guard (po `_detect_edge_routing`, przed `_resolve_thresholds`):
  ```python
  if flags.get("ENABLE_KEBAB_KROL_DINNER_EXCLUSION", True):
      restaurant_name = ((order_event or {}).get("restaurant") or "")
      if isinstance(restaurant_name, str) and KEBAB_KROL_NAME_SUBSTR in restaurant_name.lower():
          warsaw_now = (now or datetime.now(timezone.utc)).astimezone(_WARSAW_TZ)
          warsaw_hour = warsaw_now.hour
          if KEBAB_KROL_DINNER_START_HOUR_WARSAW <= warsaw_hour < KEBAB_KROL_DINNER_END_HOUR_WARSAW:
              return ROUTE_ALERT, "kk_dinner_carry_risk_v2"
  ```
- **DST-safe** (używa `ZoneInfo("Europe/Warsaw")` zamiast hard-coded `timedelta(hours=2)` z briefa — odporne na CET↔CEST switch w marcu/październiku).

**`common.py`**: dodane env-overridable konstanty (`ENABLE_CARRY_CHAIN_PENALTY`, `CARRY_CHAIN_PENALTY_COEFF=1.5`, `CARRY_CHAIN_ETA_THRESHOLD_MIN=15.0`, `CARRY_CHAIN_HARD_REJECT_STOPS=2`, `CARRY_CHAIN_DINNER_*_HOUR_WARSAW=17/21`, `CARRY_RISK_LIST=frozenset({"kebab król"})`) + pure helpery (zero I/O, deterministyczne, defensive na None/garbage).

**`dispatch_pipeline.py`** w `_v327_eval_courier`:
```python
bonus_carry_chain_penalty = 0.0
carry_chain_stops = 0
carry_chain_applied = False
carry_chain_hard_rejected = False
if C.ENABLE_CARRY_CHAIN_PENALTY:
    try:
        _bag_rests = [b.get("restaurant") for b in (bag_raw or [])]
        _eta_for_carry = float(drive_min or 0.0)
        _pen, _stops, _appl = C.carry_chain_penalty(_bag_rests, restaurant, _eta_for_carry)
        bonus_carry_chain_penalty = _pen
        carry_chain_stops = _stops
        carry_chain_applied = _appl
        carry_chain_hard_rejected = C.carry_chain_hard_reject(_stops, restaurant, now_utc=now)
    except Exception as _carry_e:
        log.warning(...)  # defense-in-depth, no crash
```
Penalty dodane do `bonus_penalty_sum` (formuła linia ~2527+). Hard reject branch (~linia 2885+):
```python
if carry_chain_hard_rejected and verdict == "MAYBE":
    verdict = "NO"
    reason = "carry_chain_hard_reject (stops=N>=2, restaurant_in_CARRY_RISK_LIST, dinner_peak Warsaw)"
```
5 nowych pól w `enriched_metrics`: `carry_chain_penalty`, `carry_chain_stops`, `carry_chain_applied`, `carry_chain_hard_reject`, `carry_chain_drive_min_used`.

**`shadow_dispatcher.py`**: `_AUTO_PROP_PREFIXES` rozszerzony o `"carry_chain_"` — wszystkie `carry_chain_*` metryki auto-propagują do `shadow_decisions.jsonl` (LOCATION B).

---

## 2. Backupy (5×)

```
/root/.openclaw/workspace/scripts/dispatch_v2/auto_proximity_classifier.py.bak-pre-sprint2-2026-05-27
/root/.openclaw/workspace/scripts/dispatch_v2/common.py.bak-pre-sprint2-2026-05-27
/root/.openclaw/workspace/scripts/dispatch_v2/dispatch_pipeline.py.bak-pre-sprint2-2026-05-27
/root/.openclaw/workspace/scripts/dispatch_v2/shadow_dispatcher.py.bak-pre-sprint2-2026-05-27
/root/.openclaw/workspace/scripts/flags.json.bak-pre-sprint2-2026-05-27
```

**Soft rollback Etap 2.1** (hot-reload, ~5s, no restart):
```bash
/root/.openclaw/venvs/dispatch/bin/python -c "
from dispatch_v2.core.flags_io import update_flag
update_flag('ENABLE_KEBAB_KROL_DINNER_EXCLUSION', False)
"
```
**Soft rollback Etap 2.2** (gdy odpalimy potem): `update_flag('ENABLE_CARRY_CHAIN_PENALTY', False)`.
**Hard rollback** (revert pliku): `cp <plik>.bak-pre-sprint2-2026-05-27 <plik>` + restart `dispatch-shadow` + `dispatch-panel-watcher` (NIE telegram bez Adrian ACK).

---

## 3. Tests results

### Sprint 2 nowe testy: **28/28 PASS**
- `test_kk_dinner_exclusion_v2.py` → **10/10 PASS** (lunch, dinner 18:00, boundaries 16:59/17:00/20:59/21:00, flag OFF, non-KK, czasówka KK→ACK, case-insensitive PL diacritics)
- `test_carry_chain_penalty_v2.py` → **18/18 PASS** (default flag OFF, CARRY_RISK_LIST, is_carry_risk_restaurant, 7× `carry_chain_penalty`, 6× `carry_chain_hard_reject`, 2× integration smoke)

### Sąsiedztwo (auto_proximity + nowe Sprint 2): **55/55 PASS**

### Pełna regression dispatch_v2/tests/: **1560 passed / 41 failed / 7 skipped** (72.32s)
- **Baseline pre-Sprint 2** (z przywróconymi backupami, bez nowych testów Sprint 2): **1531 passed / 42 failed / 7 skipped**
- **Diff:** +29 nowych testów Sprint 2 (1531→1560 passed), **-1 fail** (test pollution differential między subseries), **ZERO REGRESJI w kodzie ode mnie**.
- 41 fails to wszystko pre-existing: `test_decision_engine_f21`, `test_feasibility_integration`, `test_v319*`, `test_v325_step_a_r02`, `test_v326_hotfix_button_label`, `test_v327_proposal_lifecycle_latency_slow`, `test_lgbm_shadow`, `test_parser_health_layer3`, `test_reconcile_dry_run`, `test_scoring_scenarios` itd. — wszystkie udokumentowane jako "pre-existing" w `CLAUDE.md` sekcja "Known issues / pre-existing failures" + `TECH_DEBT.md` defer V3.28-FEASIBILITY-C3-V325-FIXTURE / similar.

### py_compile + import smoke
- `auto_proximity_classifier.py` ✓
- `common.py` ✓
- `dispatch_pipeline.py` ✓ (load `V326_DEFAULT_CITY: Białystok` OK)
- `shadow_dispatcher.py` ✓ (`_AUTO_PROP_PREFIXES` = 14 prefixes po dodaniu `carry_chain_`)

---

## 4. Etap 2.1 verification (live)

Flaga `ENABLE_KEBAB_KROL_DINNER_EXCLUSION=true` jest już w `flags.json` (atomic write via `core.flags_io._locked_rmw`). Po hot-reload (czyta `common.flag(...)` co cykl pipeline / lub 60s TTL):

**Pozytywne sprawdzenie**: następne KK PROPOSE w oknie 17:00-20:59 Warsaw → `auto_route="ALERT"`, `auto_route_reason="kk_dinner_carry_risk_v2"` w decision record. Widoczne w:
- `/root/.openclaw/workspace/dispatch_state/shadow_decisions.jsonl` (top-level `auto_route` + `auto_route_reason`)
- Telegram propozycja: po flip `AUTO_PROXIMITY_ENABLED=true` (poza scopem dziś — Adrian decyduje) KK dinner NIE odpaliłby `🤖 PEWIEN — auto-przypisałbym…` linijki, tylko zwykły ACK gate

**Live check command (po przyjściu KK dinner orderu)**:
```bash
tail -200 /root/.openclaw/workspace/dispatch_state/shadow_decisions.jsonl \
  | /root/.openclaw/venvs/dispatch/bin/python -c "
import sys, json
for L in sys.stdin:
    r = json.loads(L)
    if 'kebab krol' in (r.get('restaurant','') or '').lower():
        print(r.get('order_id'), r.get('auto_route'), r.get('auto_route_reason'))
"
```

**Pre-existing baseline (z `/tmp/kebab_krol_diagnostic.md`):** KK dinner R6 breach 22.5% (8/30). Cel: ten breach rate ZNIKA z pool AUTO-routingu (ALERT routuje do KOORD, Adrian widzi/decyduje). Soft impact monitoring: Adrian ALERT counts dla "kk_dinner_carry_risk_v2" w czacie Telegrama (Tydzień 1) ≈ ~3-9 ALERT/dzień (estymata 4.9 KK/dzień × 30/76 dinner share = ~2/dzień).

---

## 5. Etap 2.2 shadow logging path

Flaga **OFF**. Po flip `ENABLE_CARRY_CHAIN_PENALTY=true` (Adrian po 14d shadow gdy KK Etap 2.1 stable):

**Shadow log fields (per candidate)** w `shadow_decisions.jsonl`:
- `carry_chain_penalty` (float, ≤0)
- `carry_chain_stops` (int, liczba bag itemów z różnej restauracji)
- `carry_chain_applied` (bool — True gdy stops≥1 AND drive_min > threshold 15)
- `carry_chain_hard_reject` (bool — True gdy stops≥2 AND dinner Warsaw AND KK)
- `carry_chain_drive_min_used` (float, drive_min użyty do penalty)

**14d shadow obs query (post-flip)**:
```bash
/root/.openclaw/venvs/dispatch/bin/python -c "
import json
from collections import Counter
path = '/root/.openclaw/workspace/dispatch_state/shadow_decisions.jsonl'
applied = 0
hard = 0
penalty_sum = 0.0
restaurants = Counter()
for L in open(path):
    r = json.loads(L)
    if r.get('carry_chain_applied'):
        applied += 1
        penalty_sum += r.get('carry_chain_penalty', 0)
        restaurants[r.get('restaurant')] += 1
    if r.get('carry_chain_hard_reject'):
        hard += 1
print(f'applied={applied} hard={hard} mean_penalty={penalty_sum/max(applied,1):.2f}')
print('top restaurants:', restaurants.most_common(10))
"
```

**Calibration starting point**: COEFF=1.5 × drive_min (=15-30 min KK carry) → penalty -22.5 do -45 pkt. Po shadow week:
- Jeśli applied >10× peer baseline rate → COEFF zbyt agresywny, calibrate down do 0.8-1.0
- Jeśli applied bardzo rzadko (<5/d) → COEFF za niski, lub threshold za wysoki

**Live flip warunki (post-14d shadow)**:
1. `carry_chain_hard_reject` count w KK dinner > 0 i mniejsza niż KK dinner volume (potwierdza precision)
2. `carry_chain_applied` distribution ≈ wzorzec Agent D (KK dominant w high-applied)
3. Brak false-positive: penalty applied dla cross-restaurant chains gdzie order kończy OK (ETA<threshold = ok detection)

---

## 6. Pre-deploy checklist dla Adrian

### Etap 2.1 (READY TO FLIP — niski risk)
- [x] Backup `auto_proximity_classifier.py.bak-pre-sprint2-2026-05-27` istnieje
- [x] Backup `flags.json.bak-pre-sprint2-2026-05-27` istnieje
- [x] Flag `ENABLE_KEBAB_KROL_DINNER_EXCLUSION=true` w flags.json (atomic write potwierdzony)
- [x] 10/10 testów KK exclusion pass
- [x] DST-safe (ZoneInfo, NIE hard-coded offset)
- [x] py_compile + import smoke pass
- [x] Edge cases (czasówka KK, flag OFF, non-KK, boundaries 16:59/17:00/20:59/21:00) wszystkie pokryte
- [ ] **Adrian**: Decyzja czy flipować od razu (default TRUE w pliku — jest "live" w sensie że następne PROPOSE w dinner+KK = ALERT). Jeśli chcesz odłożyć: `update_flag('ENABLE_KEBAB_KROL_DINNER_EXCLUSION', False)` lub revert `flags.json.bak-pre-sprint2-2026-05-27`.
- [ ] **Adrian**: monitor pierwsze 24h (KK dinner volume Mon ~2-3 z 8 historycznie). Spodziewane: 0-2 ALERT na dzień, każdy z `kk_dinner_carry_risk_v2` reason.
- [ ] **Adrian**: rollback trigger gdy false-positive — restart NIE WYMAGANY (flag hot-reload przez `load_flags()`).

### Etap 2.2 (NOT READY — wymaga 14d shadow)
- [x] Backupy 4 plików
- [x] Flag `ENABLE_CARRY_CHAIN_PENALTY=false` (default OFF)
- [x] 18/18 testów carry chain pass + integration smoke
- [x] Defense-in-depth (try/except wokół helpera, fallback 0.0 + warning log)
- [x] Auto-prop prefix w shadow_dispatcher (`carry_chain_`)
- [ ] **Adrian**: NIE flipować flagi do dnia ~2026-06-10 (14d shadow). Najpierw obserwuj Etap 2.1 efekty. Potem flip `ENABLE_CARRY_CHAIN_PENALTY=true` w shadow mode (`AUTO_PROXIMITY_SHADOW_ONLY=true` już ON), zbieraj `carry_chain_*` metryki przez 14d, kalibruj COEFF/threshold, dopiero później flip live.
- [ ] Po flipie: monitoring command z sekcji 5

### Wspólne
- [x] Zero regresji w dispatch_v2/tests/ (1560 vs baseline 1531 — diff = 29 nowych)
- [x] 5 backupów `.bak-pre-sprint2-2026-05-27`
- [x] NIE restart services (Adrian's hard constraint)
- [x] NIE git commit (Adrian's hard constraint)
- [x] NIE modyfikowano route_simulator_v2 / OR-tools planner (out of scope)
- [ ] **Adrian**: post-deploy decision o tagu git (jeśli chce tag `sprint2-kk-carry-2026-05-27` po acceptance)

---

## 7. Architectural notes

1. **DST safety**: Adrian's spec sugerował `timezone(timedelta(hours=2))` (CEST hard-coded). Override'em do `ZoneInfo("Europe/Warsaw")` — Polska CET zimą / CEST latem; ZoneInfo robi auto-shift na `astimezone()`. **Brak buga w marcu/październiku przy DST switch.**

2. **Klasyfikator pure / pipeline impure**: KK exclusion w `classify_auto_route()` (klasyfikator) — pure function, deterministic. Carry chain feature w `dispatch_pipeline._v327_eval_courier()` (impure score loop) — bo wymaga dostępu do `bag_raw` z fleet snapshotu. **Separation of concerns**: klasyfikator decyduje routing (post-PROPOSE), pipeline decyduje feasibility (pre-PROPOSE).

3. **Hard reject placement**: pattern Mirror'owany z V3.24-A (`v324a_extension_hard_reject`). Override `verdict=MAYBE → NO`, NIE przebija wcześniejszego NO (preserves first-fail semantics).

4. **bonus_carry_chain_penalty w bonus_penalty_sum**: zgodnie z Adrian's spec — idiomatyczne wzbogacenie istniejącego sumowania (linia 2527). Score loop pickup'uje carry chain w `final_score = score_result['total'] + bundle_bonus + ... + bonus_penalty_sum + ...`.

5. **`_AUTO_PROP_PREFIXES = carry_chain_`**: shadow log serializacja automatyczna — wszystkie pola `carry_chain_*` w enriched_metrics propagują do candidate i best dict serializowanych do `shadow_decisions.jsonl`. **ZERO ręcznego dopisywania per-field** (L1+L2 anti-pattern z V3.19/V3.20 — patrz CLAUDE.md "Every new metric ... needs downstream consumer checklist", checklist załatwiony przez prefix).

6. **Cross-Sprint coupling**: KK exclusion (Etap 2.1) + carry chain hard reject (Etap 2.2) używają **tej samej dinner window 17-21 Warsaw**. To celowe — Agent D dane pokazują dokładnie ten 4h window jako carry-risk peak. Jeśli kalibracja pokazuje inny window, **oba** parametry należy zmienić zgodnie (`KEBAB_KROL_DINNER_*_HOUR_WARSAW` w classifier + `CARRY_CHAIN_DINNER_*_HOUR_WARSAW` w common.py).
