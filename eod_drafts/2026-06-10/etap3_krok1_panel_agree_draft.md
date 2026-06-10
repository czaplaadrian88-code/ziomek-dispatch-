# ETAP 3 / Krok 1 — PANEL_AGREE w panel_watcher.py (draft pre-edit)

## Cel (Z-03)
Zgodne przypisanie panelem (koordynator daje TEGO SAMEGO kuriera co best propozycji)
dziś NIE zostawia śladu — `_check_panel_override` (panel_watcher.py:173) robi
`return` przy zgodności. Logowane są tylko rozjazdy (PANEL_OVERRIDE). Dodajemy
lustrzany PANEL_AGREE — czysta telemetria, zero zmian scoringu/feasibility.

## Mechanizm istniejący (przeczytany, kopiujemy dyscyplinę)
- `_check_panel_override(oid, cid, source)` wołany w 3 miejscach po NON-DUPLICATE
  emit COURIER_ASSIGNED: panel_initial (:877), panel_diff (:984), panel_reassign (:1015).
  Ścieżki packs_fallback/coldstart NIE wołają override → AGREE też nie (symetria,
  inaczej bias akceptacji w górę).
- KOORDYNATOR (cid=26) odfiltrowany już na emit-sites (:853 `is_koordynator`,
  :966/:998 `!= KOORDYNATOR_ID`) → edge (a) strukturalnie załatwiony; w nowej
  funkcji dodatkowy defensywny guard.
- pending_proposals[oid] NADPISYWANE najnowszą propozycją (telegram_approver
  proposal_sender) → edge (b) „porównuj z OSTATNIĄ" załatwiony strukturalnie.
  Rekord ma `sent_at` (UTC iso) + `expires_at` (+5 min) + `decision_record`.
- Zapis: `dispatch_v2.core.jsonl_appender.append_jsonl` (O_APPEND + flock LOCK_EX,
  MP-#11) — ta sama dyscyplina co OVERRIDE i telegram_approver.
- Telegram ASSIGN (edge c): telegram_approver POPUJE pending PRZED tym, jak panel
  pokaże przypisanie, i loguje ASSIGN_DIRECT (`chosen_courier_id` +
  `proposed_courier_id` + pełny `decision`). panel_watcher widzi potem
  COURIER_ASSIGNED bez pending → tail-scan learning_log (cap 256 KB) za świeżym
  (≤15 min) ASSIGN_DIRECT dla oid; `chosen==proposed==panel_cid` → AGREE
  source="telegram". ASSIGN w alternatywę (chosen≠proposed) → nic nowego
  (ASSIGN_DIRECT zostaje jedynym śladem; OVERRIDE nie ruszany).
  TAK-button → action=TAK już liczony w starym agreement; spec nie każe dublować.

## Nowy kod (panel_watcher.py, obok _check_panel_override)
- `_PANEL_AGREE_MAX_AGE_MIN` = env `PANEL_AGREE_MAX_PROPOSAL_AGE_MIN` default 15.
- `_parse_iso_utc(ts)` — defensive iso→aware UTC.
- `_find_recent_assign_direct(oid)` — tail 256 KB learning_log, substring
  pre-filter, newest-first, ts ≤15 min.
- `_check_panel_agree(oid, panel_cid, source)`:
  1. flaga `ENABLE_PANEL_AGREE` (flags.json hot-reload; default z env
     `ENABLE_PANEL_AGREE` ≠ "0" → ON; kill-switch = env 0 albo flags.json false).
     Celowo BEZ zmian w common.py (brudny WIP Sesji 2).
  2. guard cid==KOORDYNATOR_ID → return.
  3. pending[oid] jest: proposed==panel_cid AND wiek sent_at ≤15 min → zapis;
     rozjazd/staro → return (OVERRIDE path nietknięty).
  4. pending brak → ścieżka telegramowa (wyżej).
- Rekord (schemat zgodny z PANEL_OVERRIDE — `proposed_courier_id`/
  `actual_courier_id`, dzięki czemu sequential_replay.build_roster łapie cidy
  bez zmian; nazwy z brief'u proposed_cid/assigned_cid mapują się 1:1):
  ts, order_id, action=PANEL_AGREE, proposed_courier_id, actual_courier_id,
  latency_s (sent_at→teraz; dla telegram: decision.ts→ASSIGN ts),
  proposed_score, proposal_verdict, restaurant, proposed_tier (best.dwell_tier,
  fallback v319h_bug4_tier_cap_used.split('/')[0]), pickup_ready_at,
  order_created_at (→ czasówka/elastyk + peak w raporcie kroku 3 bez
  embedowania pełnego decision — NIE bloatujemy learning_log),
  source ("panel"|"telegram"), panel_source (panel_initial/diff/reassign).
- Wszystkie błędy I/O → warning, nigdy nie propagują (zdrowie watchera > telemetria).
- Call-sites: po każdym `_check_panel_override(...)` w 3 miejscach.

## Testy
`tests/test_panel_agree.py` (pytest, wzór smoke_panel_override + lekcja #180/sesja
ta: patch `_LEARNING_LOG_PATH` + `_PENDING_PROPOSALS_PATH` na tmp_path, ZERO
zapisu do żywych plików):
1. zgodny cid + świeża propozycja → 1× PANEL_AGREE z poprawnymi polami
2. rozjazd cid → brak AGREE (zostaje dla OVERRIDE)
3. propozycja starsza niż 15 min → brak AGREE
4. cid koordynatora (26) → brak AGREE
5. flaga OFF (env kill-switch) → brak AGREE
6. brak pending + świeży ASSIGN_DIRECT chosen==proposed==cid → AGREE source=telegram
7. brak pending + ASSIGN_DIRECT w alternatywę (chosen≠proposed) → brak AGREE
8. brak pending + stary ASSIGN_DIRECT → brak AGREE
9. brak pliku pending → graceful (ścieżka telegramowa, brak ASSIGN_DIRECT → nic)
10. latency_s policzony z sent_at
+ regresja: smoke_panel_override (6 case) bez zmian zachowania.

## Workflow
cp panel_watcher.py .bak-pre-etap3-panel-agree-2026-06-10 → edit → py_compile →
import → pytest (nowe + smoke + test_v319b_panel_watcher_hooks) → commit TYLKO
panel_watcher.py + tests/test_panel_agree.py → tag
`panel-agree-loop-2026-06-10`. Restart panel-watchera DOPIERO w kroku 5
(skoordynowany z Sesją 2 — jej WIP w common.py/dispatch_pipeline.py).
