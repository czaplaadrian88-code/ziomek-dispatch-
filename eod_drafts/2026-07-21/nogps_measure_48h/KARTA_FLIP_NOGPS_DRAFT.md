# KARTA FLIPA — `ENABLE_NO_GPS_NEUTRAL_SCORE_DIST` (v3) — DRAFT 21.07 ~07:00 UTC

**Status: DRAFT na oknie częściowym 33h (19.07 23:39:21Z → 21.07 07:00Z).**
Finalne liczby: at#221 (21.07 23:45 UTC) → `report_final_48h.json` + `run_output_final_48h.txt` w tym katalogu.
Bramka i progi: `eod_drafts/2026-07-20/nogps_measure/GO_NO_GO.md` (flip WYŁĄCZNIE v2/v3, WYŁĄCZNIE za ACK Adriana; rekomendacja przed ACK = cross-check z Solem — [[feedback-crosscheck-sol-before-ack-2026-07-20]]).

## Wynik częściowy (n=192 decyzji, 60 z telemetrią w puli, 81 kandydatów z telemetrią)

| Kryterium (GO_NO_GO) | Próg | Zmierzone (33h) | Ocena wstępna |
|---|---|---|---|
| §3 wolumen | ≥300 decyzji/48h | 192/33h → projekcja ~280 | ⚠ PONIŻEJ — wydłużyć okno |
| §4 gap_on (kontrfaktyczny, kanon unknown) | ≤10pp (i ≤0.35×gap_off) | winner-share ON 78.3% vs pool 12.6% → **~65.7pp** | 🔴 NO-GO trajektoria |
| §4 would_flip_winner | (informacyjne) | **1.7%** (1/60) | delta za mała vs marginesy |
| §5 mixed-pool win-rate | 40–60% | ~80% OFF (kontrfakt ON ≈ bez zmiany) | 🔴 poza pasmem |
| §5b donor_filter_match_rate | ≥99% (twardy gate) | **98.3%** (57/58 mierzalnych; coverage 96.7%) | ⚠ 1 rozjazd — patrz niżej |
| §5a post_wave residual | ≤2× baseline (≤3.8%) | 2.4% target wins (1/42) | 🟢 OK |
| §7 KOORD-rate | nie rośnie >2pp po flipie | 7.8% przy OFF (ambient; flaga OFF ⇒ nie od kandydata) | obserwacja |
| §7 'cisza' rate | każde wystąpienie = sprawdź | 1/193 (0.5%) przy OFF ⇒ nie od kandydata | odnotowane |

## Kluczowa obserwacja merytoryczna

Neutralizacja DYSTANSU działa (telemetria: raw_km med. 1.36 → neutral med. 3.49; delta score med. **−6.6 pkt**), ale mediana marginesu zwycięzcy to **~45 pkt** — komponent dystansowy to za mało, żeby odwrócić zwycięzców. Kurierzy unknown-position wygrywają na innych komponentach score (pusty worek: s_obciazenie/s_kierunek/s_czas ~baseline). Kontrfaktyczne winner-share unknown przy ON ≈ 78.3% — praktycznie bez zbieżności do pool-share.

**Wniosek roboczy:** sam fix dystansu jest poprawny wewnętrznie, ale NIE domyka regresji EQUAL_TREATMENT — gap zostaje. Jeśli finał 48h to potwierdzi: NIE flipować „dla zasady" (flip nic nie zmieni w wyborach, a dodaje ruchomą część); wrócić do diagnozy pozostałych komponentów score dla pustego worka bez pozycji (osobny kandydat, protokół #0).

## Rozjazd donor-filter (489065) — analiza w toku

Silnik: neutral_km=2.53; formuła narzędzia z serializowanych kandydatów: donorzy {1.78, 2.53, 2.91, 5.9} → 2.72. Wszyscy `feasibility=MAYBE`, `synth=False`. Dwóch donorów wg narzędzia ma sentinel score −1e9 (hard-cap). Hipoteza: artefakt pomiaru (silnik widzi inne km / inny zbiór w chwili passu niż serializacja), nie defekt filtra — werdykt po read-only analizie kodu (subagent), dopisać tu przed finałem.

`synth=None` ×3: 1× pusty rekord KOORD (bez best), 2× cid=540 bez `km_to_pickup` (nie mogą być donorami) — łagodne.

## Decyzja

- [ ] Finał 48h (at#221) — liczby do tabeli powyżej
- [ ] Werdykt donor-pass (kod) — artefakt vs defekt
- [ ] Cross-check rekomendacji z Solem
- [ ] Rekomendacja CTO → Adrian (flip TYLKO za osobnym ACK; przy NO-GO — propozycja kolejnego kroku)

Rollback (gdyby po ewentualnym flipie): `flags.json` → `ENABLE_NO_GPS_NEUTRAL_SCORE_DIST=false`, hot-reload, bez restartu.
