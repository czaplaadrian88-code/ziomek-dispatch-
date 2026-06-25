# Plan CANARY — objm-lexr6 Faza 2 (live-flip `ENABLE_OBJM_LEXR6_SELECT`)
Status: PLAN (do ACK Adriana). Sam flip NIE wykonany. Data: 2026-06-24.

## Założenia
- First-order zwalidowany 7d shadow (at#152 PASS: −533 min, regr 0,56%, n=1432, flip 12,2%). Canary mierzy
  efekty **drugiego rzędu** APLIKOWANIA picku: auto-route confidence, KOORD, override, latencja, R6 realne.
- Flip = `flags.json`: `ENABLE_OBJM_LEXR6_SELECT=true` **+ równocześnie** `ENABLE_OBJM_LEXR6_SELECT_SHADOW=false`
  (inaczej `_objm_lexr6_shadow` l.5690 liczy się PO mutacji d2_pick → zaślepia telemetrię + double-compute). Hot-reload, BEZ restartu.
- Typ: **czasowy** (smoke→1 peak→2-3 dni→decyzja), nie %-split (flaga boolean; shadow już pokrył 100% — split rozmyłby sygnał).

## Faza 0 — PRE-FLIP (gate G-PRE)
- [ ] Parzystość: `test_objm_lexr6_module.py` + `test_objm_lexr6_select_faza2.py` zielone (24.06: 21/21 ✅).
- [ ] Decyzja unifikacji: **Opcja A (rekom.)** oprzyj się na teście parzystości (moduł=inline), unifikację shadow→moduł jako cleanup po canary; **Opcja B** przepnij teraz + re-test (~30 min).
- [ ] Baseline: `objm_lexr6_canary_monitor.py --save-baseline` na 3 porównywalnych dniach peak → `dispatch_state/objm_lexr6_canary_baseline.json`.
- [ ] Backup: `cp flags.json flags.json.bak-pre-objm-lexr6-flip-<data>`; przećwicz rollback (flip→false, ~5 s).
- STOP jeśli: test parzystości czerwony / brak baseline.

## Faza 1 — SMOKE off-peak (~30-60 min, niski ruch)
Flip ON poza peakiem.
- GO: 0 `OBJM_LEXR6_SELECT pick failed`; widać `OBJM_LEXR6_SELECT order=… reorder→cid=` (mechanizm żyje); p95 ≤ baseline +10%.
- STOP/rollback: jakikolwiek `pick failed` / exception / p95 > baseline +15%.

## Faza 2 — CANARY 1 PEAK (jeden lunch-peak 11-14, ścisły nadzór) — G1+G2
| Gate | Metryka | Próg STOP (rollback) | Źródło |
|---|---|---|---|
| G1 zdrowie | błędy / latencja | `pick failed` > 0 LUB p95 > baseline +15% | logi + `latency_ms` |
| G2a KOORD | rate KOORD | wzrost > **5 pp** vs baseline | `verdict` w shadow_decisions |
| G2b auto-route | udział ACK+ALERT | wzrost > **8 pp** vs baseline (niższy score zwycięzcy → mniej AUTO) | `auto_route` |
| G2c reorder sanity | % decyzji z reorderem | < **5%** lub > **25%** (oczek. ~12%) | reorder log |

GO do Fazy 3 jeśli wszystkie w normie po pełnym peaku. **Progi pp = propozycja, Adrian potwierdza/koryguje (domena).**

## Faza 3 — SUSTAIN 2-3 dni — G3 (jakość, outcome-join)
Powtórz `objm_lexr6_outcome_join.py` na ZASTOSOWANYCH pickach (już NIE kontrfaktyczny — D2 wykonany → mierzy realny efekt).
- GO: real R6-breach na dotkniętych ≤ baseline (zero szkody) I committed-punktualność nie gorsza I override na flipach nie ↑ istotnie.
- STOP: real R6-breach ↑ vs baseline LUB override na flipach ↑ istotnie (koordynatorzy cofają D2 = wybór zły).

## Faza 4 — DECYZJA (G4, ACK Adriana)
- ON na stałe jeśli G3 czysty 2-3 dni. Rollback jeśli którykolwiek gate tripnął.
- Cleanup: dokończ unifikację shadow→moduł (jeśli Opcja A), usuń martwy shadow-compute, zaktualizuj komentarz flags.json + pamięć.

## Rollback (każda faza, ~5 s)
`ENABLE_OBJM_LEXR6_SELECT=false` w flags.json (hot-reload). Opcjonalnie `SHADOW=true` z powrotem.

## Monitor
`dispatch_v2/tools/objm_lexr6_canary_monitor.py` (read-only). Tryby: `--save-baseline` / domyślny (porównanie + gate'y) / `--window-min N` / `--notify`.
Liczy: KOORD%, auto-route AUTO/ACK/ALERT%, p50/p95 latencja, reorder%, błędy. Porównuje do baseline, zwraca per-gate GO/STOP/WARN.
Timer co 10 min w oknie canary (jak carried-first-monitor) — do dorobienia per ACK.

## Powiązane
- Walidacja §6: `validate_objm_lexr6.py` (at#152 PASS 24.06). Outcome-join: `OUTCOME_JOIN_2026-06-24.md`.
- Pamięć: `objm-lexr6-validation-2026-06-24.md`. Kod flip: `dispatch_pipeline.py:5488`. Moduł: `dispatch_v2/objm_lexr6.py`.
