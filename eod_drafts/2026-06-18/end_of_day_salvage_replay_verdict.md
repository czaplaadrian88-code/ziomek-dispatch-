# End-of-day salvage (#2) — replay verdict 2026-06-18

## Ograniczenie danych (dlaczego nie ma backward-replay rescue-count)
- `obj_replay_capture.jsonl` = route-oriented (bag/pos/dwell/new_order) — BRAK per-courier shift_end/shift_start.
- shadow_decisions.jsonl loguje TYLKO feasible kandydatów; odrzuceni (PICKUP_POST_SHIFT itd.) nie są serializowani.
- → nie da się policzyć wstecz, kto był odrzucony na regule końca zmiany z pickup≤close.

## Census okna (ostatnia godzina pracy firmy; /tmp/salvage_census.py)
- ~2 tyg. logów: 28 decyzji w oknie (1-3/dzień, niski ruch).
- 22 PROPOSE / 6 KOORD (brak feasible = cel salvage) / 4 best_effort.
- Poza oknem: salvage INERTNY z konstrukcji (guard [close-60,close]).

## Ocena ryzyka
- Fail-safe: relaks bramek końca-zmiany TYLKO gdy pickup≤company_close; R6/pickup_too_far/bag_cap zostają.
- Wąski zakres (6 KOORD/2tyg), mały blast radius. Alternatywa dla salvage = KOORD (zlecenie wisi przy końcu dnia).

## Rekomendacja
Backward-replay niemożliwy (luka danych). Dwie ścieżki:
(a) FORWARD-SHADOW: telemetria „would_salvage" liczona przy fladze OFF, zbiórka ~1-2 tyg → twarde liczby → flip. Measurement-first, ale wolne (6 przypadków/2tyg).
(b) FLIP + MONITOR: mały/wąski/fail-safe → flip flaga true + restart dispatch-shadow, monitoruj `end_of_day_salvage` w shadow_decisions kilka dni. Szybkie.
Decyzja Adriana.
