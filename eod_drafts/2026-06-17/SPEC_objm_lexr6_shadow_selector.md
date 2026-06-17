# SPEC — Shadow-selektor D2 (objm R6-primary lexicographic). READ-ONLY spec, prod NIETKNIĘTY.

**Cel:** zwalidować na żywo (cień) selekcję „R6-primary lexicographic", którą replay-harness wskazał jako
**jedyny czysty zysk** (−108 min R6-breach, −134 min committed za +23 min idle, 9 regresji / 1465 decyzji),
ZANIM cokolwiek zmieni wybór. Faza 1 = **czysta telemetria, ZERO wpływu na selekcję**, za flagą default OFF.
Reużywa już-liczone `objm_*` (shadow ON od sprintu OBJ F0.3) — **zero nowych danych, zero nowego modelu**.

**Wzorzec do naśladowania (1:1):** istniejące bloki shadow w `dispatch_pipeline.py`:
`r6_danger_shadow` (l.5076-5111) i pln-objective shadow (l.5155-5197). Oba: re-rankują `feasible`
counterfactual-key przez `sorted()/min()` (NIE in-place), porównują ze zwycięzcą, piszą telemetrię. D2 jest
strukturalnie identyczny.

---

## 1. Zasada działania
W obrębie **tej samej grupy (tier × bucket) co żywy zwycięzca** `_winner = feasible[0]` wybierz kandydata o
**leksykograficznie najmniejszym** `(objm_r6_breach_max_min, late_pickup_committed_max, new_pickup_late_min)`.
Porównaj z `_winner`. Zaloguj rozjazd + 4 delty. **Nie zmieniaj `feasible`/`top`/`_winner` ani werdyktu.**

Grupa = ta sama (tier, bucket), bo żywy `_winner` wygrał swój tier+bucket; kandydat z gorszego tier/bucket i tak
nie może go bić, a z lepszego nie istnieje (inaczej byłby zwycięzcą). To dokładnie zakres, w którym dziś rozstrzyga
score — i w którym D2 by go zastąpił. (Wierność rekonstrukcji zmierzona harnessem: **96,9%**.)

## 2. Dokładny punkt wpięcia
`dispatch_pipeline.py`, **bezpośrednio po bloku pln-objective shadow (po ~l.5197), PRZED hookiem E2 (l.5206)**.
W tym miejscu: `feasible` jest po pełnym sorcie (score → `_demote_blind_empty` l.4989 → late-pickup tier gate
l.5003-5036), `_winner = feasible[0]` (l.5038), `_lp_tier` alias istnieje (l.5003), `objm_*` są już w `c.metrics`.

## 3. Flaga (hot-reload, default OFF)
- `common.py`: `ENABLE_OBJM_LEXR6_SELECT_SHADOW = False` (env-overridable, jak inne `*_SHADOW`).
- `flags.json`: `"ENABLE_OBJM_LEXR6_SELECT_SHADOW": false`.
- Czytane: `C.flag("ENABLE_OBJM_LEXR6_SELECT_SHADOW", False)` (hot-reload, bez restartu po flipie flagi —
  ale wdrożenie KODU wymaga restartu `dispatch-shadow`, patrz §9).
- Osobna flaga na PRZYSZŁY live-flip (Faza 2): `ENABLE_OBJM_LEXR6_SELECT` (default OFF) — patrz §7. NIE mieszać.

## 4. Co logować (prefix `objm_lexr6_` → AUTO-serializacja, ZERO zmian w shadow_dispatcher)
`_AUTO_PROP_PREFIXES` (shadow_dispatcher.py:190-192) zawiera już `"objm_"`; `_propagate_prefixed_metrics`
(l.244) skopiuje każdy klucz `c.metrics` zaczynający się `objm_` do zserializowanego `best`. Więc piszemy skalary
na `_winner.metrics["objm_lexr6_*"]` (jak pln pisze `pln_best_cid`/`pln_vs_score_flip` na `top[0].metrics`):
- `objm_lexr6_best_cid` (str) — kogo wskazałby D2
- `objm_lexr6_flip` (bool) — czy D2 ≠ żywy zwycięzca
- `objm_lexr6_group_size` (int) — wielkość grupy tier×bucket
- `objm_lexr6_d_r6_breach` / `_d_committed` / `_d_new_late` / `_d_idle` (float, tylko gdy flip) — delty
  (D2_pick − żywy): ujemne na R6/committed/new-late = poprawa; dodatnie idle = koszt.
Plus jedna linia `log.info("OBJM_LEXR6_DIVERGENCE order=... live=... d2=... dR6=... dCom=...")` (jak
`R6_DANGER_DIVERGENCE` l.5105). Trafią do `shadow_decisions.jsonl` → `best.objm_lexr6_*`.

## 5. Pseudokod (gotowy do wpięcia, ~28 linii; defensywny per Lekcja #83)
```python
# === D2 SHADOW: objm R6-primary lexicographic selector (OBSERVATIONAL) ===
# Wzór: r6_danger_shadow (l.5076). NIE mutuje feasible/top/_winner. objm_ prefix → auto-serialize.
if C.flag("ENABLE_OBJM_LEXR6_SELECT_SHADOW", False) and feasible:
    try:
        def _bucket(c):                      # identyczny jak _pln_pure_resort._bucket (l.907-912)
            if _is_informed_cand(c): return 0
            if _is_blind_empty_cand(c) or _is_pre_shift_cand(c): return 2
            return 1
        def _objm(c, k):
            v = (c.metrics or {}).get(k)
            return float(v) if isinstance(v, (int, float)) else None
        _w_tb = (_lp_tier(_winner), _bucket(_winner))
        _group = [c for c in feasible if (_lp_tier(c), _bucket(c)) == _w_tb]
        def _lex(c):                          # min: R6-breach → committed → new-late
            r6 = _objm(c, "objm_r6_breach_max_min")
            return (r6 if r6 is not None else 9e9,
                    _objm(c, "late_pickup_committed_max") or 0.0,
                    _objm(c, "new_pickup_late_min") or 0.0)
        _d2 = min(_group, key=_lex) if _group else _winner
        _wm = _winner.metrics
        if isinstance(_wm, dict):
            _flip = str(_d2.courier_id) != str(_winner.courier_id)
            _wm["objm_lexr6_best_cid"] = str(_d2.courier_id)
            _wm["objm_lexr6_flip"] = _flip
            _wm["objm_lexr6_group_size"] = len(_group)
            if _flip:
                _dm = _d2.metrics or {}
                _wm["objm_lexr6_d_r6_breach"] = round((_objm(_d2,"objm_r6_breach_max_min") or 0.0) - (_objm(_winner,"objm_r6_breach_max_min") or 0.0), 1)
                _wm["objm_lexr6_d_committed"] = round((_dm.get("late_pickup_committed_max") or 0.0) - (_wm.get("late_pickup_committed_max") or 0.0), 1)
                _wm["objm_lexr6_d_new_late"]  = round((_dm.get("new_pickup_late_min") or 0.0) - (_wm.get("new_pickup_late_min") or 0.0), 1)
                _wm["objm_lexr6_d_idle"]      = round((_dm.get("v3273_wait_courier_max_min") or 0.0) - (_wm.get("v3273_wait_courier_max_min") or 0.0), 1)
                log.info(f"OBJM_LEXR6_DIVERGENCE order={order_id} live={_winner.courier_id} d2={_d2.courier_id} dR6={_wm['objm_lexr6_d_r6_breach']} dCom={_wm['objm_lexr6_d_committed']} dIdle={_wm['objm_lexr6_d_idle']}")
    except Exception as _e:
        log.warning(f"OBJM_LEXR6_SHADOW failed order={order_id}: {_e!r}")  # fail-open, zero wpływu
```

## 6. Walidacja PRZED jakimkolwiek live-flipem (Faza 1 → bramka)
Narzędzie JUŻ ISTNIEJE: `eod_drafts/2026-06-17/replay_harness_p1.py` (polityka `D2_objm_lexR6`) — po N dniach
puść je na świeżym `shadow_decisions.jsonl` ALBO czytaj wprost nowe `objm_lexr6_*` (flip-rate + Σ delt).
**Bramki do flipa (wszystkie):**
1. **Net jakości:** Σ(`d_r6_breach` + `d_committed`) < 0 z marginesem (oczekiwane ≈ −240 min/tydzień) na ≥7 dni.
2. **Regresje:** liczba flipów pogarszających R6 lub committed >1 min < ~1% decyzji (harness: 9/1465 = 0,6%).
3. **Trade-off new-late zaakceptowany** przez Adriana (oczekiwane +~221 min/tydzień, ~+1,8 min/flip) —
   **+ drugorzędny test OUTCOME (kluczowy):** czy późniejszy odbiór NOWYCH zleceń nie psuje ICH własnego R6
   downstream. Join `objm_lexr6_*` (flip) ↔ `backfill_decisions_outcomes_v1.jsonl` po order_id → porównaj
   realne `pickup_to_delivery_min`/`assign_to_delivery_min` zleceń-z-flipem vs baseline. (Harness liczy
   decision-time; ta bramka domyka hindsight.)
4. **Brak nowych incydentów** dispatch-shadow 7 dni (NRestarts, błędy w logu).

## 7. Faza 2 — live-flip (OSOBNA flaga `ENABLE_OBJM_LEXR6_SELECT`, po przejściu §6, za ACK)
Minimalna zmiana: PO tier-gate sort (po l.5036), PRZED `_winner = feasible[0]` (l.5038), gdy flaga ON:
**reorder TYLKO w obrębie grupy (tier×bucket) zwycięzcy** — przesuń D2-pick na czoło swojej grupy:
```python
if C.flag("ENABLE_OBJM_LEXR6_SELECT", False) and feasible:
    _w0 = feasible[0]; _w_tb = (_lp_tier(_w0), _bucket(_w0))
    _grp = [c for c in feasible if (_lp_tier(c), _bucket(c)) == _w_tb]
    if _grp:
        _d2 = min(_grp, key=_lex)
        if _d2 is not feasible[0]:
            feasible.remove(_d2); feasible.insert(0, _d2)   # tylko w obrębie własnej grupy (czoło puli)
```
- Zachowuje bramkę tierów/committed (grupa = ten sam tier), feasibility, demote_blind_empty (ten sam bucket),
  MIN_PROPOSE/KOORD gate (działa dalej na `feasible[0].score`). Rollback = flip flagi OFF (hot-reload).
- **NIE wpinać przed tier-gate** (mogłoby przebić bramkę committed). **NIE dotykać** `wave_scoring.py`, wag
  `bonus_*`, `pln`, ani E2 hook.

## 8. Testy (mirror `tests/test_proposal_selection_v316.py` + istniejące `*_shadow`)
- `_lex` zwraca poprawną krotkę; sortuje R6→committed→new-late; `objm_*`=None → 9e9 (na koniec), brak crasha.
- **Grupa**: D2-pick NIGDY spoza (tier×bucket) zwycięzcy (fixture: kandydat o niskim R6 w gorszym tierze NIE wygrywa).
- **Shadow no-op**: po bloku `feasible`/`top`/`_winner.courier_id`/werdykt NIEZMIENIONE (assert identyczność listy id).
- **Flip detection**: fixture gdzie D2 ≠ score-winner → `objm_lexr6_flip=True` + delty policzone; gdzie D2==winner → flip=False, brak delt.
- **Auto-serialize**: po `_serialize_candidate`/`_serialize_result` `best["objm_lexr6_flip"]` obecne (prefix objm_).
- **Faza 2**: flip flagi `ENABLE_OBJM_LEXR6_SELECT` ON → `feasible[0]` = D2-pick, ale tier/bucket grupy zachowane; OFF → bez zmian. Regresja 47/47 selekcji + late-pickup tier + 25/25 v316 PASS.

## 9. Wdrożenie (workflow per CLAUDE.md — per-krok ACK)
draft → **ACK** → `cp .bak common.py dispatch_pipeline.py flags.json` → `str_replace` → `py_compile` →
import check → `pytest tests/test_proposal_selection_v316.py tests/test_late_pickup*` → commit + tag
`objm-lexr6-shadow-2026-06-XX` → **restart `dispatch-shadow` (NIE `dispatch-telegram` — Faza 1 nic nie wysyła do
operatora)** → verify: po 1 ticku `grep objm_lexr6 shadow_decisions.jsonl` (pojawiają się pola) + 0 `OBJM_LEXR6_SHADOW failed` → **stop for ACK**. Flaga zostaje OFF aż do osobnego ACK na flip (Faza 1 = sama obecność kodu liczy telemetrię? NIE — gate flagą; flip `ENABLE_OBJM_LEXR6_SELECT_SHADOW=true` hot-reload startuje zbieranie).

## 10. Landminy / ryzyka (z liczbami)
- **NIE mutować** `feasible`/`top` w Fazie 1 (`min()`/list-comp, nigdy `.sort()`) — inaczej cichy wpływ na selekcję.
- **objm_* bywa None** (stub/KOORD-minimal, ~6% rekordów) → `_lex` daje 9e9, blok fail-open (try/except) — zero crasha.
- **pln NIE jest wzorem na wagi** — harness: selekcja po `pln_v` psuje R6 o **+388 min**; D2 ≠ pln (D2 liczy CEL, pln płacę).
- **`+new_late` (+221 min/tydz.)** = jedyny realny koszt D2 — bramka §6.3 (outcome-check) MUSI go domknąć przed Fazą 2.
- **decision-time vs outcome**: Faza 1 mierzy predykcję `objm_*`; bramka §6.3 dodaje walidację hindsight.
- **Peak/restart**: restart `dispatch-shadow` poza peakiem; `dispatch-telegram` NIGDY bez ACK (CLAUDE.md).
- **Multi-session**: repo współdzielone — commit po ścieżkach, `pull --rebase` przed (feedback-multisession).
- **Zakres**: TYLKO 2 pliki kodu (`common.py` flaga + `dispatch_pipeline.py` blok) + `flags.json`. `wave_scoring.py`
  (martwy), `feasibility_v2.py` (liczy objm — NIE ruszać), shadow_dispatcher (prefix już jest) — BEZ ZMIAN.

## 11. Definition of Done (Faza 1)
Kod za flagą OFF wdrożony; po flipie `ENABLE_OBJM_LEXR6_SELECT_SHADOW=true` w `shadow_decisions.jsonl` pojawiają się
`best.objm_lexr6_{best_cid,flip,group_size,d_*}`; harness `D2_objm_lexR6` na żywych danych odtwarza profil
(−R6/−committed/+idle/+new-late); 0 błędów `OBJM_LEXR6_SHADOW failed`; selekcja/werdykt produkcyjny bez zmian
(flip-rate liczony, ale `feasible[0]` nietknięty). Flip live = osobny ACK po bramkach §6.
