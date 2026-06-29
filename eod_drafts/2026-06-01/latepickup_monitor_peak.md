# Late-pickup/R6/wait peak monitor — 01.06 lunch

Okno: 2026-06-01T09:00:00+00:00 → 2026-06-01T12:00:00+00:00 | PROPOSE: 39

📊 PEAK MONITOR 01.06 lunch (11-14) — 4 zmiany selekcji 31.05
PROPOSE w oknie: 39
🔀 Opcja B przestawiła zwycięzcę: 12 — przegląd: {'OK_REJECT_BLIND': 8, 'SUSPECT_WORSE': 4}
   (OK_REJECT_BLIND=słusznie odrzucił blind/pre_shift fake-low | SUSPECT_WORSE=⚠realny kurier, gorszy dowóz | OK=lepszy/równy)
🔀 R6-danger przestawił zwycięzcę: 6
⏱ best w strefie danger r6>32: 4/39
⏳ best z ostrzejszą karą wait (fix#7): 5
↩ powrót: demoted=1 / wygrał(only/infeasible)=0
📦 best deliv_spread mediana=4.98km | r6 mediana=19.2min
pos_source best: {'pre_shift': 6, 'gps': 9, 'post_wave': 2, 'last_assigned_pickup': 8, 'last_picked_up_pickup': 4, 'no_gps': 10}
⚠️ DO OCZU — 4 potencjalnych regresji (realny kurier→gorszy r6):
  10:09 477548 Grill Kebab: Adrian R[post_wave](sc 8.18,r6 12.7)→Andrei K(sc 23.7,r6 22.6,tier 1)
  10:13 477549 Rany Julek: Adrian R[gps](sc -26.65,r6 13.0)→Adrian Cit(sc 1.76,r6 17.4,tier 1)
  10:14 477550 Goodboy: Adrian R[gps](sc -2.13,r6 13.9)→Andrei K(sc 81.35,r6 28.0,tier 1)
  10:21 477555 Sushi Rany Jul: Adrian R[gps](sc -37.17,r6 17.0)→Adrian Cit(sc -25.38,r6 33.6,tier 1)

## Opcja B rozjazdy (wszystkie, z klasyfikacją)

- ✅ [OK_REJECT_BLIND] 10:02 477544 Baanko: stary=Mateusz O[no_gps] (sc 101.09,tier 0,spread None,r6 10.3) → nowy=Andrei K (sc -8.37,tier 1,spread 4.98,r6 18.6)
- ✅ [OK_REJECT_BLIND] 10:06 477545 Rany Julek: stary=Mateusz O[no_gps] (sc 111.3,tier 0,spread None,r6 8.8) → nowy=Andrei K (sc 32.54,tier 1,spread 3.78,r6 19.9)
- ⚠️ [SUSPECT_WORSE] 10:09 477548 Grill Kebab: stary=Adrian R[post_wave] (sc 8.18,tier 0,spread 5.1,r6 12.7) → nowy=Andrei K (sc 23.7,tier 1,spread 3.2,r6 22.6)
- ⚠️ [SUSPECT_WORSE] 10:13 477549 Rany Julek: stary=Adrian R[gps] (sc -26.65,tier 0,spread None,r6 13.0) → nowy=Adrian Cit (sc 1.76,tier 1,spread 3.53,r6 17.4)
- ⚠️ [SUSPECT_WORSE] 10:14 477550 Goodboy: stary=Adrian R[gps] (sc -2.13,tier 0,spread None,r6 13.9) → nowy=Andrei K (sc 81.35,tier 1,spread 4.53,r6 28.0)
- ⚠️ [SUSPECT_WORSE] 10:21 477555 Sushi Rany Jul: stary=Adrian R[gps] (sc -37.17,tier 0,spread None,r6 17.0) → nowy=Adrian Cit (sc -25.38,tier 1,spread 5.33,r6 33.6)
- ✅ [OK_REJECT_BLIND] 10:44 477560 Pizza Dealer: stary=Marek[pre_shift] (sc 100.1,tier 0,spread None,r6 9.0) → nowy=Adrian Cit (sc 1.33,tier 1,spread 4.28,r6 18.2)
- ✅ [OK_REJECT_BLIND] 10:47 477561 Pizza Dealer: stary=Marek[pre_shift] (sc 90.4,tier 0,spread None,r6 9.2) → nowy=Adrian Cit (sc -25.23,tier 1,spread 4.28,r6 19.5)
- ✅ [OK_REJECT_BLIND] 11:15 477577 Rany Julek: stary=Marek[no_gps] (sc 95.99,tier 0,spread None,r6 12.8) → nowy=Adrian Cit (sc -23.55,tier 1,spread 4.69,r6 31.9)
- ✅ [OK_REJECT_BLIND] 11:24 477579 Pani Pierożek: stary=Marek[no_gps] (sc 65.58,tier 0,spread None,r6 6.3) → nowy=Michał K. (sc -18.19,tier 1,spread 7.25,r6 19.2)
- ✅ [OK_REJECT_BLIND] 11:39 477584 Retrospekcja: stary=Marek[no_gps] (sc 70.98,tier 0,spread None,r6 17.9) → nowy=Michał K. (sc -6.75,tier 1,spread 1.81,r6 26.5)
- ✅ [OK_REJECT_BLIND] 11:59 477591 Piwo Kaczka Su: stary=Marek[no_gps] (sc 96.53,tier 0,spread None,r6 18.6) → nowy=Andrei K (sc -21.89,tier 1,spread 12.11,r6 39.9)

## R6-danger rozjazdy

- 10:02 477544 Baanko: stary=Adrian Cit(r6 110.9) → nowy=Andrei K(r6 18.6)
- 10:06 477545 Rany Julek: stary=Adrian Cit(r6 106.2) → nowy=Andrei K(r6 19.9)
- 10:09 477548 Grill Kebab: stary=Adrian Cit(r6 109.6) → nowy=Andrei K(r6 22.6)
- 10:09 477547 Karczma Maciej: stary=Adrian Cit(r6 109.6) → nowy=Adrian R(r6 19.9)
- 11:02 477568 _500 stopni: stary=Adrian R(r6 47.7) → nowy=Michał K.(r6 32.0)
- 11:15 477577 Rany Julek: stary=Jakub OL(r6 34.5) → nowy=Adrian Cit(r6 31.9)

---
READ-ONLY (tylko shadow_decisions.jsonl). Bez wpływu na prod.
