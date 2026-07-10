# ETA, SLA i czas

## Kontrakty

- R6: 35 minut w normalnym trybie; 40 tylko alarm, nie klasa kuriera.
- R27: committed pickup chroniony; semantyka ±5/±10 wymaga jawnej decyzji.
- R-DECLARED-TIME: nie wolno obiecać odbioru przed zadeklarowaną gotowością.
- Czas obliczeń UTC; granice panelowe i prezentacja jawnie Europe/Warsaw.

## Stan dowodów

Sprint 3 zbudował lepsze lineage ETA, ale KPI fizycznego pickup/handoff pozostaje
`unbound`. `last_inside` nie jest wyjazdem z restauracji, a arrival pod adresem
nie jest handoffem klientowi. Wyniki button-truth nie powinny awansować do
fizycznego KPI przez zmianę nazwy.

Odzyskany audyt wskazał `ALGO-01` (champion gate może tolerować pogorszenie) i
`ALGO-02` (cechy pickup oparte o faktyczny czas realizacji). Oba są P2 z review,
ale przed zmianą modelu wymagają oddzielnej reprodukcji leakage na tym samym
support.

## Najważniejszy konflikt

Furtka gold p80 jest technicznie żywa i wpływa na feasibility. Kanon mówi o jej
usunięciu; to nie jest automatyczna zgoda na flip. Najpierw karta wpływu,
oddzielenie już-niesionego od nowego naruszenia i rollback hot.
