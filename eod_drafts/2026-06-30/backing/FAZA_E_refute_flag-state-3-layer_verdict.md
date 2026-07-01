# FAZA E — adwersarialna weryfikacja ROOT: flag-state-3-layer-no-single-source (R14)
# Lens B (refuter). Werdykt: CONFIRMED. is_source=true, is_open=true. Harm=LATENT/scoped (nie active).

## Metody niezależne (poza grepem z Fazy A-D):
1. /proc/<pid>/environ shadow(3403424) vs panel-watcher(3382392): REALNA divergencja env per-proces.
   - USE_V2_PARSER=1 tylko panel-watcher; shadow brak->default "0" (V1).
   - ENABLE_PANEL_BG_REFRESH=1 shadow / =0 panel-watcher.
   - plan_recheck writer-flagi (CARRIED_FIRST_RELAX/GPS_FREE_ANCHOR/RECANON_ON_WRITE/PLAN_CANON_ORDER_INVARIANTS/
     NO_RETURN_TO_DEPARTED_PICKUP/NONCARRIED_DROPOFF_REORDER/LEX_COMMITTED_WINDOW) =1 tylko panel-watcher.
2. flags.json (233 klucze): WSZYSTKIE divergentne flagi ABSENT -> env-only, nie hot-shared.
3. Oracle flag_fingerprint(): 63 (=59 ETAP4 + 4 extra). common.py ma 168 ENABLE_* bool-global; 107 poza fingerprintem.
4. Live fingerprint shadow.log vs plan_recheck.log: BYTE-IDENTYCZNE na 63 (decision_flag()->flags.json shared).
   -> monitorowana powierzchnia decyzyjna SPÓJNA; divergencja w CAŁOŚCI poza fingerprintem.
5. Osiągalność: dispatch_pipeline/route_simulator/shadow_dispatcher/feasibility NIE importują plan_recheck
   -> writer-flagi nie konsumowane w shadow (divergencja scoped-by-design). parse_panel_html w shadow tylko
   przez health_check() (diagnostyka), realny konsument panel_watcher.py:1543. PANEL_IS_FREE_AUTHORITATIVE
   i TRANSPARENCY_SCORING: 0 konsumentów (fresh grep) = martwe-ale-ON = inert. OR_TOOLS/GROUPING: nieoverride'owane
   w żadnym żywym procesie -> oba default "1" -> brak żywej divergencji (double-insert = latentny przy przyszłym mis-flipie).

## Wniosek: fakty roota wszystkie potwierdzone (3-warstwy, 63/168 fingerprint, env-frozen poza flags.json,
## 2 martwe flagi ON). NIE da się zrefutować source-ness ani openness. Lens-B kwalifikuje SEVERITY:
## active cross-proces decision-divergence = BRAK (63 identyczne, divergentne flagi scoped+spójne u konsumentów,
## martwe=0 konsumentów). Harm = latentna mina (mis-flip) + luka przyrządu (false-parity) + dług rejestru.
