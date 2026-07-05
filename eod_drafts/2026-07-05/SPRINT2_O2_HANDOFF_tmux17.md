# HANDOFF → tmux 17: SPRINT 2/O2 — budowa WĄSKIEJ REGUŁY O2 (kod za flagami OFF; zero flipów)

> Od: sesja konsolidacyjna 05.07 (po zamknięciu Twojego Sprintu 1 — dzięki niemu ten sprint jest odblokowany: VOID 0/4, czyste okna kalibracji OD **2026-07-03T13:19Z**).
> Kontekst: `KONSOLIDACJA_STANU_0507.md` + tracker + `dispatch_state/bundle_calib_review_verdict_2026-07-02.txt` + `eod_drafts/2026-07-02/O2_bramka_odczyt_raport.md` + `eod_drafts/2026-07-03/kind_review_o2_k2_prereq_raport.md`.

## Werdykt bramki (02.07, wiążący)
**GO na SPRINT silnika / NO-GO na flip surowego O2** — surowy O2 łamie carried-first (served łamie R6-od-gotowości w 36,1%). Wartość = **wąska reguła cap-Z=20**: policy-improved 7,9% (+214 worków), med ΔO2 +10,4 min, detour/regres pod capem ~0. Carried-first NIENARUSZALNY — nie cofaj inwersji P-1..P-7 bez ACK.

## Co JUŻ istnieje (nie buduj drugi raz — ETAP 0 zweryfikuj stan)
- O2 cap-Z Krok1+Krok2 SCALONE 02.07 (ledger §15): flagi `ENABLE_O2_CAPZ_RESEQ` (replay 3049: improved 7,3%/med 10,5 min/regres 0) + `ENABLE_SLA_GATE_READY_ANCHOR` (0 flipów werdyktu, 48% reason-churn). ⚠ OBA klucze NIE istnieją w flags.json (czytane defaultem OFF) — przy flipie FLIPMASTER je dopisze; TY ich nie dopisuj.
- Przegląd `_kind()` DONE 03.07 — realne prereqi flipu K2: **parytet best_effort picku (`dispatch_pipeline.py:6971`)** + **parytet plan_recheck compare-and-keep w replayu** + sekwencja PO ustabilizowaniu L3 (L3 ON od 04.07 — sprawdź 2 dni obserwacji `l3_regen_*`).
- S1 sla_anchor unified ON od 02.07 (`ENABLE_SLA_ANCHOR_UNIFIED`) — sprawdź, czy werdykt obserwacji S1 (~04.07) został skonsumowany; jeśli nie ma go w trackerze/shadow-jobs — zrób odczyt (to bramka flipu O2 K1).

## ZAKRES SPRINTU (wszystko za flagami OFF)
1. **ETAP 0** (protokół #0): stan na żywo, testy bazowe (baseline po S1 ~4204/0 — zweryfikuj), `atq` (nie ruszaj at-205/206/208 Pn), werdykt S1, stan flag; **oracle-test przyrządu bundle_calib** — at-168 w [[shadow-jobs-registry]] oznaczony UNTESTED: zanim zaufasz liczbom, wstrzyknij znany przypadek i sprawdź, że przyrząd go widzi.
2. **Parytety-prereqi K2**: dowód parytetu best_effort picku + plan_recheck compare-and-keep w replayu (testy z zębami/mutation-probe).
3. **Wąska reguła**: detour≤X ∧ carried≤Z (start od cap-Z=20 z werdyktu; X/Y skalibruj replayem na oknach OD 03.07T13:19Z — starsze rekordy mają dziury sprzed L1.1). **Trójka feasibility↔route_simulator↔plan_recheck RAZEM** + bliźniak selekcji `dispatch_pipeline`↔`objm_lexr6` RAZEM (mapa kompletności!). Jeśli cap-Z z 02.07 już realizuje regułę — nie dubluj, tylko dowiedź i skalibruj.
4. **Dowód**: replay ≥3 dni na czystych oknach — pozytywny wpływ (metryka docelowa lepsza ON↔OFF, nie tylko brak regresji) + wpływ na carried-first = 0 naruszeń; werdykt do `dispatch_state/o2_narrow_rule_replay_verdict.txt`.
5. **Wyjście**: rekomendacja sekwencji flipów K1→K2 dla sesji FLIPMASTER (tmux 19) w trackerze — sam NICZEGO nie flipuj.

## ⛔ ZASADY
Protokół #0 · worktree + commity po jawnych ścieżkach · NIE pushuj (master ahead, push za Adrianem) · ZERO flipów/restartów (flags.json = wyłączność sesji FLIPMASTER!) · replaye poza peakiem (Pn-Pt 11-14/17-20) · równolegle pracują: tmux 15 (courier_api/tests golden/monitor route-order/cod-weekly), tmux 18 (rejestr flag, tools), tmux 19 FLIPMASTER (flags.json+restarty za ACK) — nie dotykaj ich zakresów; working tree może mieć cudze modyfikacje (`common.py`, testy) — pracuj w świeżym worktree od HEAD · tracker+todo_master aktualizuj dopisując po każdym domknięciu.
