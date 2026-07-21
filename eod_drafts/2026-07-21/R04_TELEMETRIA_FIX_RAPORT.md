# R-04 telemetria — raport 2026-07-21

## Werdykt

Źródło ślepoty potwierdzone: evaluator zapisuje
`tier_suggestions[courier_id]`, lecz serializer pobierał identyfikator z
`Candidate.metrics`, gdzie klucza nie ma. Bazowy HEAD klonu zawiera już właściwy
fix `c5aa203`: A (`_serialize_candidate`) używa `c.courier_id`, B (inline best)
używa `best.courier_id`. Nie dublowano zmiany produkcyjnej.

R-04 v2 to peak-quality/graduation schema. Dla tieru `new` graduation wymaga
łącznie: ≥14 dni, ≥50 peak deliveries/30d, ≥5 peak active days/30d. Producentem
jest `r04_evaluator`, konsumentem telemetrii `shadow_dispatcher`; scoring,
feasibility, selekcja, `rule_verdict` i bramkowany `r04_apply` są N-D i
nietknięte. `docs/REGULY` nie istnieje; sprawdzono bieżący kod, master KB,
kanon pamięci i `ZIOMEK_LOGIC_REFERENCE`.

## Dowody

- Fixture-first + guard A/B + mutant (`courier_id` znów z metrics): 3/3 PASS;
  mutant odrzucony przez `SerializerIdentityError`.
- Read-only replay `/tmp/d1ep_data/shadow_decisions.jsonl`, ostatnie 719:
  teraz 91 filled, 585 null, 43 missing; po poprawnym lookupie 312/719 filled,
  czyli +221. Mapa 14 CID została odtworzona z niepustych payloadów snapshotu;
  to projekcja kontrfaktyczna, nie historycznie dokładny replay (brak
  równoczesnego `tier_suggestions.json`). Zero mismatchów tożsamości.
- Compile nowych plików: PASS. Pełna suita: świadomie CTO; kanoniczny venv jest
  niewykonywalny w tym sandboxie (Permission denied), nie zastępowano go
  systemowym Pythonem jako wyniku kanonicznego.
- DoD mechaniczny: HOLD wyłącznie na pełnej regresji. Commit: HOLD (`.git`
  read-only); przekazano `/tmp/r04_telemetria_fix.patch`.

Zmiana jest telemetry/read-only; nie zmienia decyzji. Nie było flag, migracji,
deployu ani restartu. Rollback: revert lokalnego commita; fix źródłowy pozostaje
w bazowym `c5aa203`.
