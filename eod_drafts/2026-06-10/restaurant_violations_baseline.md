# Baseline naruszeń restauracji ±5 min — backfill z historii CSV

Źródło: `/root/panel_history_new/lokalka_zamowienia_2025-11_do_2026-06-09.csv` (2025-11 → 2026-06-09). Formuła = żywy detektor ETAP 6: `wait = real_pickup − max(commit, przyjazd)`, violation > 5 min. Przyjazd: `real − oczekiwanie odbiór` gdy panel zmierzył czekanie (≈21% wierszy), inaczej commit fallback.

- Zleceń (doręczone, bez paczek): **54726** (paczki/firmowe pominięte: 468)
- Naruszeń łącznie: **19872** = 36.3% zleceń
- Ranking: restauracje z ≥30 zleceniami

| # | Restauracja | Zleceń | Naruszeń | % | Mediana wait | p90 | w tym z pomiarem czekania |
|---|---|---|---|---|---|---|---|
| 1 | Rany Julek | 4609 | 2131 | 46.2% | 9 min | 20 min | 1055 |
| 2 | Grill Kebab | 4735 | 1366 | 28.8% | 9 min | 20 min | 74 |
| 3 | Mama Thai Bistro | 2526 | 1132 | 44.8% | 11 min | 22 min | 363 |
| 4 | Rukola Sienkiewicza | 2902 | 1101 | 37.9% | 10 min | 20 min | 150 |
| 5 | Chicago Pizza | 3072 | 1061 | 34.5% | 9 min | 19 min | 290 |
| 6 | Rukola Kaczorowskiego | 2214 | 921 | 41.6% | 10 min | 19 min | 101 |
| 7 | Raj | 2468 | 834 | 33.8% | 9 min | 18 min | 71 |
| 8 | Sushi Rany Julek & Pizza Majstry | 1973 | 709 | 35.9% | 9 min | 20 min | 142 |
| 9 | Karczma Maciejówka | 1481 | 624 | 42.1% | 10 min | 19 min | 83 |
| 10 | Restauracja Kumar's | 1706 | 575 | 33.7% | 9 min | 19 min | 37 |
| 11 | Pani Pierożek | 1445 | 546 | 37.8% | 10 min | 20 min | 55 |
| 12 | Miejska Miska | 1634 | 523 | 32.0% | 9 min | 19 min | 35 |
| 13 | Restauracja Sioux | 1159 | 437 | 37.7% | 10 min | 21 min | 24 |
| 14 | Ogniomistrz | 1144 | 434 | 37.9% | 9 min | 18 min | 168 |
| 15 | Pruszynka Restauracja | 1119 | 390 | 34.9% | 9 min | 19 min | 93 |
| 16 | Baanko | 1134 | 383 | 33.8% | 10 min | 22 min | 19 |
| 17 | Paradiso | 914 | 357 | 39.1% | 10 min | 22 min | 22 |
| 18 | Zapiecek | 941 | 356 | 37.8% | 10 min | 21 min | 21 |
| 19 | Retrospekcja | 1124 | 355 | 31.6% | 10 min | 22 min | 4 |
| 20 | Chinatown Bistro | 1042 | 346 | 33.2% | 10 min | 22 min | 5 |
| 21 | Kebab Król | 1090 | 326 | 29.9% | 9 min | 19 min | 9 |
| 22 | Piwo Kaczka Sushi | 754 | 304 | 40.3% | 10 min | 22 min | 34 |
| 23 | Goodboy | 941 | 301 | 32.0% | 9 min | 18 min | 9 |
| 24 | Ramen Base | 682 | 269 | 39.4% | 10 min | 23 min | 30 |
| 25 | Naleśniki Jak Smok | 815 | 255 | 31.3% | 10 min | 18 min | 16 |
| 26 | Doner Kebab | 696 | 213 | 30.6% | 10 min | 23 min | 4 |
| 27 | Pan Schabowy | 481 | 206 | 42.8% | 9 min | 20 min | 37 |
| 28 | Gym Fit Food | 503 | 204 | 40.6% | 10 min | 21 min | 58 |
| 29 | Trzy Po Trzy Mickiewicza | 494 | 196 | 39.7% | 9 min | 18 min | 58 |
| 30 | Trzy Po Trzy Sienkiewicza | 447 | 191 | 42.7% | 10 min | 21 min | 14 |
| 31 | Enklawa | 582 | 185 | 31.8% | 9 min | 19 min | 15 |
| 32 | _500 stopni | 417 | 158 | 37.9% | 10 min | 24 min | 3 |
| 33 | Sweet Fit & Eat | 586 | 150 | 25.6% | 10 min | 22 min | 9 |
| 34 | Pizzeria 105 Galeria Biała | 439 | 150 | 34.2% | 11 min | 20 min | 11 |
| 35 | Farina | 323 | 143 | 44.3% | 9 min | 21 min | 57 |
| 36 | Pizza Dealer | 418 | 138 | 33.0% | 10 min | 17 min | 13 |
| 37 | _350 Stopni KILIŃSKIEGO | 374 | 134 | 35.8% | 10 min | 20 min | 8 |
| 38 | Arsenal Panteon | 408 | 131 | 32.1% | 11 min | 23 min | 7 |
| 39 | Restauracja Eatally | 275 | 124 | 45.1% | 10 min | 19 min | 6 |
| 40 | Maison du cafe | 366 | 115 | 31.4% | 10 min | 22 min | 17 |

Ograniczenia: HH:MM bez daty (wraparound znormalizowany ±12h); `oczekiwanie odbiór` zależy od użycia statusu 4 przez kuriera (0 ≠ brak czekania, tylko brak pomiaru → wtedy commit fallback, jak w żywym detektorze); % liczony na doręczonych.