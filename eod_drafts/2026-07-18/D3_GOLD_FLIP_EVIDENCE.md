# Sprint D3-gold (2026-07-18) — EVIDENCE: flip OFF `ENABLE_ETA_QUANTILE_R6_BAGCAP`

GO Adriana: „Dawaj D3-gold". Podstawa: werdykt D3 (29.06, KANON §9: „BEZ WYJĄTKÓW: 35 dla
KAŻDEGO; USUŃ recovery gold≤4") + OD-07 OWNER_CONFIRMED (12.07: R6 nigdy klasa kuriera).
Protokół #0; mapa = skill scope (klasy: r6-progi + nowa-flaga/flip + artefakt-werdykt).

## ETAP 0-3 — stan + mapa

- Flaga PRZED: flags.json **True** (LIVE od 14.06, „GOLD->4 LIVE GATE", replay ówczesny CI[−0.63,+1.25] 14:1 — słaby dowód, nadpisany werdyktem właściciela); const common **False**; w ETAP4 ✓; ZERO env-carrierów w /etc ✓; ZERO konsumentów cross-repo.
- Odczyt: **`C.flag(...)` w OBU gałęziach → HOT-RELOAD** — flip bez restartów, bez okna blackoutu.
- Mapa miejsc: gałąź główna `feasibility_v2:~1141` (gold, bag+1≤4 → `_gate_bt`=p80; metryka `r6_gold4_gate_recovered` auto-serializowana) + gałąź co-design `:~1254` (aktywna TYLKO przy `ENABLE_SLA_GATE_READY_ANCHOR` — BRAK w flags.json → default False → DZIŚ UŚPIONA; jedna flaga gasi OBIE = bliźniaki razem z konstrukcji).
- N-D: `ENABLE_ETA_QUANTILE_SHADOW` (dispatch_pipeline :4563/:4577) — INNA flaga: telemetria kwantyli w cieniu, NIE recovery — NIE dotykana. `ENABLE_PACZKA_R6_THERMAL_EXEMPT` — wyjątek dla PACZEK zachowany ŚWIADOMIE (KANON §3: „Paczki: wcześniejsze R6 exempt zachowane"). Kod gałęzi NIE usuwany w tym sprincie (rollback-first; usunięcie = cleanup po oknie, wpis w backlogu).

## ETAP 4 — dowody mechaniczne

- **Testy gałęzi (NOWE — luka od 14.06, brak było ON≠OFF):** `tests/test_d3_gold_quantile_flip.py` 4/4 —
  ON odzyskuje (metryka, zero violation) / **OFF = surowa bramka 35 (violation, zero metryki) = stan po flipie** / ON+std NIGDY nie odzyskuje (strażnik „wyłącznie gold" pod przyszłe usunięcie kodu) / const==stan-po-flipie (koniec miny const≠json).
- Sąsiedzi: test_o2_capz_reseq + test_sla_anchor_unified — zielone.
- regresja: patrz „Wyniki końcowe".

## ETAP 5 — wpływ (pomiar ŻYWY zamiast syntetycznego replayu)

- **Skala odzysków LIVE (shadow_decisions, bieżące okno ~3 dni):** 39 decyzji z metryką
  (16.07: 8, 17.07: 25, 18.07: 6 ≈ **13/dzień**) — ale **odzysk NA ZWYCIĘZCY: 0/39**
  (wszystkie 39 = PROPOSE z innym zwycięzcą; odzyskany gold ani razu nie wygrał).
- **Wniosek:** flip OFF na bieżącym korpusie **nie zmienia ŻADNEGO wyniku decyzji**
  (kandydat, który traci feasibility, i tak przegrywał) — zero-regresyjny w danych,
  a POZYTYW = zgodność z kanonem właściciela (OD-07/D3: żaden kurier nie ma R6>35 poza
  Alarmem). world_replay nie ma flag-override — pomiar live 0/39 + testy gałęzi go zastępują.
- **Okno 2 dni (at#217, pon. 20.07, wspólne z B2):** sekcja D3 w `b2_window_verdict.sh` —
  (a) ZERO nowych `r6_gold4_gate_recovered` po fladze OFF (live ON≠OFF), (b) sanity KOORD/
  best_effort rate bez skoku. Peak sobotni 16-21 dziś da wolumen tuż po flipie.

## ETAP 6-7 — deploy + rollback

- Deploy = **sam flip flags.json → false** (hot ≤60 s wszystkie procesy; ZERO zmian .py
  w silniku, ZERO restartów, blackout nieistotny). Commit: tylko testy+docs+rejestr.
- Rollback: flags.json → true (hot). Kod gałęzi nietknięty = pełna odwracalność.

## DoD — tokeny mechaniczne

regresja: patrz „Wyniki końcowe" (finalna suita na zamrożonym stanie)
e2e: pomiar ŻYWY na kanonicznym logu decyzji (shadow_decisions.jsonl, pełna fasada silnika w produkcji): 39 decyzji z odzyskiem w 3 dni, odzysk na ZWYCIĘZCY 0/39 → flip nie zmienia wyników; brak zmian .py w silniku (flip flags.json), warstwy render/plan niedotknięte
replay: ON↔OFF w testach gałęzi (4/4, w tym OFF=stan-po-flipie i strażnik „tylko gold"); syntetyczny replay całej fasady zbędny — world_replay nie ma flag-override, a live 0/39 na zwycięzcy jest mocniejszym dowodem braku wpływu na wyniki; pozytyw = zgodność z OD-07/D3 (kanon właściciela)
rollback: flags.json → true (hot-reload C.flag, zero restartów); kod gałęzi NIETKNIĘTY = pełna odwracalność; lifecycle registry `deprecated` z notą

N-D: dispatch_pipeline.py — czyta TYLKO ENABLE_ETA_QUANTILE_SHADOW (telemetria, inna flaga) — niedotykana
N-D: route_simulator_v2.py — nie czyta flagi (grep 0); symulacja bez zmian
N-D: plan_recheck.py — nie czyta flagi (grep 0)
N-D: sla_anchor.py — nie czyta flagi (grep 0); gałąź co-design w feasibility gaśnie tą SAMĄ flagą (bliźniaki razem z konstrukcji)

## Wyniki końcowe

- **Baseline PRZED flipem: 5166 passed / 0 failed** (27 skip — zegarowe self-skipy ±3, wg AGENTS.md oceniamy listą faili; 8 xfail). Testy D3 powstały PO kolekcji baseline'u.
- **Flip 13:45 UTC:** atomic; `C.flag` → False; const=False (koniec miny const≠json); usługi active bez restartu (hot-reload).
- **Live po flipie (13:52):** 0 decyzji od 13:46 (cisza przedpeakowa) — dowód wolumenowy w peaku 16-21, zliczy at#217 sekcja D3.
- **Finalna regresja (zamrożony stan, z testami D3): 5170 passed / 0 failed** (= baseline 5166 + dokładnie 4 nowe; 27 skip zegarowe, 8 xfail).

## APPENDIX — CLEANUP KODU GAŁĘZI (pre-ACK Adriana 18.07 „Dawaj usunięcie kodu gałęzi po oknie")

- **Gałąź `d3-gold-cleanup`, commit `0f8452b`** (zbudowany i przetestowany 18.07 w worktree `wt-d3cleanup-pkgroot`): obie gałęzie feasibility_v2 wycięte (markery-komentarze), flaga zdjęta z ETAP4+const, registry→dead (carriers=marker — schema wymaga niepustych), testy przepisane post-removal (gold=std parytet + **flaga-widmo inert** + deregistracja), o2_capz bez martwych referencji.
- **Walidacja wt: PEŁNA SUITA 5170/0** (klucz do zielonego biegu: `ZIOMEK_SCRIPTS_ROOT=pkgroot` + lane flags.json bez klucza — trzy wcześniejsze biegi po kolei ujawniły: leak-guard ratchet czyta ŻYWY flags.json spod SCRIPTS_ROOT, checker wymaga niepustych carriers, env `DISPATCH_FLAGS_PATH` nie wystarcza bo conftest ma własny root).
- **Apply: at#218 pon. 20.07 07:20 UTC** → `d3_cleanup_gated_apply.sh` — bramki: (0) werdykt at#217 istnieje, (1) D3 w oknie zielone (0 nowych odzysków), (2) flaga nadal false (rollback flipa = ABORT), (3) czyste drzewo plików celu; potem cherry-pick `0f8452b` → py_compile → **zdjęcie klucza flags.json PRZED pytestem** (wymóg ratchet-guarda; backup + restore przy abort) → PEŁNA regresja → checkery. Każdy fail = forward-safe revert + raport `D3_CLEANUP_APPLY_REPORT.txt`. ZERO restartów (kod martwy przy OFF).
- Rollback po aplikacji: `git revert` commitu cleanup (+ ewentualnie klucz z backupu `.bak-pre-d3-cleanup-2026-07-20`).

regresja: 5170 passed, 0 failed (baseline 5166 + dokładnie 4 nowe testy D3; log d3_final_pytest)
