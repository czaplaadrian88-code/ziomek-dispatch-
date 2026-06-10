# ETAP 2 — pewne poprawki scoringu (design, sesja CC 2026-06-10/11)

Spec: AUDIT_FIX_PLAN_2026-06-10.md ETAP 2. Findingi: memory ziomek-audit-2026-06-10 (Z-02/Z-09/Z-10/Z-11/Z-06).
Dyrektywa: feedback-always-propose-defer-pickup — bramki POPRAWIAJĄ propozycję, nie eskalują.

## Fix 1 — Z-02: sign-guard mnożnika Bug Z + rozdzielenie Unknown (dispatch_pipeline.py:3327 + common.py)

**Bug:** `final_score *= v327_bundle_score_mult` bez guardu znaku. Na UJEMNYM score ×0.1 = 10×
POPRAWA (np. −80 → −8) → najgorsze geometrycznie bundle wygrywają z lepszymi (−50 same-quadrant).
Dodatkowo `min_drop_proximity_factor` traktuje 'Unknown' jak realny cross-quadrant (0.0 → ×0.1)
— luka pokrycia districts karana jak twardy sygnał geometryczny.

**Fix (flaga `ENABLE_V327_MULT_SIGN_GUARD`, default ON, kill-switch env+flags.json hot-reload):**
1. Mnożnik aplikowany TYLKO gdy `final_score > 0`. Ujemny score → bez mnożnika
   (kary już działają; metryka `v327_mult_sign_guarded=True`).
2. Nowy helper `min_drop_proximity_factor_split(zones)` → `(min_factor_known, has_unknown)`:
   min po parach ZNANYCH stref; Unknown wykrywany osobno.
   - realny cross-quadrant wśród znanych → mult 0.1 (bez zmian),
   - 0.0 tylko-z-Unknown → mult `V327_BUNDLE_UNKNOWN_SCORE_MULT=0.7` (łagodny defensive),
   - mult finalny = min(mult_known, 0.7 jeśli Unknown obecny).
3. Obserwowalność: `v327_min_drop_factor_known`, `v327_unknown_zone_present`,
   `v327_mult_sign_guarded` w metrics (auto-prop po Fix 2).

Test kierunkowy: kandydat −80 cross-quadrant NIE pokonuje −50 same-quadrant (dziś: −8 > −50 wygrywa).

## Fix 2 — Z-09: serializacja (shadow_dispatcher.py)

- `_AUTO_PROP_PREFIXES += ("v327_", "late_pickup_", "new_pickup_")` — łańcuch mnożnika Bug Z
  (min_factor / mult / pre_mult / audit / sign_guarded) + wyrównanie late_pickup_*/new_pickup_*
  w best (LOCATION B dostaje je przez `_propagate_prefixed_metrics(out["best"], best_m)`).
- `pos_from_store` + `pos_age_min` explicit w LOCATION A (_serialize_candidate) i B (best inline);
  `pos_age_min` dodany do enriched_metrics w dispatch_pipeline (z cs).
Bez flagi — czysta obserwowalność.

## Fix 3 — Z-10: margin AUTO na finalnym rankingu (auto_proximity_classifier.py:321)

**Bug:** margin = top1−top2 po surowym score wśród feasible, a `result.best` wybierany jest PO
demote/tieringu (V3.16 demote blind-empty, late_pickup Opcja B, …) → margin potrafi opisywać
dwóch NIE-wybranych kandydatów; AUTO może odpalić na best który NIE jest score-topem.

**Fix (flaga `ENABLE_F7_MARGIN_FINAL_RANKING`, default ON via env, hot-reload via flags.json):**
- `margin = score(result.best) − max(score pozostałych feasible, po courier_id)`,
- nowe pole `ClassifierContext.best_is_score_top` (default True),
- `_meets_high_conf` C7: `not best_is_score_top` → ACK reason `best_not_score_top`.
AUTO_PROXIMITY jest live-OFF (shadow-only) → zmiana z natury shadow-first; prerequisite flipu Fazy 7.

## Fix 4 — Z-11: mass-fail heurystyka — bramka shift_end (dispatch_pipeline.py ~3751)

**Bug:** heurystyka V328 Fix 6 (mass-fail fallback) omija CAŁĄ feasibility — jedyny guard to
bag-cap. Kurier po końcu zmiany może wygrać w degraded mode (łamie R-SCHEDULE-AWARE/V325).

**Fix (flaga `ENABLE_V328_HEURISTIC_SHIFT_END_GUARD`, default ON — lustrzane do bezwarunkowego
bag-cap guard, kill-switch env+flags.json):** w pętli heurystyki skip kuriera gdy
`shift_end < now + naive_eta`, gdzie `naive_eta = dist_km(hav)/get_fallback_speed_kmh(now)*60`.
Brak shift_end → NIE skipuj (heurystyka działa w degraded mode; fail-open spójny z duchem FAIL-12).
Log `V328_HEURISTIC_SKIP_POST_SHIFT`.

## Fix 5 — Z-06 (część kodowa): semantyka pos_from_store w FAIL12 (feasibility_v2.py:561)

**Bug:** rescue z last-known-pos store (TTL 25 min) replay'uje pierwotny label `gps` →
przechodzi gate świeżego GPS w FAIL12 ("kurier FIZYCZNIE pracuje — świeży GPS TEN TICK").
Pozycja sprzed ≤25 min to nie jest dowód pracy w tym ticku.

**Fix (flaga `ENABLE_FAIL12_STOREPOS_STRICT`, default ON, kill-switch env+flags.json):**
- `check_feasibility_v2(..., pos_from_store: bool = False)` — nowy kwarg, przekazywany z cs
  w obu call-sites (główna pętla :2226 + solo fallback :4711),
- warunek FAIL12: `len(bag) > 0 or (pos_source == "gps" and not pos_from_store)`,
- gdy store-pos zablokował fail-open → metryka `fail12_storepos_blocked=True` (głośno).
Bag nadal wystarcza (twardy dowód niezależny od GPS). Wpływ live ograniczony do scenariusza
awarii grafiku × kurier ciemny >30 min × ostatni żywy GPS ≤25 min — bez baga.

## Walidacja
- Replay Z-02: rekonstrukcja stref z `bag_context.delivery_address` + decision.delivery_address
  (caveat: brak delivery_city w bag_context → default Białystok), odtworzenie pre_mult ze score/mult,
  re-rank feasible per decyzja, licznik flipów zwycięzcy + ręczny przegląd 5-10.
- Replay Z-10: czysta analiza logu — divergence best vs score-top + rozkład delty marginu.
- Z-11/Z-06: brak danych w logach (mass-fail rzadkie; pos_from_store nieserializowane do dziś)
  → testy jednostkowe + zliczenie LAST_KNOWN_POS_USED / V328_OR_TOOLS_MASS_FAIL z journala.
- Pytest: zero NOWYCH faili vs baseline (lista w /tmp/etap2_baseline_failed.txt).
