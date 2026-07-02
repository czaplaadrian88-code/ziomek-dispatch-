# Pomiar Czasów 2.0 — segmentowana mapa błędu ETA (Krok 0a)
_Wygenerowano: 2026-07-02T14:09:14.099870+02:00  ·  narzędzie: tools/eta_truth_map.py (read-only)_

## Okno i założenia
- Okno (delivered_at): **2026-06-27T22:00:00+00:00 → teraz** (UTC).
- Próg segmentu: **--min-n = 20** (segment poniżej → ZA MAŁO DANYCH).
- Czasówki: **WYKLUCZONE** (domyślnie wykluczone — hold pod restauracją zaburza nogę odbioru).
- **Znak błędu: minus = OPTYMIZM silnika** (odbiór później / dostawa dłużej niż obiecano). Uwaga: to odwrotny znak niż `eta_error_min` w eta_calibration_logger.
- Predykcja = plan REALNEGO kuriera (dopasowanie best+alternatives po courier_id), nie `best`. Niedopasowane zlecenia pominięte w metryce.
- Timestampy sla_log (naive) parsowane jako czas Warszawy → UTC przez kanoniczny `ledger_io.parse_sla_ts`. Logi czytane rotation-aware.
- Noga ODBIORU: `plan.pickup_at[oid]` − `sla.picked_up_at`.
- Noga DOSTAWY: `plan.per_order_delivery_times[oid]` − `sla.delivery_time_minutes` (obie = minuty od odbioru, anchor-free).
- Bucket obciążenia z `pool_feasible_count`: ciasno<=3 / 4-6 / 7-9 / >=10.
- Mediana wieku predykcji (shadow_ts→odbiór): 34 min.

## Pokrycie joinu (uczciwe n)
- Zleceń w oknie (sla, delivered): **1040**
- Czasówki pominięte: 56
- Bez rekordu shadow (utracone okno / brak decyzji): 16
- Realny kurier poza pulą kandydatów (niedopasowane): 409
- **Dopasowane do realnego kuriera: 559**
  - z nogą ODBIORU (pickup_err): 554
  - z nogą DOSTAWY (deliv_err): 554

## NOGA: ODBIÓR (dojazd-po-odbiór)
**Ogółem: n=554  mediana=-3.6 min  p10=-18.6  p90=+7.4**  (− = optymizm silnika)

### Wg tieru kuriera (v326_speed_tier_used)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| (brak) | 56 | +0.7 | -23.2 | +13.0 |
| gold | 92 | -2.3 | -21.7 | +3.8 |
| new | 24 | -6.2 | -21.1 | +7.1 |
| std | 134 | -4.6 | -18.6 | +6.9 |
| std+ | 248 | -3.6 | -15.8 | +6.9 |

### Wg solo vs bundle
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| bundle | 458 | -3.2 | -17.1 | +7.7 |
| solo | 96 | -6.0 | -24.5 | +4.5 |

### Wg rozmiaru baga (bag_size)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| 1 | 96 | -6.0 | -24.5 | +4.5 |
| 2 | 181 | -3.6 | -13.9 | +6.2 |
| 3 | 160 | -2.7 | -16.2 | +7.4 |
| 4 | 89 | -5.0 | -22.3 | +7.7 |
| 5 | 24 | -1.9 | -11.3 | +14.5 |
| 6 | 4 | ZA MAŁO DANYCH | — | — |

### Wg obciążenia floty (pool_feasible)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| ciasno (<=3) | 241 | -5.1 | -22.6 | +7.5 |
| duza pula (>=10) | 30 | -1.3 | -9.3 | +3.5 |
| luzno (7-9) | 113 | -2.4 | -14.1 | +7.7 |
| srednio (4-6) | 170 | -3.6 | -17.6 | +6.9 |

### Wg pory dnia (bucket)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| offpeak | 45 | -3.5 | -17.1 | +9.4 |
| peak | 301 | -2.7 | -16.4 | +7.4 |
| shoulder | 208 | -5.0 | -21.9 | +6.4 |

### Wg godziny (Warsaw)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| 10 | 16 | ZA MAŁO DANYCH | — | — |
| 11 | 32 | -4.2 | -10.0 | +4.5 |
| 12 | 52 | -5.2 | -15.9 | +5.5 |
| 13 | 58 | -1.4 | -17.8 | +8.1 |
| 14 | 48 | -5.0 | -12.4 | +3.6 |
| 15 | 50 | -3.9 | -22.8 | +7.8 |
| 16 | 42 | -7.0 | -25.6 | +5.7 |
| 17 | 49 | -1.2 | -15.4 | +7.7 |
| 18 | 56 | -6.2 | -20.4 | +7.1 |
| 19 | 54 | -1.6 | -11.2 | +6.0 |
| 20 | 52 | -4.8 | -15.7 | +4.8 |
| 21 | 26 | -2.4 | -15.5 | +9.4 |
| 22 | 9 | ZA MAŁO DANYCH | — | — |
| 9 | 10 | ZA MAŁO DANYCH | — | — |

### Wg kuriera (top 25 po n)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| 492 | 64 | -7.0 | -27.5 | +6.3 |
| 447 | 64 | -1.0 | -14.6 | +7.4 |
| 531 | 56 | -4.4 | -12.4 | +10.8 |
| 370 | 49 | -1.0 | -14.3 | +7.7 |
| 179 | 40 | -3.4 | -28.3 | +1.1 |
| 484 | 30 | -3.9 | -14.8 | +3.6 |
| 470 | 30 | -3.7 | -17.1 | +6.1 |
| 509 | 22 | -0.5 | -8.9 | +4.3 |
| 515 | 22 | -9.6 | -39.0 | +9.4 |
| 413 | 21 | -15.1 | -25.6 | +1.0 |
| 520 | 19 | ZA MAŁO DANYCH | — | — |
| 400 | 17 | ZA MAŁO DANYCH | — | — |
| 471 | 14 | ZA MAŁO DANYCH | — | — |
| 409 | 14 | ZA MAŁO DANYCH | — | — |
| 536 | 14 | ZA MAŁO DANYCH | — | — |
| 441 | 14 | ZA MAŁO DANYCH | — | — |
| 207 | 12 | ZA MAŁO DANYCH | — | — |
| 535 | 11 | ZA MAŁO DANYCH | — | — |
| 508 | 9 | ZA MAŁO DANYCH | — | — |
| 289 | 8 | ZA MAŁO DANYCH | — | — |
| 500 | 8 | ZA MAŁO DANYCH | — | — |
| 376 | 8 | ZA MAŁO DANYCH | — | — |
| 537 | 4 | ZA MAŁO DANYCH | — | — |
| 123 | 3 | ZA MAŁO DANYCH | — | — |
| 526 | 1 | ZA MAŁO DANYCH | — | — |

### Wg restauracji (top 25 po n)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| Grill Kebab | 50 | -1.4 | -19.4 | +7.7 |
| Rany Julek | 42 | -7.1 | -17.9 | +1.5 |
| Mama Thai Bistro | 37 | -3.6 | -11.7 | +6.0 |
| Miejska Miska | 29 | -6.1 | -22.6 | +0.9 |
| Sushi Rany Julek &amp; Pizza Majstry | 29 | -3.8 | -16.3 | +4.9 |
| Chicago Pizza | 23 | -2.7 | -13.4 | +5.7 |
| Sweet Fit &amp; Eat | 22 | -5.4 | -17.1 | +3.2 |
| Rukola Sienkiewicza | 20 | -6.3 | -17.0 | +5.5 |
| Pizzeria 105 Galeria Biała | 17 | ZA MAŁO DANYCH | — | — |
| Raj | 17 | ZA MAŁO DANYCH | — | — |
| Piwo Kaczka Sushi | 17 | ZA MAŁO DANYCH | — | — |
| Pani Pierożek | 16 | ZA MAŁO DANYCH | — | — |
| Kebab Król | 16 | ZA MAŁO DANYCH | — | — |
| Karczma Maciejówka | 13 | ZA MAŁO DANYCH | — | — |
| Retrospekcja | 12 | ZA MAŁO DANYCH | — | — |
| Rukola Kaczorowskiego | 12 | ZA MAŁO DANYCH | — | — |
| Pruszynka Restauracja | 11 | ZA MAŁO DANYCH | — | — |
| Goodboy | 10 | ZA MAŁO DANYCH | — | — |
| Hacienda Pizza | 10 | ZA MAŁO DANYCH | — | — |
| Street Mama Thai | 9 | ZA MAŁO DANYCH | — | — |
| Zapiecek | 9 | ZA MAŁO DANYCH | — | — |
| Toriko | 8 | ZA MAŁO DANYCH | — | — |
| Arsenal Panteon | 8 | ZA MAŁO DANYCH | — | — |
| Restauracja Kumar&#039;s | 8 | ZA MAŁO DANYCH | — | — |
| Bar Merino | 7 | ZA MAŁO DANYCH | — | — |


## NOGA: DOSTAWA (od odbioru do klienta)
**Ogółem: n=554  mediana=+1.3 min  p10=-16.6  p90=+17.2**  (− = optymizm silnika)

### Wg tieru kuriera (v326_speed_tier_used)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| (brak) | 56 | +20.4 | -8.2 | +36.5 |
| gold | 92 | +2.6 | -10.7 | +15.2 |
| new | 24 | -5.3 | -18.7 | +9.8 |
| std | 134 | +0.0 | -19.2 | +12.8 |
| std+ | 248 | +0.2 | -16.6 | +11.6 |

### Wg solo vs bundle
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| bundle | 458 | +1.9 | -17.0 | +18.4 |
| solo | 96 | -1.9 | -15.4 | +10.6 |

### Wg rozmiaru baga (bag_size)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| 1 | 96 | -1.9 | -15.4 | +10.6 |
| 2 | 181 | -0.3 | -20.6 | +11.1 |
| 3 | 160 | +2.6 | -16.0 | +15.7 |
| 4 | 89 | +4.7 | -10.7 | +25.5 |
| 5 | 24 | +10.8 | -14.7 | +29.8 |
| 6 | 4 | ZA MAŁO DANYCH | — | — |

### Wg obciążenia floty (pool_feasible)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| ciasno (<=3) | 241 | +1.5 | -18.8 | +24.4 |
| duza pula (>=10) | 30 | +0.5 | -10.9 | +9.2 |
| luzno (7-9) | 113 | +2.0 | -10.5 | +12.8 |
| srednio (4-6) | 170 | +0.4 | -17.8 | +11.2 |

### Wg pory dnia (bucket)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| offpeak | 45 | -1.5 | -20.8 | +13.9 |
| peak | 301 | +1.3 | -16.2 | +13.8 |
| shoulder | 208 | +2.6 | -15.4 | +22.9 |

### Wg godziny (Warsaw)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| 10 | 16 | ZA MAŁO DANYCH | — | — |
| 11 | 32 | +0.2 | -16.6 | +11.3 |
| 12 | 52 | -0.2 | -17.0 | +14.2 |
| 13 | 58 | +1.3 | -17.5 | +21.6 |
| 14 | 48 | +3.9 | -11.3 | +26.7 |
| 15 | 50 | +2.9 | -14.4 | +23.4 |
| 16 | 42 | +3.8 | -15.7 | +22.9 |
| 17 | 49 | +0.4 | -17.5 | +10.1 |
| 18 | 56 | +3.1 | -8.5 | +15.8 |
| 19 | 54 | +0.5 | -10.0 | +8.4 |
| 20 | 52 | +1.2 | -10.7 | +11.3 |
| 21 | 26 | -2.1 | -20.1 | +4.8 |
| 22 | 9 | ZA MAŁO DANYCH | — | — |
| 9 | 10 | ZA MAŁO DANYCH | — | — |

### Wg kuriera (top 25 po n)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| 492 | 64 | -1.1 | -17.0 | +9.8 |
| 447 | 64 | +2.7 | -8.0 | +18.4 |
| 531 | 56 | -0.7 | -21.1 | +22.4 |
| 370 | 49 | +1.2 | -16.2 | +14.2 |
| 179 | 40 | +2.6 | -9.4 | +13.6 |
| 484 | 30 | -1.1 | -18.1 | +9.7 |
| 470 | 30 | -4.6 | -34.6 | +15.6 |
| 509 | 22 | +2.0 | -10.7 | +11.0 |
| 515 | 22 | +1.5 | -13.7 | +15.9 |
| 413 | 21 | +3.0 | -10.7 | +16.3 |
| 520 | 19 | ZA MAŁO DANYCH | — | — |
| 400 | 17 | ZA MAŁO DANYCH | — | — |
| 471 | 14 | ZA MAŁO DANYCH | — | — |
| 409 | 14 | ZA MAŁO DANYCH | — | — |
| 536 | 14 | ZA MAŁO DANYCH | — | — |
| 441 | 14 | ZA MAŁO DANYCH | — | — |
| 207 | 12 | ZA MAŁO DANYCH | — | — |
| 535 | 11 | ZA MAŁO DANYCH | — | — |
| 508 | 9 | ZA MAŁO DANYCH | — | — |
| 289 | 8 | ZA MAŁO DANYCH | — | — |
| 500 | 8 | ZA MAŁO DANYCH | — | — |
| 376 | 8 | ZA MAŁO DANYCH | — | — |
| 537 | 4 | ZA MAŁO DANYCH | — | — |
| 123 | 3 | ZA MAŁO DANYCH | — | — |
| 526 | 1 | ZA MAŁO DANYCH | — | — |

### Wg restauracji (top 25 po n)
| segment | n | mediana (min, − = optymizm) | p10 | p90 |
|---|---|---|---|---|
| Grill Kebab | 50 | +0.0 | -18.0 | +14.2 |
| Rany Julek | 42 | -3.5 | -20.0 | +12.5 |
| Mama Thai Bistro | 37 | +3.6 | -13.5 | +17.9 |
| Miejska Miska | 29 | +0.6 | -9.8 | +9.1 |
| Sushi Rany Julek &amp; Pizza Majstry | 29 | -4.1 | -18.8 | +7.8 |
| Chicago Pizza | 23 | -0.2 | -16.2 | +7.7 |
| Sweet Fit &amp; Eat | 22 | +3.3 | -11.1 | +19.0 |
| Rukola Sienkiewicza | 20 | +3.5 | -8.4 | +17.2 |
| Pizzeria 105 Galeria Biała | 17 | ZA MAŁO DANYCH | — | — |
| Raj | 17 | ZA MAŁO DANYCH | — | — |
| Piwo Kaczka Sushi | 17 | ZA MAŁO DANYCH | — | — |
| Pani Pierożek | 16 | ZA MAŁO DANYCH | — | — |
| Kebab Król | 16 | ZA MAŁO DANYCH | — | — |
| Karczma Maciejówka | 13 | ZA MAŁO DANYCH | — | — |
| Retrospekcja | 12 | ZA MAŁO DANYCH | — | — |
| Rukola Kaczorowskiego | 12 | ZA MAŁO DANYCH | — | — |
| Pruszynka Restauracja | 11 | ZA MAŁO DANYCH | — | — |
| Goodboy | 10 | ZA MAŁO DANYCH | — | — |
| Hacienda Pizza | 10 | ZA MAŁO DANYCH | — | — |
| Street Mama Thai | 9 | ZA MAŁO DANYCH | — | — |
| Zapiecek | 9 | ZA MAŁO DANYCH | — | — |
| Toriko | 8 | ZA MAŁO DANYCH | — | — |
| Arsenal Panteon | 8 | ZA MAŁO DANYCH | — | — |
| Restauracja Kumar&#039;s | 8 | ZA MAŁO DANYCH | — | — |
| Bar Merino | 7 | ZA MAŁO DANYCH | — | — |

