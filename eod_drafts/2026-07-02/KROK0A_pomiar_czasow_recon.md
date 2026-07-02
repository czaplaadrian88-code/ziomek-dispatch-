# Krok 0a — „Pomiar Czasów 2.0": recon + narzędzie

_2026-07-02 · read-only · sesja 0a roadmapy_

## WERDYKT: DANE WYSTARCZAJĄ — narzędzie zbudowane i zwalidowane

Da się policzyć segmentowany błąd ETA ZE ZNAKIEM, **rozdzielnie dla dwóch nóg**
(odbiór + dostawa), na świeżym oknie po 28.06. Nie trzeba dokładać loggingu do
silnika. Narzędzie: `tools/eta_truth_map.py` (read-only, CLI). Pierwszy raport:
`eod_drafts/2026-07-02/eta_truth_map_2026-06-28_to_now.md`.

## Co silnik loguje jako „obiecany czas" (recon źródeł)

Predykcja żyje w `scripts/logs/shadow_decisions.jsonl`, w planie kandydata
(`best` + `alternatives[].plan`), per zlecenie:

| Pole planu | Znaczenie | Noga |
|---|---|---|
| `plan.pickup_at[oid]` | obiecany czas ODBIORU (UTC aware) | ODBIÓR |
| `plan.per_order_delivery_times[oid]` | obiecany czas jazdy od odbioru (min, anchor-free) | DOSTAWA |
| `plan.predicted_delivered_at[oid]` | obiecany bezwzględny czas dostawy (UTC) | (łączy obie) |

Prawda (ATA) w `scripts/logs/sla_log.jsonl`: `picked_up_at`, `delivered_at`
(NAIVE = czas Warszawy — przycisk kuriera), `delivery_time_minutes`,
`courier_id`, `restaurant`, `was_czasowka`, `sla_ok`.

Tier kuriera: `best/alt.v326_speed_tier_used` = **gold / std+ / std / new**
(nie ma dosłownie „slow"; `cs_tier_label` jest prawie zawsze null — nie używać).
Obciążenie floty: `rec.pool_feasible_count` (top-level shadow). Pora dnia:
godzina Warszawa z realnego `picked_up_at`.

Dopasowanie predykcji do REALNEGO kuriera (nie do `best`) — jak
`eta_calibration_logger v2`: szukamy w puli kandydatów kuriera == realny i
bierzemy JEGO plan. To jedyna wiarygodna metryka błędu modelu (realny ≠ best
w ~73% przypadków).

### TZ (kluczowa mina, obsłużona)
`sla_log` stemple NAIVE = Warszawa; `plan.*` = UTC aware. Parsowanie przez
KANONICZNY `ledger_io.parse_sla_ts` (naive→Warsaw→UTC). Gdyby czytać naive jako
UTC — +2h fałszu (znany near-miss L1.2). Logi czytane rotation-aware przez
`ledger_io.iter_sla` / `iter_shadow_decisions` (naiwny odczyt żywego pliku gubi
~29% okna 7 dni przez logrotate).

## Pokrycie okna po 28.06 (uczciwe n)

| Plik | Rekordów >= 28.06 | Uwaga |
|---|---|---|
| `sla_log.jsonl` (ATA) | 1035 | truth odbiór+dostawa |
| `shadow_decisions.jsonl` (predykcje) | ~1054 best | pickup_at 95% / deliv 95% / tier 86% |
| `eta_calibration_log.jsonl` | 1033 (558 matched) | gotowy join TYLKO nogi dostawy, bez tieru/load |
| `gps_delivery_truth.jsonl` | jest | fizyczna dostawa (button +~4 min) — opcjonalna korekta |
| `restaurant_dwell.json` | jest (do 08.06+) | fizyczny odbiór z geofence — opcjonalna dekompozycja poślizgu |

**Join committed↔ATA per noga per segment: 558 zleceń dopasowanych** (z 1037 w
oknie; 407 realny kurier poza pulą kandydatów = niedopasowane/pominięte,
56 czasówek wykluczonych, 16 bez shadow). Nogi: odbiór 553, dostawa 553.

Precomputowany `eta_calibration_log` pokrywa TYLKO nogę dostawy i **nie ma**
tieru ani pool_feasible — dlatego narzędzie buduje własny join ze źródła
(sla + shadow), a nie z tego loga.

## Narzędzie `tools/eta_truth_map.py`

```
/root/.openclaw/venvs/dispatch/bin/python tools/eta_truth_map.py \
    --since 2026-06-28 [--until ...] [--min-n 20] [--include-czasowka] [--out plik.md]
```

- Znak: **minus = OPTYMIZM silnika** (odbiór później / dostawa dłużej niż
  obiecano). To ODWROTNY znak niż `eta_error_min` w `eta_calibration_logger`.
- Noga ODBIORU = `plan.pickup_at[oid] − sla.picked_up_at`.
- Noga DOSTAWY = `plan.per_order_delivery_times[oid] − sla.delivery_time_minutes`
  (obie strony = minuty od odbioru → anchor-free, bez mieszania stref).
- Segmenty (mediana ze znakiem, p10, p90, n): tier · solo/bundle · bag_size ·
  obciążenie (pool_feasible) · pora (bucket) · godzina · kurier (top25) ·
  restauracja (top25). Segment z n < --min-n → „ZA MAŁO DANYCH" bez liczby.
- Czasówki domyślnie wykluczone (hold pod restauracją zaburza nogę odbioru).

### Walidacja (anti-lie)
Noga dostawy z narzędzia (n=553, med +1.3, p10 −16.6, p90 +17.2) zgadza się co
do 0.x z niezależnym gotowym joinem `eta_calibration_log` (n=550, +1.3, −17.0,
+17.2). Różnica n (553 vs 550) = szersze okno shadow (−3h) + rotation-aware.

## Kluczowe liczby z pierwszego przebiegu (okno 28.06→02.07, min-n=20)

**Noga ODBIORU: mediana −3.6 min (p10 −18.6, p90 +7.4), n=553.**
Silnik systematycznie OPTYMISTYCZNY co do czasu odbioru; ogon lewy gruby
(p10 −18.6 = odbiory ~19 min później niż obiecane).
- tier: new −6.4 / std −4.6 / std+ −3.6 / gold −2.3 (im „gorszy" tier tym większy optymizm)
- solo −6.0 vs bundle −3.2
- obciążenie: ciasno<=3 **−5.1** vs duża pula>=10 −1.3 (optymizm rośnie pod scarcity — spójne z kalibracją 29.06)
- pora: shoulder −5.0 > peak −2.7
- kurierzy skrajni: 413 **−15.1**, 515 −9.6, 492 −7.0 (vs 447/370/509 ~ −1.0)

**Noga DOSTAWY: mediana +1.3 min (p10 −16.6, p90 +17.2), n=553.**
Prawie bez biasu w medianie, ale OGROMNY rozrzut (±17 min) — model dostawy
niecelny, nie systematycznie optymistyczny.
- tier: gold +2.6 / std ~0 / std+ ~0 / new −6.1; segment `(brak)` tieru **+20.4** (paczki/koordynator/degraded — osobno)
- solo −1.9 vs bundle +1.9

### Wniosek dla roadmapy
Systematyczny optymizm siedzi w **nodze ODBIORU** (poślizg dojazdu, rośnie z
obciążeniem i dla wolniejszych tierów) — potwierdza diagnozę kalibracji 29.06
(„poślizg odbioru ~18 min", nie jazda). Noga dostawy = wysoka wariancja bez
biasu → cel to zwężenie rozrzutu, nie przesunięcie mediany. To wejście do kroku
kalibracji buforu LOAD-AWARE (osobny temat, protokół + ACK).

## Pliki utworzone
- `tools/eta_truth_map.py` — narzędzie (read-only, powtarzalne)
- `eod_drafts/2026-07-02/eta_truth_map_2026-06-28_to_now.md` — pierwszy raport
- `eod_drafts/2026-07-02/KROK0A_pomiar_czasow_recon.md` — ten dokument

Zero zmian w silniku / flags.json / systemd / dispatch_state. Git commit = sesja główna.
