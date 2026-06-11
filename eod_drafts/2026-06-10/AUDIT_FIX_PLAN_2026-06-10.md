# PLAN NAPRAW PO AUDYCIE ZIOMKA 2026-06-10 — kolejność wykonania

> **Dla następnej sesji CC: to jest plik roboczy planu. Zanim zaczniesz — przeczytaj memory `ziomek-audit-2026-06-10.md` (findingi Z-01..Z-22) + `lessons.md` #180.**
> Statusy aktualizuj W TYM pliku (zmień `[ ]` → `[x]` + commit/tag/data) ORAZ w memory `todo_master.md` (#8/#9 + sekcja AUDYT).
> Źródło: pełny audyt 10.06 (sesja CC, read-only) + decyzje Adriana z 10.06:
> 1) „100-pkt cap bonusów" = fikcja wcześniejszej sesji CC, NIE reguła — nie implementować.
> 2) Incydent 06.06 = pytest, nie panel (dowód w lessons #180).
> 3) **DYREKTYWA TRWAŁA: zawsze propozycja, Ziomek ma pracować bez udziału człowieka** → memory `feedback-always-propose-defer-pickup.md`. Nowe bramki = „popraw propozycję", NIE „eskaluj do KOORD".
> 4) restaurant_violations ±5 min — potwierdzone wymaganie (kontrakt).

**Zasady wykonania (obowiązują każdy etap):** workflow per-krok z ACK (draft → `.bak` → edit → `py_compile` → import → testy → commit+tag → restart → verify), atomic writes, NIE restartuj `dispatch-telegram` bez jawnego ACK Adriana, zmiany scoringu ZAWSZE shadow-first/replay-first, flagi z kill-switchem. Venv: `/root/.openclaw/venvs/dispatch/bin/python`.

---

## ETAP 0 — ODBLOKOWANIE AUTONOMII (5 minut, zrobić NATYCHMIAST) `[x]` ✅ 2026-06-10 18:23 UTC

> **WYKONANE 2026-06-10 18:23:45 UTC** (sesja CC wieczór): `PARSER_DEGRADED=false` + `PARSE_CONTINUITY_GUARD_ENABLED=false` (tymczasowo) + `A4_TEST_FLAG=false`, atomic, hot-reload, zero restartów. Backup: `flags.json.bak-pre-etap0-parse01-2026-06-10`. Weryfikacja: decyzje po 18:23 w shadow_decisions mają auto_route_reason BEZ parser_degraded (ALERT tylko z realnym powodem weak_pick_score, ACK z C3_tier). Guard re-enabled w ramach Etapu 1 (niżej).

**Problem (Z-01):** `PARSER_DEGRADED=true` w flags.json od 06.06 17:53 (ustawione przez pytest) → `auto_proximity_classifier:414` daje ALERT na KAŻDE PROPOSE; AUTO=0 od 5 dni, ~85% propozycji = fałszywy ALERT.

**Kroki:**
1. `PARSER_DEGRADED=false` w flags.json (hot-reload ~5 s, ZERO restartów):
```bash
python3 -c "import json,os,tempfile; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['PARSER_DEGRADED']=False; fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False)); os.replace(t,p)"
```
2. **TYMCZASOWO** `PARSE_CONTINUITY_GUARD_ENABLED=false` (powrót do shadow) — inaczej **każdy pełny przebieg pytest re-ustawi flagę** (test_v320_packs_ghost pisze do żywych flag, dopóki Etap 1 nie wyląduje). Guard w shadow dalej loguje „ZABLOKOWALBYM". Re-enable = ostatni krok Etapu 1.
3. Przy okazji: `A4_TEST_FLAG=false` (artefakt testowy, Z-21).

**Weryfikacja:** następnego dnia rozkład `auto_route_reason` w `shadow_decisions.jsonl` — parser_degraded=0, AUTO wraca do ~10-40/dzień (baseline 06-04/06-05):
```bash
python3 -c "
import json,collections
c=collections.Counter()
for l in open('/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl','rb').readlines()[-400:]:
    try: d=json.loads(l)
    except: continue
    c['parser_degraded' if 'parser_degraded' in str(d.get('auto_route_reason')) else d.get('auto_route')]+=1
print(c)"
```
**Ryzyko:** zerowe (rollback = flip z powrotem).

---

## ETAP 1 — STRUKTURALNY FIX PARSE-01 + IZOLACJA TESTÓW (1 sesja, ~0.5-1 d) `[x]` ✅ 2026-06-10 wieczór

> **WYKONANE 2026-06-10 ~18:40 UTC.** Commity (PUSHED na origin ~18:50 za ACK Adriana):
> - dispatch_v2 `d28d50a` + tag `parse01-lifecycle-test-isolation-2026-06-10` (master): kroki 1+2+3+6 — lifecycle SET_BY/TS w flags.json (recovery czyści po `SET_BY=="parse01"` cross-procesowo; set ręczny/obcy NIE czyszczony) + L1 writer odmawia pod PYTEST_CURRENT_TEST (opt-out `ALLOW_FLAGS_WRITE_IN_TEST=1`) + L2 conftest autouse `_isolate_flags_json` (kopia żywego flags.json→tmp, patch FLAGS_PATH w common+pcg+core.flags_io, reset cache) + `CALIBRATION_EXCLUDE_WINDOWS` w `tools/faza7_daily_kpi.py` (okno 06-06 17:53 → 06-10 18:24 UTC wykluczone u źródła wczytywania).
> - panel `8421f50` (coordinator-console): krok 4 — watchdog **R16** w assistant-watcher (PARSER_DEGRADED>2h przy żywym parse → alert; oneshot/60s wciąga kod sam, bez restartu).
> Testy: guard 13/13 (4 nowe: lifecycle keys / cross-process set→clear / manual-set-not-cleared / L1 refusal), watcher 14/14 (3 nowe R16). **Pełna suita: 49 failed = IDENTYCZNA lista jak baseline pre-change (diff pusty), 2089 passed — zero nowych faili.** Smoke cross-procesowy: set w procesie A → clear w świeżym procesie B (tmp flags) PASS. Krok 5: restart dispatch-panel-watcher czysto (18:36) → `PARSE_CONTINUITY_GUARD_ENABLED=true` (hot-reload). Backupy: `parse_continuity_guard.py/.conftest/.test/.faza7_daily_kpi` `.bak-pre-etap1-*-2026-06-10`, watcher `.bak-pre-r16-parser-degraded-2026-06-10`.

**Cel:** żeby ta klasa błędu (lessons #180) nigdy nie wróciła: (a) flaga persystentna z lifecycle w pamięci procesu, (b) testy piszące do żywego flags.json, (c) flip uzbrajający uśpione side-effecty testów.

**Kroki:**
1. `parse_continuity_guard.py`: zamiast `_degraded_set_by_guard` (in-memory) → zapis `PARSER_DEGRADED_SET_BY="parse01"` + `PARSER_DEGRADED_SET_TS` w flags.json przy set; recovery czyści gdy `SET_BY=="parse01"` — **niezależnie od procesu/restartu**. Przy czyszczeniu usuwa też SET_BY/TS.
2. L1 (wzorzec lekcji #75 z telegram_utils): `_set_parser_degraded` (docelowo: wspólny helper zapisu flags.json) **odmawia zapisu gdy `PYTEST_CURRENT_TEST` w env** (opt-out `ALLOW_FLAGS_WRITE_IN_TEST=1`), loguje warning.
3. L2: `tests/conftest.py` — autouse fixture monkeypatch `parse_continuity_guard.FLAGS_PATH` + `common.FLAGS_PATH` na tmp_path (globalna izolacja; per-file patche zostają jako L3).
4. Watchdog (np. w `assistant-watcher` R-reguła albo cron_health): `PARSER_DEGRADED=true` dłużej niż 2h przy żywym parse (świeże cykle w parser_health) → alert Telegram.
5. Re-enable `PARSE_CONTINUITY_GUARD_ENABLED=true` + smoke: ręcznie 2× evaluate z pustym order_ids w konsoli testowej (z patchem!) → confirmed → set/clear działa cross-procesowo.
6. **Kalibracja Fazy 7: wykluczyć okno 2026-06-06 17:53 → moment Etapu 0** z wszelkich analiz auto_route (dane zatrute — dopisać do skryptów/notatek kalibracyjnych `faza7_kpi`).

**Testy:** rozszerzyć `test_parse_continuity_guard.py` o: set w procesie A + clear w „procesie B" (reset_for_test między), odmowa zapisu pod PYTEST_CURRENT_TEST, zgodność SET_BY lifecycle.
**Ryzyko:** niskie. **Zależności:** Etap 0.

---

## ETAP 2 — PEWNE POPRAWKI SCORINGU (1 sesja, ~1 d, wszystko z replayem) `[x]` ✅ 2026-06-10 wieczór

> **WYKONANE 2026-06-10 ~19:10 UTC** (sesja CC wieczór, 5 commitów+tagów na master, restart dispatch-shadow 19:09:43 czysty):
> - **Z-02** `8bc073c` tag `v327-mult-sign-guard-unknown-split-2026-06-10` — `apply_bundle_score_mult` (mult tylko na dodatnim score) + `min_drop_proximity_factor_split` (Unknown→0.7, znany cross→0.1); flaga `ENABLE_V327_MULT_SIGN_GUARD` ON.
> - **Z-09** `c58f121` tag `shadow-serializer-v327-posstore-2026-06-10` — prefiksy `v327_`/`late_pickup_`/`new_pickup_` + `pos_from_store`/`pos_age_min` w LOCATION A+B.
> - **Z-10** `b97023c` tag `f7-margin-final-ranking-2026-06-10` — margin=score(best)−max(reszta feasible) + C7 `best_not_score_top`; flaga `ENABLE_F7_MARGIN_FINAL_RANKING` ON (czysto shadow).
> - **Z-11** `df2598d` tag `v328-heuristic-shift-end-guard-2026-06-10` — `_v328_heuristic_post_shift_skip` (shift_end < now+naive_eta → skip); flaga `ENABLE_V328_HEURISTIC_SHIFT_END_GUARD` ON.
> - **Z-06** `7dcd230` tag `fail12-storepos-strict-2026-06-10` — `check_feasibility_v2(pos_from_store)`, FAIL12: gps AND not store + metryka `fail12_storepos_blocked`; C4 strict_gps też; flaga `ENABLE_FAIL12_STOREPOS_STRICT` ON.
> **Replay 7d (1461 PROPOSE):** Z-02 18 flipów/1322 (1.4%), przegląd 15 ręcznie — wszystkie korekcyjne (inwersja znaku ~11 + Unknown-split ~7, m.in. #479042 bundle 244 pkt zgnieciony do 24 przez Unknown). Z-10: **best≠score-top w 68% decyzji, stary margin zawyżony o medianę 105 pkt**; z 53 AUTO tylko 3→ACK. **⚠ progi Fazy 7 (min_score_margin) stroione na fikcji — przeliczyć rozkład NOWEGO marginu przed flipem.** Pytest: 49 failed = identyczna lista jak baseline (diff pusty), 2145 passed (+56 nowych). Pełny werdykt: `ETAP2_VERDICT_2026-06-10.md`.

Cztery małe, dobrze wycelowane fixy poprawiające dzisiejsze propozycje:

1. **Z-02: mnożnik Bug Z bez inwersji znaku** (`dispatch_pipeline.py:3327-3329`): mnożyć tylko dodatni score (`if final_score > 0: final_score *= mult`); dla ujemnych — nic (kary już działają). Rozdzielić `Unknown` od cross-quadrant: Unknown → mult 0.7 (łagodny defensive) zamiast 0.1; cross-quadrant zostaje 0.1. Flaga `ENABLE_V327_MULT_SIGN_GUARD` default ON + env kill-switch.
2. **Z-09: serializacja** — dodać `"v327_"` do `_AUTO_PROP_PREFIXES` (`shadow_dispatcher.py:189`) + `pos_from_store`/`pos_age_min` do candidate metrics i serializera + wyrównać `late_pickup_*` w best (LOCATION B) do alternatives.
3. **Z-10: margin AUTO na finalnym rankingu** (`auto_proximity_classifier.py:321-325`): `margin = score(result.best) − max(score pozostałych feasible)`; AUTO dodatkowo wymaga `best == score-top` (inaczej ACK z reason `best_not_score_top`). To PREREQUISITE przed jakimkolwiek flipem Fazy 7.
4. **Z-11: mass-fail heurystyka — minimalne bramki** (`dispatch_pipeline.py:3751+`): skip kuriera gdy `shift_end < now + naive_eta` (obok istniejącego bag-cap). 10 linii.
5. **Z-06 (część kodowa): semantyka pos_from_store w gate'ach** — FAIL12 (`feasibility_v2.py:561`) i przyszłe strict_gps wymagają `pos_source=="gps" and not pos_from_store`.

**Walidacja:** replay 7d corpus (istniejący harness `obj_replay_capture` / replay z eod_drafts) — policz ile zwycięzców się zmienia per fix; testy jednostkowe na inwersję znaku (score −80 cross-quadrant NIE może pokonać −50 same-quadrant).
**Ryzyko:** niskie-średnie (każdy fix za osobną flagą). **Zależności:** brak (można równolegle z E1).

---

## ETAP 3 — PĘTLA WYNIKOWA „PANEL_AGREE" (1-2 d) — FUNDAMENT `[x]` ✅ 2026-06-10 wieczór

> **WYKONANE 2026-06-10 ~19:20 UTC** (sesja CC „Sesja C", 4 commity+tagi na master, restart dispatch-panel-watcher 19:19:24 czysty — skoordynowany PO commitach Sesji 2/ETAP 2; dispatch-telegram NIETKNIĘTY):
> - **Krok 1** `6e11712` tag `panel-agree-loop-2026-06-10` — `_check_panel_agree` w panel_watcher (lustrzane do PANEL_OVERRIDE, te same 3 call-sites; packs_fallback/coldstart celowo poza OBOMA — symetria). Edge: (a) cid=26 guard (emit-sites już filtrują — belt-and-suspenders); (b) pending_proposals[oid] = zawsze OSTATNIA propozycja (nadpis przez proposal_sender) + świeżość sent_at ≤15 min (`PANEL_AGREE_MAX_PROPOSAL_AGE_MIN` env); (c) ASSIGN z Telegrama popuje pending PRZED przypisaniem w panelu → tail-scan 256KB learning_log za świeżym ASSIGN_DIRECT, `chosen==proposed==panel_cid` → AGREE `source="telegram"` (ASSIGN w alternatywę → nic; TAK → zostaje TAK). Schemat pól jak OVERRIDE (`proposed_courier_id`/`actual_courier_id` — build_roster w sequential_replay łapie bez zmian) + latency_s/proposed_score/proposal_verdict/restaurant/proposed_tier/pickup_ready_at/order_created_at/source/panel_source. Flaga `ENABLE_PANEL_AGREE` (flags.json hot-reload, default env≠"0"→ON). Zapis przez `jsonl_appender` (flock, MP-#11). Testy 13/13 + smoke_panel_override PASS.
> - **Krok 2** `f1c78ce` tag `learning-log-denoise-2026-06-10` — RESOLVE_CID_* → `state.append_match_debug_log` → `dispatch_state/courier_match_debug.jsonl`; para: `scripts/schedule_utils.py` (MATCH_AMBIGUOUS/NOT_FOUND, plik POZA repo — edit+.bak `bak-pre-etap3-matchdebug`) przekierowany tak samo. Konsumenci zaudytowani (lekcja #80): daily_briefing/learning_analyzer/telegram `/status`/validation_gate_lgbm/sequential_replay.build_roster/sprint2_analysis/panel+assistant — ŻADEN nie liczy MATCH_*/RESOLVE_*. **Korekta audytu: MATCH_*/RESOLVE_* = 90.7% LICZBY wpisów (8071/8903), ale tylko 3.0% bajtów (1.14 MB)** — wagę 37.7 MB robią TIMEOUT_SUPERSEDED (55%) + PANEL_OVERRIDE (40%) z pełnym `decision`. Stary learning_log nietknięty. LIVE: ostatni MATCH_* w learning_log 19:08:11 (sprzed restartu shadow przez Sesję 2 o 19:09:43, który wciągnął nowy schedule_utils); courier_match_debug.jsonl rośnie. Testy resolve_cid 19/19.
> - **Krok 3** `3313058` tag `briefing-acceptance-2026-06-10` — daily_briefing: dzienna linia `Acceptance (panel) AGREE/(AGREE+OVERRIDE)` (morning+evening) + sekcja „Acceptance 7d" w morning (per tier / pora peak 11-14/17-20 Warsaw / typ czasówka≥60 min prep vs elastyk + top-3 komponenty score OVERRIDE'owanych zwycięzców z `decision.best` EMBEDOWANEGO w rekordzie — ta sama decyzja co shadow_decisions po order_id, bez skanu wielkiego pliku per briefing). Testy 9/9 + dry-run obu trybów na żywych danych. **⚠ ODKRYCIE: daily_briefing NIE MIAŁ żadnego harmonogramu** (wbrew „już ma crona" — crontab/timers/at: zero, log pusty od 08.05) → dopisane 2 wpisy user-crontab (06:00 + 20:00 UTC; backup `/root/backups/crontab.bak-pre-etap3-briefing-2026-06-10`; rollback = usunąć 2 linie).
> - **Krok 4** `c8121b4` tag `panel-agree-baseline-2026-06-10` — baseline retroaktywny z backfill (3056 dostaw 06-01..06-09): **per-order (OSTATNIA propozycja, definicja PANEL_AGREE) proposed==courier_id_final = 18.0% (307/1701)** — dolne przybliżenie (reassign=rozjazd). Sygnał kluczowy: ostatnia propozycja bez explicit-reject (TIMEOUT_SUPERSEDED) kończy u proponowanego w **64.3%**; po PANEL_OVERRIDE wraca tylko 2.1%. Tiery: std+ 25.3% > std 17.1% > **gold 12.7%** (anomalia — do E7), slow 0/13, new 31.2%. 100% elastyk (czasówki bez propozycji = spójne z Z-05). Raport: `eod_drafts/2026-06-10/panel_agree_baseline.md`.
> Pytest pełny: **49 failed = diff listy vs baseline PUSTY, 2154 passed** (+22 nowych testów E3). Backupy: `panel_watcher.py.bak-pre-etap3-panel-agree`, `daily_briefing.py.bak-pre-etap3-acceptance`, 5× `.bak-pre-etap3-matchdebug` (state/worker/helpers/test/schedule_utils). Rollback: flaga `ENABLE_PANEL_AGREE=false` (hot-reload) / `git revert` per tag. Weryfikacja live PANEL_AGREE: monitor uzbrojony po restarcie (wieczór, niski ruch — wpisy spłyną z pierwszymi przypisaniami z propozycją; acceptance w briefingu od pierwszego crona 06:00 UTC).

**Problem (Z-03):** 0× TAK/NIE/F7AGREE w 14 dni; system nie wie, czy propozycje są trafne. Zgodne przypisanie panelem (koordynator daje TEGO SAMEGO kuriera co propozycja) nie jest dziś logowane — tylko rozjazdy (PANEL_OVERRIDE, 315/14d).

**Kroki:**
1. `panel_watcher` (obok istniejącej logiki PANEL_OVERRIDE, plik `panel_watcher.py:142-190`): gdy COURIER_ASSIGNED i istnieje świeża propozycja (≤15 min) dla tego ordera → porównaj cid: zgodny → `action=PANEL_AGREE`, różny → istniejący PANEL_OVERRIDE. Zapis do learning_log + pole `proposed_cid`/`assigned_cid`/`latency_s`.
2. **Odszumić learning_log:** MATCH_AMBIGUOUS / MATCH_NOT_FOUND / RESOLVE_CID_* przenieść do osobnego `courier_match_debug.jsonl` (to ~90% objętości; `daily_briefing` czyta learning_log jako licznik decyzji i dziś kłamie).
3. Raport tygodniowy (cron, wzór `daily_briefing`): acceptance-rate = AGREE/(AGREE+OVERRIDE), per tier/per pora/per typ (czasówka vs elastyk) + top komponenty score u OVERRIDE'owanych zwycięzców.
4. (Opcjonalnie, jeśli czas) outcome-link: dopiąć `backfill_decisions_outcomes` żeby per propozycja był też realny czas dostawy — atrybucja jakości do komponentów score.

**Dlaczego TERAZ:** każda dalsza kalibracja (E5, E7, progi Fazy 7, FILOZ-1 dynamic caps) bez tego jest zgadywaniem. Od momentu wdrożenia dane się zbierają — im wcześniej, tym szybciej E7.
**Ryzyko:** niskie (read-only nad istniejącymi strumieniami; bez restartu telegrama). **Zależności:** brak.

---

## ETAP 4 — UNIFIKACJA FLAG DECYZYJNYCH CROSS-PROCES (1 d) `[x]` ✅ 2026-06-10 ~20:10 UTC

> **WYKONANE 2026-06-10 wieczór** (sesja CC ETAP 4). Commity (master): `17407e8` mechanizm (common.decision_flag: flags.json→stała modułu→False + ETAP4_DECISION_FLAGS + flag_fingerprint + conftest wycina klucze ETAP4 z tmp-kopii + script-runnery dostają kopię bez flag przez env `DISPATCH_FLAGS_PATH` + tool `tools/flag_fingerprint.py`) → `6e3108d` call-sites 15 flag (dispatch_pipeline/feasibility_v2/route_simulator_v2/courier_resolver `_f4_flag`/common C2) → `81676b3` FLAG_FINGERPRINT przy starcie 4 procesów → `c317d05` tabela inwentaryzacyjna. Tag `etap4-flag-unification-2026-06-10`.
> - **Inwentaryzacja + ACK Adriana**: `flag_inventory_etap4.md` (obok) — 13 flag (a)→flags.json (wartości = stan shadow 06-10, w tym **COMMIT_DIVERGENCE_VERDICT_GATE=false — czasówka miała gate ON wbrew ALWAYS-PROPOSE, bo default common.py="1"**); 5 flag (b) telemetria zostaje shadow-only (REPLAY_CAPTURE/LGBM×2/SELECTION_VETO/PENDING_POOL); 8 wpisów (c) celowo per-proces. Koeficjenty (A2=60/SPAN=1.0/R6=100) = identyczne z defaultami → tylko sprzątnięte z unitów.
> - **Unity sprzątnięte**: override.conf shadow (zostały tylko (b)+(c) z klasyfikacją w komentarzach), main unit (R6 przeniesiony — R6FRESH-DUP-CONFIG-01 zamknięty, kanon=flags.json), `etap2-flip.conf` i `objective-alignment.conf` (plan-recheck) USUNIĘTE. Backupy `*.bak-pre-etap4-2026-06-10` obok plików + flags.json.bak.
> - **Walidacja**: test integracyjny (ten sam order+flota przez assess_order przy env „czasowka" vs „shadow" → identyczny ranking) PASS; suita 49 failed = diff vs baseline PUSTY, 2160 passed (+6); **fingerprinty 4 procesów IDENTYCZNE po flipie** (czasowka 20:09:13 PRZED: wszystko 0 + commit_div=1 → 20:10:18 PO: = shadow). Restarty shadow+panel-watcher 20:10 czyste (NRestarts=0, journal bez err); telegram NIETKNIĘTY.
> - Rollback per flaga = wpis w flags.json (hot-reload); pełny = `.bak` unitów + daemon-reload + restart.
> - KROK 0 (push ~10 commitów): nieaktualny — master był już na origin (E2/E3 wypchnęły).

**Problem (Z-04):** dispatch-shadow ma w override.conf ~15 flag env (m.in. `ENABLE_BUNDLE_DELIV_SPREAD_CAP`, `ENABLE_R1_PROGRESSIVE_CLIP`, `ENABLE_V319H_CONTINUATION_GUARD`, `ENABLE_A2_RELIABILITY_SOFT_SCORE`, `ENABLE_FAIL12_SCHEDULE_FAILOPEN`, `ENABLE_F4_COURIER_POS_PICKUP_PROXY/INTERP`, `ENABLE_C2_NEG_GAP_DECAY`, `ENABLE_OBJ_SPAN_COST`, `ENABLE_OBJ_R6_SOFT_DEADLINE` [main unit], `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP`, `ENABLE_LGBM_SHADOW`), których **dispatch-czasowka i dispatch-plan-recheck NIE mają** → czasówki/re-decyzje liczone starszym silnikiem.

**Kroki:**
1. Inwentaryzacja: tabela flaga → wartość live w shadow → docelowo wspólna? (większość TAK; wyjątki świadome wpisać z komentarzem).
2. Mechanizm: preferowane **flags.json** (hot-reload już działa cross-proces przez `C.flag()`); dla flag czytanych jako env module-level w common.py → zmienić odczyt na `flag(name, env_default)` wzorem `ENABLE_WORKING_OVERRIDE_GRAFIK_CAP` (hot-reload kill-switch). Alternatywa minimalna: wspólny `EnvironmentFile=/root/.openclaw/workspace/scripts/dispatch_flags.env` we WSZYSTKICH unitach dispatch-*.
3. W unitach zostają wyłącznie parametry infra (Memory/CPU/ścieżki).
4. Restarty: shadow + panel-watcher + oneshot timery (czasowka/plan-recheck wciągną same). **dispatch-telegram: nie ruszać** (nie czyta flag scoringowych).

**Walidacja:** ten sam order przepuszczony przez assess_order w procesie czasówki i shadow daje identyczny ranking (test integracyjny z zamockowanym fleet).
**Ryzyko:** średnie — flip „przy okazji" może włączyć coś, co w danym procesie było OFF celowo; stąd tabela inwentaryzacyjna PRZED zmianą + ACK Adriana na tabelę. **Zależności:** żadne twarde; przed E5.

---

## ETAP 5 — CZASÓWKI: SELEKCJA SCORE-BASED W T-60/T-50 (1-2 d) `[~]` KROK 1+5 DONE 2026-06-10 ~20:50 UTC; KROK 2 czeka na 24-48h danych shadow

**WYKONANE 10.06 wieczór (commity `f1f37d3`+`3ddfbcb`+`c2ca316`, tagi `etap5-krok1-czasowka-score-shadow-2026-06-10` + `etap5-krok5-waiting-at-persist-2026-06-10`, PUSHED origin):**
- **KROK 1 SHADOW LIVE:** `czasowka_proactive/score_selector.py` (czysta funkcja) + hook w `czasowka_scheduler` — w oknie 40<mins≤60 do `czasowka_eval_log` dopisywane pola `sb_*` (would_assign / cid / score / margin E2-Z10 / wait / r6 / pool / best_is_score_top / reject_reason). Gates: score≥`CZASOWKA_PROACTIVE_MIN_SCORE`(30), margin≥`CZASOWKA_PROACTIVE_MIN_MARGIN`(15; pula solo=strict reject `solo_pool`), wait≤`CZASOWKA_PROACTIVE_MAX_WAIT_MIN`(10), R6=0. Flagi w flags.json; `CZASOWKA_PROACTIVE_SCORE_SHADOW=true`, `CZASOWKA_PROACTIVE_SCORE_BASED=false` (flip = KROK 3 za ACK). Zero zmiany decyzji, zero restartów (oneshot). Tick 20:40 czysty. Testy 24/24; suita 49 failed = baseline (0 nowych).
- **KROK 2 TOOLING GOTOWY:** `eod_drafts/2026-06-10/czasowka_proactive_calib.py` — raport: would_assign rate T-60/T-50, zgodność sb_cid vs FORCE_ASSIGN T-40 vs realny kurier (learning_log PANEL_AGREE/OVERRIDE z E3), histogram reject_reasons, rozkłady score/margin/wait, **sensitivity 6 wariantów progów** (m.in. solo=OK, margin 10/5/0) → propozycja progów Z DANYCH. Uruchomić ≥12.06 wieczór: `/root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-06-10/czasowka_proactive_calib.py --md eod_drafts/<data>/czasowka_proactive_calib.md` → **STOP → ACK Adriana na progi → dopiero KROK 3 (flip TYLKO T-50)**.
- **KROK 5 BONUS LIVE:** `waiting_at` persystowany w orders_state (panel_watcher pu_reconcile, pierwszy widziany sid=4, idempotent, event `WAITING_AT_RESTAURANT_OBSERVED`; flaga `ENABLE_WAITING_AT_PERSIST` ON hot-reload). Detektor E6 podchwytuje automatycznie (arrival_source=status4 zamiast commit_fallback). Restart panel-watchera 20:47 UTC czysto. Testy 5/5.
- **OBSERWACJA (ważna do KROKU 2):** Z-05 częściowo zdezaktualizowany przez E4 — po unifikacji flag czasówki dostają match_quality=good i realne EMIT-y (10.06 np. #479740 EMIT @48min). 14d: WAIT 2506 / EMIT 233 / FORCE 215 (EMIT-y głównie post-E4). Kalibracja zmierzy przyrost score-based NAD nowym baseline'em, nie nad „100% FORCE".
- **SIDE-FINDINGS:** (a) `czasowka_proactive/state.cleanup_stale` NIGDY nie wpięty do prod — proposals_state rośnie od 05.05 (315 zamówień, 251KB); wpiąć przy KROKU 3; (b) testowy oid 500001 w prod proposals_state + 126× JSONDecodeError w czasowka_proactive.log = noise z testów (klasa #180 — ścieżki patchowane, ale logger wspólny i test mass-catchup używa oidów 500000+); prod plik VALID.

**ZOSTAJE:** KROK 2 raport (po 24-48h) → ACK progi → KROK 3 flip T-50 (`CZASOWKA_PROACTIVE_SCORE_BASED=true`; emisja ISTNIEJĄCĄ ścieżką czasowka_scheduler/czasowka_proactive — telegram_approver NIETKNIĘTY) → KROK 4 KPI w briefingu (% przypisanych przed T-40 cel ≥30%, R6-breach przed/po, „żałowane wczesne przypisania", wait kuriera).

**Problem (Z-05):** 2813/2813 ewaluacji (14 d) z candidates=0 → 100% czasówek = FORCE_ASSIGN na T-40. Progi proaktywne (kurier ≤1-2 km od restauracji + drop_prox ≥0.5, `common.py:1352-1357`) są niespełnialne przy 18% pokryciu GPS.

**Kroki:**
1. Wyrzucić progi km/drop_prox z okien T-60/T-50; kandydat „proaktywny" = z pełnego rankingu `assess_order` przy warunkach jakościowych na ISTNIEJĄCYCH metrykach, np.: `score ≥ 30` AND `margin ≥ 15` AND `przewidywany wait kuriera ≤ 10 min` AND brak breachy R6 w planie. Progi za flagami, kalibracja po tygodniu.
2. Zgodnie z dyrektywą always-propose: T-60/T-50 emitują PROPOZYCJĘ (nie cichy WAIT), z adnotacją „czasówka, odbiór za N min".
3. KPI w eval_log: % czasówek przypisanych przed T-40 (cel ≥30%), R6-breach rate czasówek przed/po.

**Ryzyko:** średnie — wcześniejsze commitmenty usztywniają plany (frozen window ±5). Mitygacja: zaczynamy od T-50 (nie T-60), mierzymy „żałowane wczesne przypisania" (czasówka przypisana wcześnie, potem PANEL_OVERRIDE/replan). **Zależności:** E4 (pełny silnik w procesie czasówki), E3 (miara sukcesu).

---

## ETAP 6 — RESTAURANT_VIOLATIONS ±5 MIN (0.5-1 d, niezależny — można wcisnąć wcześniej) `[x]` DONE 2026-06-10 ~20:20 UTC

**Wymaganie potwierdzone przez Adriana 10.06** („5± jest w kontrakcie i powinno działać"). KB §II.8 deklaruje plik `restaurant_violations.jsonl` — nigdy nie powstał (Z-19).

**WYKONANE:** detektor `sla_tracker._check_restaurant_violations` (wzorzec R6: skan per tick picked_up+delivered, seen-flag `restaurant_violation_logged` set-then-write, paczki pominięte, ZERO Telegrama; flaga `ENABLE_RESTAURANT_VIOLATIONS` default ON inline `C.flag`, próg 5.0 w sla_tracker — celowo NIE w common.py, kolizja z E4). **Przyjazd = commit_fallback:** zwiad KROK 0 potwierdził, że wejście w id_status=4 NIE jest nigdzie persystowane, a dodanie go = edycja panel_watcher.py (gorąca ścieżka + niezacommitowany WIP sesji E4) → STOP wg planu; kod forward-compat czyta `waiting_at` (arrival_source=status4) gdy pole kiedyś powstanie. Zapis przez `core.jsonl_appender` (flock). Sekcja „Naruszenia restauracji 7d" w `daily_briefing.format_morning` (top-5: liczba + mediana wait + % zleceń, mianownik z sla_log). Backfill 54 726 zleceń z CSV (2025-11→2026-06-09): **36,3% łamie ±5 min** — top Rany Julek 46,2% / Mama Thai Bistro 44,8% / Karczma Maciejówka 42,1% → `restaurant_violations_baseline.md` (obok). Testy 16/16, suita 49 failed = baseline (0 nowych). Restart dispatch-sla-tracker 20:16 UTC czysto (telegram nietknięty); live pierwsze ticki: 110 wpisów / 110 unikalnych oid / 0 ERROR.

**Kroki:**
1. Detektor w `sla_tracker.py` (już skanuje aktywne ordery co tick): naruszenie restauracji ≈ `czas_odbioru_timestamp (realny pickup) − max(czas_kuriera_warsaw (commit), przyjazd_kuriera) > 5 min`. Przyjazd kuriera: timestamp wejścia w `id_status=4` („oczekiwanie pod restauracją") z orders_state/event_bus; fallback: brak statusu 4 → licz od commit.
2. Zapis `dispatch_state/restaurant_violations.jsonl`: ts, oid, restauracja, commit, real_pickup, wait_min, cid. BEZ alertów Telegram na start (Adrian zarządza przez panel; alerty = osobna decyzja).
3. Tygodniowa agregacja per restauracja (może od razu do raportu z E3) — wejście do rozmów kontraktowych i przyszłej predykcji prep-time (restaurant_meta.prep_variance ma już zalążek).

**Ryzyko:** niskie (czysta telemetria). **Zależności:** brak.

---

## ETAP 7 — RE-TUNE HIERARCHII WAG (po ≥7-14 dniach danych z E3!) `[ ]`

> ⏰ **KICKOFF ZAPLANOWANY: at#131 → 2026-06-17 06:00 UTC (08:00 Warsaw)** — skrypt `eod_drafts/2026-06-10/e7_kickoff_notify.py` (read-only) wyśle Adrianowi na Telegram raport gotowości (acceptance tygodnia PANEL_AGREE per tier + AUTO/best_not_score_top + czasówki E5 + naruszenia) i wskaże tę sekcję jako spec sesji. Log: `scripts/logs/e7_kickoff.log`. Dry-run: `E7_DRY=1`. Anulowanie: `atrm 131`.

**Problem (Z-07/Z-08/Z-14/Z-15):** R4 do +150 pkt dominuje hierarchię R-PRIORYTETÓW (dystans max 30); tabela R-NO-WASTE z REGULY niezaimplementowana (ekstremalny overlap bez kary); `s_obciazenie` zeruje się uniwersalnie na bag≥5 wbrew doktrynie per-courier; tie-break R2 martwy (float equality).

**Kroki (jedna spójna paczka, replay + shadow-compare jak late_pickup Opcja B):**
1. Cap/renormalizacja R4: `bonus_r4 = min(60, raw×1.5)` (start; kalibracja replayem).
2. `r_no_waste(gap)` — pełna tabela z REGULY:49-71 (obie strony, z karami −10/−20/−30); wycofać nakładający się fragment timing_gap (kara >15 min zostaje TYLKO w jednej osi).
3. `s_obciazenie(bag, cap_tier_pora)` — normalizacja do efektywnego capa z BUG-4/courier_tiers zamiast stałej 5.
4. Quantyzacja klucza selekcji do ~2.5 pkt → realny tie-break: corridor dev, potem tier (zgodnie z R-PRIORYTETÓW #4).
5. Werdykt: acceptance-rate z E3 przed/po + replay 7d (zwycięzcy zmienieni: ile poprawek vs regresji, wzór werdyktów R1+CB 9.5:1).

**Ryzyko:** wysokie (szeroka zmiana rozkładu score) — dlatego OSTATNIE i dopiero z sędzią z E3. **Zależności:** E3 (dane), E2 (mnożnik naprawiony — inaczej kalibrujemy na artefakcie), E4 (jeden silnik).

### E7-DOKLEJKI (dopisane 2026-06-11, sesja audytowa Bartek 2.0 — ACK Adriana)

1. **Logrotate-aware sweep (ogon po SP-B2-LOGROT):** 3 czytniki nadal czytają TYLKO żywy plik i zaniżą okna po rotacji 100 MB: `r04_evaluator` (**okno 30 dni na learning_log!** — po rotacji widzi ~3 dni), `validation_gate_lgbm`, `learning_analyzer`. Fix = wzorzec `tools/_rotated_logs.py` (commit `4ba36ec`, SP-B2-LOGROT). ~1h, zrobić W TEJ sesji przed kalibracją wag (inaczej E7 liczy na obciętych danych!).
2. **Konsumpcja pól Bartek 2.0 (NIE dublować analiz):** `pln_*` (funkcja celu PLN w shadow od 11.06 14:13, commit `76d6c5a`) + `sync_*`/`bonus_sync_spread` (FLIPNIĘTY 11.06) + `loadgov_*` (FLIPNIĘTY 11.06) + `repo_*`/`soon_free_*` (shadow) — pełen kontekst i werdykty: `/root/ROADMAP_BARTEK2_2026-06-11.md` sekcja „DO DECYZJI ADRIANA" + `/root/bartek2_workdir/agent_econ/REPORT.md`. Przy re-tune R4/R-NO-WASTE sprawdzić nakładanie z nowymi karami (sync/repo/loadgov), żeby nie karać tej samej osi 2×.
3. **Wykonawczo przy flipach BUG A/B i carry_chain** (werdykty z 11.06 w DO DECYZJI): flagi są env-only — wciągnąć we wzorzec `decision_flag`/flags.json z ETAP4 (sekwencja: B → 7 dni → A częściowy bez Σ; carry_chain soft z `CARRY_CHAIN_HARD_REJECT_STOPS=999`).
4. **Lekcja #186 sweep:** przy okazji sprawdzić, czy żadna z flag dotykanych w E7 nie gate'uje OBLICZENIA pól shadow (wzorzec: licz zawsze, flaga tylko score).
5. **TODO drobne:** `dispatch_state/courier_vehicle.json` (cid → auto firmowe/własne) z panelu — uściśla koszt km w PLN-shadow (dziś konserwatywnie 0,90 dla wszystkich).

---

## RÓWNOLEGLE — ADOPCJA GPS (ops, Adrian; największa dźwignia danych) `[ ]`

Tylko 18% best-kandydatów ma żywy GPS; 82% pozycji syntetycznych ogranicza KAŻDY komponent score (Z-06). Działanie nie-kodowe: egzekwowanie apki przy odprawie (panel FLT-04 pokazuje kto ma telefon/apkę), cel ≥60% udziału `gps` w pos_source best w 30 dni. Pomiar: rozkład pos_source w shadow_decisions tygodniowo (skrypt z audytu).

## HIGIENA (wciskać w luki, bez osobnego sprintu) `[ ]`
- Z-20: `validation_gate_lgbm.py:178,183` → `ZoneInfo("Europe/Warsaw")` (DST!); `monitoring/detector_419.py:99` → realny Warsaw; ujednolicić peak 12-14/18-20 → 11-14/17-20 w `auto_proximity_classifier._peak_window_for`.
- Z-18: V325 hard-skip nowych — serializować flagę zamiast score=−1e9 (czystość analityki).
- Z-21: SHIFT_NOTIFY sub-flagi sprzątnąć; `_meta.cap_override_cids` zsynchronizować albo usunąć; ujednoznacznić podwójny `r6_soft_penalty` (feasibility −3/min = martwy vs pipeline −8/min = żywy) przez rename.
- Z-22: decyzja wave_scoring.py — usunąć (FILOZ-4 robi BUG-2+R-09) albo wpiąć; dziś martwa obietnica.
- Z-17: katalog 21 reguł w KB → kolumna „w kodzie: plik:linia / emergentne / martwe / OFF-by-directive".
- Z-13: ASSIGN z Telegrama — przeliczać `time` z `eta_pickup_utc` w momencie kliku + odrzucać kliki w propozycje starsze niż 10 min (re-assess). (Mała zmiana w telegram_approver → wymaga ACK na restart telegrama — zaplanować przy innej okazji restartu.)
- H-SECRETS (2026-06-10): przemianować `.secrets/panel.env` → `gastro.env` (zawiera login bota do gastro.nadajesz.pl `adrian.czapla@epaka.pl`/`adrian`, NIE do panelu admina — myląca nazwa wprowadziła w błąd przy odzyskiwaniu dostępu 10.06). Zaktualizować konsumentów (`grep -rl panel.env` w scripts/ — panel_client/most). Przy okazji: hasło bota gastro `adrian` jest słabe — rozważyć rotację, jeśli gastro na to pozwala.
- H-PANEL-PW (2026-06-10): hasło admina `gps.nadajesz.pl/admin` zresetowane ad-hoc (oba konta `ziomek_admin`: `ac@nadajesz.pl` + `admin@ziomek.pl`). Docelowo: (a) Adrian zmienia na własne przez `change-password` po pierwszym logowaniu; (b) udokumentować w memory/sekrecie KTÓRE konto jest kanoniczne + endpoint resetu, żeby następna sesja nie zgadywała; (c) usunąć/wyłączyć nieużywane konta seed demo (`owner@demo.pl`/`manager@demo.pl`/`staff@demo.pl` jeśli istnieją na prodzie).

---

## ☀ WERYFIKACJA PORANNA 11.06 (po lunch peaku 11-14 Warsaw; ~5 min, bez sesji albo na start kolejnej)

1. **E0/E1:** AUTO > 0 i `parser_degraded=0` w auto_route_reason (komenda w ETAPIE 0 wyżej); watchdog R16 NIE wysłał alertu.
2. **E2:** rozkład `best_not_score_top` w nowych decyzjach (jeśli >30% propozycji — ważny sygnał do E7, nie bug; wczoraj na replayu best≠score-top w 68% decyzji).
3. **E3:** briefing 06:00 UTC przyszedł z sekcjami „Acceptance" + „Naruszenia restauracji 7d"; `grep -c PANEL_AGREE /root/.openclaw/workspace/dispatch_state/learning_log.jsonl` rośnie (wczoraj wieczorem: 1).
4. **E4:** świeże decyzje shadow wyglądają jak wczoraj (te same flagi z konstrukcji); PIERWSZA czasówka dnia ma w eval metryki pełnego silnika (`fail12_*`/`a2_reliability_delta` gdy dotyczy); `tools/flag_fingerprint.py` nadal 4×identyczny. (Wieczorem 10.06 po flipie: 1 decyzja shadow — normalna; 0 czasówek — czekamy na poranne.)
5. **E6:** `restaurant_violations.jsonl` przyrasta bez duplikatów (wczoraj 110 wpisów / 110 oid).
6. **E5 (jeśli KROK 1 shadow już wszedł):** wpisy `would_assign` w czasowka_eval_log — po 24-48h raport kalibracyjny → ACK progów → flip T-50.

---

## METRYKI SUKCESU CAŁOŚCI (sprawdzać tygodniowo, dane z shadow_decisions + E3)
1. AUTO/dzień > 0 i rosnące; parser_degraded=0 w auto_route_reason.
2. Acceptance-rate (PANEL_AGREE / (AGREE+OVERRIDE)) — baseline po E3, cel trend ↑.
3. % czasówek przypisanych przed T-40: 0% → ≥30%.
4. Udział `gps` w pos_source best: 18% → ≥60%.
5. TIMEOUT_SUPERSEDED/14d: 439 → spadek (propozycje na tyle dobre i świeże, że są używane).
6. R6-breach rate w propozycjach best_effort: monitor (nie pogorszyć przy E7).
