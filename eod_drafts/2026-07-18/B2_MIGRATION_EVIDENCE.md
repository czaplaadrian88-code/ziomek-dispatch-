# Sprint B2 (2026-07-18) — EVIDENCE: migracja ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION → flags.json

GO Adriana: „Dawaj sprint migracji COMMITTED_PROPAGATION" (po audycie parytetu 18.07).
Protokół #0 ETAP 0→7; mapa kompletności = skill `ziomek-cto scope` (klasy: feasibility-hard + nowa-flaga).

## ETAP 0 — stan na żywo
- Baseline PRZED zmianą: **pytest 5166 passed / 0 failed / 24 skip / 8 xfail** (bieg ~10:40 UTC, exit 0).
- Sobota 18.07, 12:3x-13:xx Warsaw — blackout sobotni 16-21 → okno restartu OTWARTE.
- tmux recon: CE-003 (201/ce003-*) inny obszar; brak cudzych `.bak` na plan_recheck/common; git log -3 = moje commity parytetu; atq pusty.
- Efektywny stan flagi PRZED: env=1 drop-inami w {plan-recheck, b-route-shadow, carried-first-guard}; panel-watcher BEZ env → OFF (ROZJAZD B2).

## ETAP 1-3 — źródło + mapa (wszystkie miejsca)
| Miejsce | Status |
|---|---|
| `plan_recheck.py:396` read-site env-const → `_CF.decision_flag(...)` | ✅ TAK |
| `plan_recheck.py` `_D3_FALA_A_FLAGS` (hot-reload w pw: hooki :2152/:2230/:2576) | ✅ TAK (dopisana) |
| `common.py` const-fallback = **True** (steady-state json; const≠json = mina L6) | ✅ TAK |
| `common.py` `ETAP4_DECISION_FLAGS` (+komentarz migracji) → fingerprint/strip | ✅ TAK |
| `../flags.json` klucz `true` (atomic; PRZED commitem kodu — timery biegną z working tree, kolejność edycji common→plan_recheck→flags gwarantuje ON w każdym stanie pośrednim) | ✅ TAK |
| Gałąź `:839` w `_gen_one_bag_plan` | NIETKNIĘTA (tylko źródło flagi) |
| **Bliźniak `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH`** | **N-D z dowodem:** read TYLKO w `_refresh_live_eta_from_plans` (def :2422), jedyny caller `run_recheck:2712` + guard :2710 → NIEOSIĄGALNA z pw (recanon/redecide nie dochodzą) → kuracja single-service PRAWDZIWA (why w flag_registry zaktualizowane 18.07) |
| Bliźniak `ENABLE_PICKUP_REFLOOR` (:77 env-const default ON) | N-D: ZERO nośników w /etc → uniform we wszystkich procesach, brak rozjazdu |
| Cross-repo (panel nadajesz_clone, courier_api) | N-D: grep = 0 konsumentów |
| Serializer A+B / shadow_decisions | N-D: flaga warstwy planu, nie emituje metryk decyzji; markery `COMMITTED_TIEBREAK_*` idą do logu plan_recheck |
| `tools/flag_registry.py` | ✅ KNOWN_DIVERGENCES wpis usunięty (rozjazd domknięty migracją; komentarz historyczny); LIVE_ETA why z dowodem 18.07; auto-klasyfikacja carrierów = `json-overrides-env/open` |
| `tools/flag_lifecycle_registry.json` | ✅ seeder `--merge` (kuracja zachowana 505/505) |
| Drop-iny env=1 (3 pliki) | → DO ZDJĘCIA po restarcie (komendy `!` u Adriana; martwe — decision_flag nie czyta env) |
| `ZIOMEK_LOGIC_REFERENCE.md` | ✅ sekcja „Sprint B2 (2026-07-18)" |

## ETAP 4 — dowody mechaniczne
- **ON≠OFF:** `tests/test_committed_propagation_b2.py::test_on_adopts_committed_aware_plan_off_keeps_base` — zapisany plan różni się (pickup +7 min adoptowany tylko przy ON).
- **Guard anty-regresyjny nietknięty:** `::test_on_guard_rejects_committed_plan_worsening_sla` — gorsze `sla_violations` → ON zostaje przy baseline.
- **Wiring D3:** `::test_d3_wiring_hot_reload_and_registry` — członkostwo w `_D3_FALA_A_FLAGS`+ETAP4, const=True, refresh nadpisuje TYLKO gdy klucz w flags.json (kontrakt monkeypatch przeżywa strip).
- Sąsiedzi + nowe: `test_committed_propagation_b2 + planner_k15 + l3_gates + plan_cas + live_eta_refresh + flags_io + flag_registry_f3 = 85/85`.
- Kontrakt testów zachowany: wszystkie istniejące manipulacje to `monkeypatch.setattr(PR, "ENABLE_...")` — gałąź czyta globalę modułu, setattr działa jak dotąd.
- Checkery: `flag_lifecycle_check --live` 505 OK · `flag_hygiene` 245/245, 0 sierot · `flag_doc_coverage` ✅ brak driftu · `flag_registry` f3 12/12.
- py_compile: common, plan_recheck, flag_registry OK.
- **Pełna regresja PO zmianie:** → wynik dopisany niżej (bieg w tle).

## ETAP 5 — pozytywny wpływ (uczciwie)
- **Cel = parytet bliźniaczych ścieżek pw↔tick** (klasa „reguła w N procesach"): po migracji strukturalnie JEDNO źródło (flags.json) — rozjazd niemożliwy z konstrukcji.
- **Replay A/B offline na ŻYWYCH workach** (`b2_committed_ab_replay.py`, save_plan przechwycony, file-logi zdjęte, real OSRM): 5 worków z committed → **OFF↔ON: 5/5 IDENTYCZNE; szum OFF↔OFF2: 0** (determinizm potwierdzony). Interpretacja: przed-peakowy sobotni snapshot bez aktywnego przypadku mrugania; mechanizm = behavior-preserving z gotowością adopcji committed-wygranych w peaku (guard filtruje pogorszenia — dowód testem #2).
- Historyczny pozytyw gałęzi: replay 22.06 przy wdrożeniu tie-breaka („zachowuje czyste wygrane punktualności odbioru, odrzuca trade-offy").
- **Okno 2 dni (do pon. 20.07):** markery `COMMITTED_TIEBREAK_ADOPT/REJECT` w journalu `dispatch-panel-watcher` (dotąd niemożliwe: pw=OFF) + re-run replayu; werdykt = brak regresji + liczba adopcji pw. Rejestracja niżej (at-job).

## ETAP 6-7 — deploy + rollback
- Kolejność: flags.json PRZED kodem dla timerów nieistotna (const=True chroni każdy stan pośredni) — wykonano: common → plan_recheck → flags.json.
- Timery (plan-recheck / carried-first-guard oneshot) biorą nowy kod następnym tickiem — wartość ON bez zmiany dla nich.
- **1 restart = `dispatch-panel-watcher`** (jedyny proces zmieniający zachowanie OFF→ON) — przed 16:00 Warsaw; wynik dopisany niżej.
- Rollback: **hot** `flags.json → false` (pw łapie przez `_refresh_d3_fala_a_flags` bez restartu; timery następny tick) / `.bak-pre-b2-migration-2026-07-18` (plan_recheck, common) / git revert.

## DoD — tokeny mechaniczne (format bramki `ziomek-cto dod`; treść = skrót sekcji wyżej)

regresja: 5169 passed, 0 failed (pełna suita vs baseline 5166 — dokładnie +3 nowe testy B2; log b2_final3_pytest)
e2e: replay `b2_committed_ab_replay.py` przez PEŁNĄ warstwę planu na ŻYWYCH workach (real OSRM :5001 + żywy orders_state/gps → `_gen_one_bag_plan` end-to-end, save przechwycony) — 5 worków committed, 0 błędów; warstwy decyzji silnika (assess_order) NIEDOTKNIĘTE (flaga żyje wyłącznie w warstwie planu — grep 0 odczytów poza plan_recheck)
replay: ON↔OFF na żywych workach: 5/5 identycznych + szum OFF↔OFF2 = 0 (determinizm; przed-peak) — zmiana klasy parytet-bliźniaków: pozytyw = pw↔tick JEDNO źródło z konstrukcji + test ON≠OFF (adopcja tylko przy ON) + guard-test (nie pogarsza SLA); skala mechanizmu: 1047 ADOPT / 632 REJECT historycznie w ticku; okno 2d at#217 zliczy adopcje pw
rollback: flaga=false w flags.json (hot-reload w pw przez _refresh_d3_fala_a_flags, bez restartu; timery następny tick) / .bak-pre-b2-migration-2026-07-18 (plan_recheck.py, common.py) / git revert commitu

N-D: core/candidates.py — nie czyta ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION (grep 0); pętla kandydatów poza warstwą planu, semantyka gałęzi :839 nietknięta
N-D: feasibility_v2.py — nie czyta flagi (grep 0); HARD-y (R6/R27/R-SCHEDULE) niedotknięte — migracja zmienia ŹRÓDŁO flagi, nie regułę
N-D: route_simulator_v2.py — nie czyta flagi (grep 0); symulator dostaje czas_kuriera_warsaw na OrderSim jak dotąd (gałąź :839 bez zmian)
N-D: sla_anchor.py — nie czyta flagi (grep 0); kotwice SLA niedotknięte
N-D: panel_watcher.py — CALLER (redecide/recanon), bez własnej kopii reguły ani odczytu flagi (grep 0) — dostaje fix przez plan_recheck + hot-reload hook
N-D: route_order.py — render/kolejność display, nie czyta flagi (grep 0)
N-D: route_podjazdy.py — render podjazdów dla apki, nie czyta flagi (grep 0)

## Wyniki końcowe
- **Pełna regresja PO (stan zamrożony): 5169 passed / 0 failed / 24 skip / 8 xfail** = baseline 5166 + dokładnie 3 nowe testy B2, zero regresji.
- Dwa wcześniejsze biegi regresji złapały po drodze realne problemy (obydwa naprawione przed commitem): (1) race z równoległym dopisywaniem wpisu do LOGIC_REFERENCE (test doc-coverage); (2) **bug seedera lifecycle: `--merge` wyzerował pole kuracji `known_drift_note` przy USE_V2_PARSER** — pole przywrócone (format seedera zachowany: sort_keys+indent2+trailing NL), bug w backlogu.
- **Restart pw (jedyny, 13:04 Warsaw — 3h przed sobotnim blackoutem):** active, 0 ERROR/Traceback w journalu, health :8888 `healthy` (221 orders, fetch żywy). Efektywny stan procesu (wzorzec #9): `/proc/PID/environ` → **0 wystąpień COMMITTED** (env zdjęty z unitu przez daemon-reload sprzed restartu) → decyduje flags.json=True; świeży import w identycznym env = True; hook `_refresh_d3_fala_a_flags` dociąga wartość na każdym recanon/redecide (hot-reload potwierdzony testem wiring).
- **Cleanup martwych env-carrierów (3 pliki, backupy obok):** `committed-propagation.conf` → mv do `.bak-post-b2-migration-2026-07-18`; `route-flag-parity.conf` + `engine-env-parity.conf` → sed PEŁNĄ nazwą (ENABLE_LEX_COMMITTED_WINDOW* i LIVE_ETA_REFRESH NIETKNIĘTE — zweryfikowane grep) + `.bak-pre-b2-cleanup-2026-07-18`; daemon-reload OK.
- **KOŃCOWE MIERNIKI: `flag_registry` ROZJAZDY = 0 · entropy #4 = 0** (z 2 rano → 1 po USE_V2_PARSER → 0 po B2). Pierwsza metryka entropii na zerze.
