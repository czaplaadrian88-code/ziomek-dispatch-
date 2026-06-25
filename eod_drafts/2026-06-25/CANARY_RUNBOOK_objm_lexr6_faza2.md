# RUNBOOK — objm-lexr6 Faza 2 CANARY (live-flip `ENABLE_OBJM_LEXR6_SELECT`)

Status: **GO-READY, czeka na ACK Adriana do flipu.** Przygotowane 2026-06-25 (sesja CC).
Plan źródłowy: `eod_drafts/2026-06-17/CANARY_PLAN_objm_lexr6.md`. Pamięć: `objm-lexr6-validation-2026-06-24.md`.

## Co to zmienia (przypomnienie)
Po flipie selektor R6-primary (carry-aware) przesuwa na czoło grupy (tier×bucket) zwycięzcy kandydata
z najmniejszym carry-inclusive R6-breach — w ~12% decyzji. Cel: −615 min twardych naruszeń/tydz (walidacja
§6 PASS, re-potwierdzona 25.06 n=1845, regr 0,49%). Apply: `dispatch_pipeline.py:5709`. Selektor: moduł
`objm_lexr6.pick(..., bucket_fn=_selection_bucket)` (live=moduł, equal-treatment-aware → no_gps/pre_shift NIE re-demote).

## ✅ Pre-flip — ZROBIONE (ETAP 0-4 protokołu)
- [x] Bramki §6 PASS (24.06 n=1432 −533min; 25.06 re-replay n=1845 **−615min**, regr 0,49%).
- [x] Test parytetu modułu↔inline + klaster: **38 passed** (`test_objm_lexr6_{module,select_faza2,shadow,unify_2026_06_25}`).
- [x] P-4 equal-treatment w LEXR6: `bucket_fn=_selection_bucket` (no_gps+pre_shift→bucket 0). Demote-inwersja NIE odżyje.
- [x] Live=moduł (apply woła `_olx.pick`), nie inline → caveat parytetu zamknięty.
- [x] Reorder identity-safe (pop po id), fail-open do `feasible[0]`, PO tier-gate (nie łamie HARD/committed/KOORD gate).
- [x] Monitor read-only `tools/objm_lexr6_canary_monitor.py` (dry-run ✅). Timer `dispatch-objm-lexr6-canary-monitor.{service,timer}` zainstalowany **DISABLED**.

## ⛔ Pre-flip — DO POTWIERDZENIA PRZEZ ADRIANA (domena)
1. **Okno flipu = OFF-PEAK** (smoke). Peaki Białystok ~11-14 i ~17-20 Warsaw → flip np. 21-23 lub 9-10 Warsaw. NIE w peaku, NIE telegram-restart (flip = hot-reload flagi, BEZ restartu).
2. **Progi gate'ów** (propozycja z planu, env-overridable): KOORD +5pp / auto-route ACK+ALERT +8pp / reorder pas 5-25% / p95 +15%. Potwierdź lub skoryguj.
3. **Baseline z PEAKU**: gate'y G2a/G2b wymagają baseline z porównywalnego peaku (patrz krok B). G1 (błędy/latencja) i G2c (reorder%) działają bez baseline.

---

## SEKWENCJA (po ACK)

### A. Backup flag (zawsze najpierw)
```bash
cp /root/.openclaw/workspace/scripts/flags.json \
   /root/.openclaw/workspace/scripts/flags.json.bak-pre-objm-lexr6-flip-$(date -u +%Y%m%d)
```

### B. Baseline z peaku (PRZED flipem, w trakcie lunch/dinner peaku)
```bash
# uruchom NA PEAKU (np. 11:30 Warsaw) — okno 60 min, zapis baseline:
/root/.openclaw/venvs/dispatch/bin/python \
  /root/.openclaw/workspace/scripts/dispatch_v2/tools/objm_lexr6_canary_monitor.py \
  --save-baseline --window-min 60
# -> dispatch_state/objm_lexr6_canary_baseline.json
```

### C. FLIP off-peak (atomic, hot-reload, BEZ restartu) — SELECT=true I SHADOW=false RAZEM
```bash
/root/.openclaw/venvs/dispatch/bin/python - <<'PY'
import json,os,tempfile
p="/root/.openclaw/workspace/scripts/flags.json"
d=json.load(open(p))
d["ENABLE_OBJM_LEXR6_SELECT"]=True       # apply ON
d["ENABLE_OBJM_LEXR6_SELECT_SHADOW"]=False  # inaczej cień liczy się PO mutacji (zaślepia + double-compute)
fd,t=tempfile.mkstemp(dir=os.path.dirname(p))
open(fd,"w").write(json.dumps(d,indent=2,ensure_ascii=False))
os.replace(t,p)
print("FLIP OK: SELECT=True SHADOW=False")
PY
```
Smoke (~30-60 min): w `logs/dispatch.log` pojawia się `OBJM_LEXR6_SELECT order=… reorder→cid=…` (mechanizm żyje),
ZERO `OBJM_LEXR6_SELECT pick failed`, p95 ≤ baseline +15%.

### D. Włącz monitor na okno canary (co 10 min, STOP/WARN→Telegram)
```bash
systemctl enable --now dispatch-objm-lexr6-canary-monitor.timer
# podgląd: journalctl -u dispatch-objm-lexr6-canary-monitor.service -n 40 --no-pager
#          tail -40 /root/.openclaw/workspace/scripts/logs/objm_lexr6_canary_monitor.log
```

### E. Pełna regresja przy flipie (ETAP 4 — dowód braku regresji na żywym HEAD)
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q 2>&1 | tail -15
# baseline oczek.: ~2 failed pre-existing (sla_preexisting_bypass + parser_health), reszta pass
```

## ROLLBACK (każda faza, ~5 s)
```bash
/root/.openclaw/venvs/dispatch/bin/python - <<'PY'
import json,os,tempfile
p="/root/.openclaw/workspace/scripts/flags.json"
d=json.load(open(p)); d["ENABLE_OBJM_LEXR6_SELECT"]=False; d["ENABLE_OBJM_LEXR6_SELECT_SHADOW"]=True
fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); open(fd,"w").write(json.dumps(d,indent=2,ensure_ascii=False)); os.replace(t,p)
print("ROLLBACK OK: SELECT=False SHADOW=True")
PY
systemctl disable --now dispatch-objm-lexr6-canary-monitor.timer
```
(Albo przywróć `flags.json.bak-pre-objm-lexr6-flip-<data>`.)

## GATE'Y (monitor liczy automatycznie)
| Gate | STOP/rollback |
|---|---|
| G1 zdrowie | `pick failed` > 0 LUB p95 > baseline +15% |
| G2a KOORD | rate > baseline +5pp |
| G2b auto-route | ACK+ALERT > baseline +8pp (niższy score zwycięzcy → mniej AUTO — spodziewane, próg na nadmiar) |
| G2c reorder | < 5% lub > 25% (oczek. ~12%) |
| G3 (Faza 3, 2-3 dni) | real R6-breach na dotkniętych > baseline LUB override na flipach ↑ istotnie |

## FAZY (timeline)
- Faza 1 SMOKE off-peak (~30-60 min) → G1 czyste.
- Faza 2 CANARY 1 peak (lunch 11-14) → G1+G2 w normie.
- Faza 3 SUSTAIN 2-3 dni → outcome-join na ZASTOSOWANYCH pickach (`scratchpad/objm_lexr6_outcome_join.py`, już nie kontrfaktyczny).
- Faza 4 DECYZJA (ACK): ON na stałe / rollback. Cleanup: dokończ unify cienia→moduł, usuń martwy shadow-compute, zaktualizuj pamięć.
