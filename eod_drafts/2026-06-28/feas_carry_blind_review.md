🔎 Przeglad shadow B2 (feas_carry_blind / P-6) — okno 2026-06-25..2026-06-28
Co sie dzieje: bramka feasibility 'wybacza' najgorszy breach niesionego i wycina lepszego kuriera (root #483000); shadow mierzy jak czesto warto by przekierowac.

FRESH (od 26.06, n=669): redirect 55.6% | regret med 9.6/sr 750.5/p90 95.8 min | marginal 92 | {'r6_new': 247, 'sla': 125}
CUMULATIVE (n=967): redirect 53.3% | regret sr 545.5 min | {'sla': 179, 'r6_new': 336}
BASELINE 25.06 (n=178): redirect 38,2% | regret sr 9,0 min | r6_new 42 / sla 26

WERDYKT (na FRESH): ✅ SYGNAL TRZYMA -> REKOMENDUJE budowe fixu B2 (unifikacja bramki feasibility: carry-inclusive PRIMARY + gradient + new-order cap) PRZEZ protokol ziomek-change-protocol.md + Twoj ACK (shadow-first, replay, pelna regresja).
Co robisz: jesli ✅ -> daj ACK na build (przez protokol). Raport: eod_drafts/2026-06-28/feas_carry_blind_review.md
