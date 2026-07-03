# WERDYKT perf po flipie ENABLE_PERF_LAZY_MEMBERS (at-207 + dociąg peaku) — 2026-07-03 ~11:15 UTC

**Zadanie:** tmux14 zad. 1 (handoff 03.07) — pomiar na żywym oknie po flipie PERF_LAZY (LIVE od 03.07 ~00:25, restart shadow czysty).
**Sędzia (baseline 14d, `FALA1_perfslo_raport.md` §1):** całość p50 852 / p95 1939 / ogon>1500 = 13,1%; peak p50 857 / p95 1847; off-peak p50 638 / p95 1668. Replay obiecywał −22% p50 (dolna granica).
**Pomiar:** `tools/perf_budget_report.py --since 2026-07-03T00:30` — 2 biegi: at-207 09:05 (n=19, przed peakiem, SLO 🟢) + dociąg 11:13 (n=60, w tym peak n=42). Raporty: `perf_budget_report_0905utc.{txt,json}` + bieg 11:13 poniżej.

## Liczby (okno 00:30→11:13, n=60, wszystko po-flipowe)

| metryka | baseline 14d | dziś po flipie | Δ |
|---|---|---|---|
| całość p50 | 852 ms | **620 ms** | **−27%** |
| całość p95 | 1939 ms | **1551 ms** | **−20%** |
| ogon >1500 ms | 13,1% | **6,7%** | **−49%** |
| peak p50 (n=42) | 857 ms | **710 ms** | **−17%** |
| peak p95 (n=42) | 1847 ms | **1810 ms** | **−2%** ⚠ |
| off-peak p50 (n=18, < min_n) | 638 ms | 568 ms | −11% |
| off-peak p95 (n=18, < min_n) | 1668 ms | 765 ms | −54% |

## Werdykt: 🟢 POPRAWA REALNA — flaga ZOSTAJE ON (zero rollbacku)

1. **Live potwierdza replay i go przebija na p50** (replay −22,4% offline; live −27% całość). Ogon >1500 ms spadł o połowę. Kierunek i skala = zgodne z diagnozą (stat() flags.json + load_plan per-kandydat).
2. **⚠ Peak p95 prawie nietknięty (1810 vs 1847)** — ogon W PEAKU ma inne źródło niż narzut IO zdjęty przez PERF_LAZY (kandydaci: compute pod obciążeniem — OR-Tools/OSRM/liczba kandydatów). To jest NASTĘPNY cel osobnej fali perf, nie powód do rollbacku.
3. **SLO §5a:** dziś łamany tylko peak (p50 710>700 marginalnie; p95 1810>1500). Off-peak poniżej min_n=20, ale trend p95 765<<900 = w budżecie.

## Rekomendacja flipu `ENABLE_PERF_SLO_ALERT` (decyzja Adriana)

Flip TERAZ = alert od razu w breach (peak p95) — edge-triggered, więc 1 alert wejścia + przypomnienia, nie spam. Dwie sensowne opcje:
- **(a) flip po pełnym dniu** (re-run raportu po peaku ~13:00+ UTC i z pełną dobą) — czystszy sygnał;
- **(b) flip od razu** świadomie: alert peak-p95 działa jako żywy tracker pozostałego regresu ogona.
Rekomendacja: **(a)** — log-only do pełnej doby po flipie, flip jutro rano z ACK; breach peak-p95 i tak jest już zapisany tu i w trackerze.

## Zastrzeżenia pomiaru
- n=60 (jedna niepełna doba); peak niedokończony w chwili pomiaru (11:13 UTC, peak 09-12). Miarodajny re-run: `--since 2026-07-03T00:30` po 12:00 UTC / jutro.
- Godziny 10-11 UTC mają ogon 14-16% — cały dzisiejszy ogon siedzi w peaku (spójne z pkt 2 werdyktu).
