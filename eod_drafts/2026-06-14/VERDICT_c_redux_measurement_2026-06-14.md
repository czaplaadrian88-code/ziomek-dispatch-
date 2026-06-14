# C-redux — werdykt pomiarowy (2026-06-14)

**Kontekst:** Po merge `auton/ziomek-hygiene` do master (`aaddc9a`) odblokował się batch C-redux (SCORE-03/04/06 + SCORE-09/10 + EARLYBIRD-01 + COD Weekly). Adrian: „zmierz, zdiagnozuj, przetestuj na historycznych — chcę wiedzieć że nie będzie żadnej regresji ZANIM ruszymy autonomicznie."

**Metoda:** 3 niezależne pomiary READ-ONLY na realnych danych (1652 czyste decyzje PROPOSE, 02–14.06), z wykluczeniem 2 skażonych okien (PARSER_DEGRADED 06-06T17:53..06-10T18:24; SYNCWORKA 06-11T14:28..06-12T18:32). Dyscyplina: TREND/NETTO, nie pojedyncze przypadki (lekcja food-age 14.06).

---

## 🟥 SCORE-03/04/06 — WERDYKT: **NIE WDRAŻAĆ** (diagnoza audytu błędna)

Skrypt: `eod_drafts/2026-06-14/score_axis_double_count_audit.py` (READ-ONLY).

- Realny wskaźnik ≥2 osi kary = **26%** (nie 46% z audytu; 46% tylko przy luźniejszej def. osi z r9_stopover).
- **To NIE podwójne liczenie:** korelacja(timing_gap, r6_soft) = **−0,02** ≈ zero. Trójka timing_gap/r6_soft/r5_detour mierzy 3 NIEZALEŻNE problemy (spóźnienie vs termika worka vs objazd-km), nie ten sam. Konsolidacja = utrata realnego sygnału.
- **Brak szkody w outcome:** kandydaci z naładowanymi karami → breach ~**7%**, identycznie jak baseline floty (płaskie po kubełkach 0-1/2/3+ osi). Kary nie wybierają złych kurierów.
- **Selekcja warstwowa:** w **67%** rekordów ≥2-osiowych BEST ≠ score-argmax (late-pickup/best-effort/r6-redirect nadpisują score) → przeważenie wag i tak nie zmieniłoby zwycięzcy.
- Jedyny realny double-count: s_obciazenie (bag≥1) + termika = 26% przypadków, ale wkład s_obciazenie ograniczony do ≤25 pkt (waga 0.25) wobec termiki w dziesiątkach/setkach → marginalny.

**Decyzja:** Ryzyko regresji WYSOKIE, zysk ~zero. NIE konsolidować. Udokumentować trójkę jako celowo-addytywne sygnały feasibility, nie traktować współwystępowania jako bug. (Osobny E7 re-tune R4-cap to inny temat — patrz B4_E7_weight_retune_proposal.)

## 🟧 SCORE-09/10 — WERDYKT: **NIE WDRAŻAĆ / ZAMKNĘTE** (near-no-op + regresja)

Skrypt: `eod_drafts/2026-06-14/score09_doomed_pickup_penalty_measure.py`; doc: `VERDICT_score09_doomed_pickup_penalty.md`.

- Część carry-overlap już odrzucona 11.06 (carry>35 = 6,6% < próg 20%; `VERDICT_carry_overlap.md`).
- Pozostała kara proporcjonalna za doomed picked_up>35: kurier z doomed-workiem **wygrywa tylko 5,9%** decyzji (< próg materialności 20%).
- Symulacja: **7 flipów / 12 dni = 0,4%** decyzji; saturuje (większy COEFF nie zmienia). 60/98 doomed-winnerów wybranych przez warstwę NIE-score → kara to no-op.
- **6 z 7 flipów = REGRESJA:** przekierowanie na kuriera równie/bardziej obciążonego (3→4, 2→5) lub dalej (0,5→5,8 km). Mediana doomed = 39 min (4 min ponad) → przekierowanie nie ratuje już-starego jedzenia.
- Dubluje żywe V4 `OBJ_R6_SOFT_DEADLINE` (anti-pattern SCORE-03/04).

**Decyzja:** Cały temat SCORE-09/10 ZAMKNIĘTY (carry 11.06 + doomed-penalty 14.06). Re-open tylko gdyby E7 re-tune podbił doomed-as-winner do ≥20%.

## 🟩 EARLYBIRD-01 — WERDYKT: **WDROŻYĆ (shadow-first)** — jedyny GO z C-redux

Skrypt: `eod_drafts/2026-06-14/earlybird01_measure.py`. Log źródłowy: `dispatch_state/observability/candidate_decisions_YYYYMMDD.jsonl` (verdict=KOORD, reason="early_bird (N min ahead)").

- early_bird = **mediana 44,8%/dzień** wszystkich KOORD (potwierdza audyt 44-46%); **~16 alertów/dzień**, ~40% wolumenu KOORD.
- Trigger: `dispatch_pipeline.py` `_early_bird_threshold_min()` (próg `EARLY_BIRD_THRESHOLD_MIN`=60, hot-reload flags.json) — zwiera obwód PRZED budową puli feasibility.
- Wszystkie odpalają ≥60 min przed odbiorem → zawsze poza oknem T-30.
- **Ryzyko regresji NISKIE i zmierzone:** z 87 early_birdów, które PÓŹNIEJ potrzebowały realnej eskalacji, **86/87 (99%) odpaliło realny KOORD z >30 min wyprzedzeniem**; tylko **1/87** w oknie T-30 → pokrywa backstop `CZASOWKA_TRIGGERS_MIN` (flags.json l.43).
- **Korzyść:** ~16 mniej „pilnych" alertów/dzień; koordynator widzi realne eskalacje zamiast szumu (~40% redukcja wolumenu KOORD).

**⚠ LUKA do zamknięcia w shadow:** „deferowalność ~83%" to PROXY (early_bird zwiera przed pulą → logi nie mówią czy kurier byłby dostępny w T-30). Faza shadow MUSI przepuścić early_birdy do feasibility w T-30 i zmierzyć realną rozwiązywalność PRZED flipem na AUTO.

**Plan:** shadow-first (flaga OFF, log kontrfaktyczny) → kilka dni → replay OFF↔ON + no-regress gate (żaden realnie-potrzebny KOORD nie odpala za późno) → flip za ACK.

---

## COD Weekly
Nie-scoringowe, większość LIVE (3 timery preflight/last-call/write + dual-write arkusz+panel). W C-redux = polerka operacyjna, niskie ryzyko, osobny tor.

## TL;DR
Z 4 pozycji C-redux: **2 odrzucone pomiarem** (SCORE-03/04/06 net-szkodliwe/cosmetic, SCORE-09/10 near-no-op+regresja), **1 GO shadow-first** (EARLYBIRD-01), 1 operacyjna polerka (COD). Pomiar zadziałał: zatrzymał 2 zmiany grożące regresją przy zerowym zysku.
