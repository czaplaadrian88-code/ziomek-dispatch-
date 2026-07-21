# Certyfikacja deterministyczności golden replay — PASS (2026-07-21)
Fix `b9bce44e` (det-budget + sekwencyjny replay + capture fail-closed) na masterze; HEAD=00be30ba.
Dwa niezależne biegi (before=after=HEAD, korpus od 2026-07-21T09:02Z): **PARITY / PARITY**,
cross_differences_n=0/0, unstable 0/0, errors/input_missing/osrm_miss puste, corpus n=10
(truncated=false), SHA korpusu identyczny w obu biegach; raporty różnią się wyłącznie polem
`generated_at`. Warunki akceptacji z tools/GOLDEN_DECISION_REPLAY.md spełnione w komplecie.
⚠ Zalecenie: powtórka na korpusie po peaku (n≥50) dla mocniejszej próby — formalna bramka
spełniona już teraz. Odblokowane: tor replayowy nauki (#22 krok 3) + bounded-replay tie-breaka.
Biegi: cert_run1.json + cert_run2.json (ten katalog).
