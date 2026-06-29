# Kalibracja Ziomka replayem — 2026-06-29 (trwały snapshot)

Zabezpieczone ze scratchpada (ulotny). Pełny kontekst + werdykty: memory `ziomek-calibration-2026-06-29.md`.

## Cel
Adrian: „dokładny replay na różnych dniach, dopasować do tierów/wielkości worka, segment PO OBCIĄŻENIU floty (nie po porze)". Dwa tory + fundament.

## Pliki
- `decisions_outcomes_loadbucketed.jsonl` — FUNDAMENT, 2827 zam, 14 dni (06-15..28), 1 wiersz/order_id, TZ→UTC, join 7 logów, kubełek obciążenia (pool_feasible). Schemat: `README_decisions_outcomes_loadbucketed.md`.
- `cell_counts.txt` — liczność komórek load×tier×worek.
- `track1_prediction_report.md` — PREDYKCJA. Sekcje: ★06-29 (prawdziwe `served`), RECONCILIATION (P1a-P3), per-dzień, okno, R6.
- `track2_weights_report.md` — WAGI (kierunkowo) + spec instrumentu `weight_shadow`.
- `build_dataset.py` / `analyze*.py` / `finalize.py` / `track1_*.py` / `track2_*.py` — skrypty (read-only, odtwarzalne).

## GŁÓWNE WNIOSKI (read-first)
1. **LOAD > CLOCK** — obciążenie floty (pool_feasible) tłumaczy ~2× więcej błędu ETA niż godzina; niezależne. Segment: luzno pf≥5 / srednio 2-4 / ciasno ≤1.
2. **★ PRZYCZYNA OPTYMIZMU = POŚLIZG ODBIORU, NIE JAZDA.** Wobec realnej bazy obietnicy (`predicted_delivery_min`) noga jazdy ~0 błędu; kurier dojeżdża po odbiór ~18min później niż silnik założył (kolejkowanie pod obciążeniem). Wcześniejsze „jazda ~2× OSRM" = artefakt złej kolumny (`predicted_drive_min`=surowy OSRM, nie zasila obietnicy). Fix = load-aware bufor ODBIORU lub re-anchor ETA przy realnym odbiorze. = engine change → protokół+ACK.
3. **Bufor LOAD-AWARE, nie stały.** 06-29 (high-load, prawdziwe `served`): clean-bundle srednio +12.7 / ciasno +19.1; SOLO ~2×: +25-30. Spokojne dni: ~+5. Pooling worków zawyża (route-resequencing — planowo-ostatni stop dowieziony wcześnie). SOLO najgorsze = nie-do-zbundlowania → zajęty kurier → czekanie na odbiór.
4. **Pesymistyczne dni Adriana = REALNE:** (a) worki resequencing (20-46% dowiezione wcześnie), (b) baza ETA zmieniła się ~06-23 (surowy OSRM→uplift kalibracji). Mediana dzienna znaku nie zmienia, ale masa pesymistyczna realna.
5. **WAGI w zasadzie OK.** Pod ciasną flotą zło = WYMUSZONE podażą, nie wagami/progiem. Jedyny kierunkowy trop: idle-wait v3273 lekko za słaby (czyste solo). ŻADEN flip — instrument `weight_shadow` dozbiera prawdę.
6. **R6 breach rośnie z load** (06-29 ALL 20.4%, solo-ciasno 28%). Zaostrzanie progu przyjęcia NIE pomoże (breach nie przy progu) — tylko uczciwszy czas odbioru.

## Pewność
Mechanizm+kierunek = HIGH. Dokładne minuty high-load = MED (06-29=1 dzień, dozbierać więcej dni wysokiego obciążenia). Wagi = LOW/kierunkowo.
