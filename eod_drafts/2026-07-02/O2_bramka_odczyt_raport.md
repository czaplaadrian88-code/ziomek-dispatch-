# O2 (L6.B) — ODCZYT BRAMKI bundle_calib, 2026-07-02

**Pas:** O2-odczyt (FALA-2 audytu Ziomka, tmux 11). **READ-ONLY, zero edycji kodu/flag/flipów.**
**Autor:** Claude Fable 5 · **Data odczytu:** 2026-07-02 ~12:35 UTC.
**Kalendarz (tracker §5, 02.07):** „bramka O2 (L6.B) — wyrównać kotwicę bundle_calib; bug4-gate WAIT z fałszywego powodu."

---

## TL;DR (jedno zdanie + liczby + rekomendacja)

Bramka O2 **JUŻ SIĘ SKONSUMOWAŁA DZIŚ RANO** — `dispatch-bundle-calib-review` odpalił się o **07:00 UTC** (one-shot timer, NEXT=`-`), at-168 spingował werdykt o 08:00; **WERDYKT = GO** na sprint (nie na flip), materialny i fizycznie uziemiony, instrument **uczciwy (regresje odsłonięte) + proxy-certyfikowany-konserwatywny** (nie ground-truth). **Rekomendacja: GO na SPRINT silnika za ACK; flip flagi as-is = NO-GO dziś** (flaga włącza SUROWY O2 = łamie carried-first; wartość jest wyłącznie w wąskiej regule cap-Z=20, której w silniku JESZCZE NIE MA). **Pozycja kalendarza 02.07 (odczyt O2) = MOŻE być oznaczona SKONSUMOWANA** (bramka odczytana, werdykt zapisany, rekomendacja niżej); **bug4-gate = osobny pas, NIE konsumuję** (WAIT słuszny, ale z mylącego powodu — patrz §4).

---

## 1. STAN FAKTYCZNY

### 1a. Bramka odpaliła się rano — nie trzeba jej uruchamiać
- `dispatch-bundle-calib-review.timer` — ostatni bieg **Thu 2026-07-02 07:00:01 UTC**, NEXT=`-` (one-shot, skonsumowany). Journal: `Finished ... Deactivated successfully`, exit 0.
- Werdykt zapisany do **3 miejsc**: `scripts/logs/bundle_calib_review.log` (07:00) + `dispatch_state/bundle_calib_review_verdict_2026-07-02.txt` (kopia trwała) + Telegram (`[telegram] wysłano`).
- at-168 (08:00) = `bundle_calib_verdict_reminder` — odczytał werdykt i spingował Adriana: „➤ WERDYKT: GO → Odpal sesję Claude Code do DECYZJI ... NIC nie flipować bez ACK". Log: `dispatch_state/bundle_calib_verdict_reminder.log` (`wyslano=True`).
- **Nikt jeszcze nie zrobił z tego ODCZYTU** (pozycja kalendarza otwarta) — ten raport ją domyka.
- Zgodność z notką [[l12-l22-d3]] („bundle_calib_review po bramce 08:00, werdykt GO niezmieniony"): **POTWIERDZONE** — werdykt GO, przy czym „08:00" to at-168 reminder, sam review = 07:00.

### 1b. „Wyrównać kotwicę bundle_calib" — co to znaczy i co zrobione
Dwie osie kotwicy:
- **Kotwica w INSTRUMENCIE (review) = WYRÓWNANA.** (i) GATE O2 liczy **overage-ONLY = parytet silnika** (fix 29.06 `477b731` — zdjęty λ=1.5 z bramki); (ii) outcome-join #5b bierze **fizyczny przyjazd GPS** (432/432 zleceń na fizycznym, reszta klik-fallback) i liczy R6 **od gotowości** (ready-anchor). Instrument mierzy dziś to, co silnik by zrobił, na fizycznej prawdzie.
- **Kotwica w SILNIKU (gate-fix) = WCIĄŻ OTWARTA** — finding `feas-r6-sla-anchor-gap` (ZIOMEK_FINDINGS_LEDGER l.48, status **open**, „L6.B / bramka 02.07"). SLA/R6-anchor wciąż na `pickup_at` (nie ready) w 3 bliźniakach: `_count_sla_violations` (route_sim), `feasibility_v2:~1156`, `plan_recheck._o2_key`. To **prerekwizyt FLIPU**, nie odczytu — należy do sprintu, nie do dzisiejszej bramki. Kotwica instrumentu (pomiar) wyrównana; kotwica silnika (decyzja) czeka na sprint.

### 1c. Stan flagi (ETAP 0 — efektywny w procesie, nie env-default)
- `ENABLE_O2_READY_ANCHOR_SWEEP`: **BRAK w `flags.json`**, **brak drop-inu** w `/etc/systemd/system/`, **brak w `systemctl show dispatch-shadow -p Environment`** → efektywnie **OFF (env-default)**. **FLIP NIE ZOSTAŁ ZROBIONY.** Rekomendacja spójna.

---

## 2. LICZBY WERDYKTU (NETTO, nie 1 case)

**Korpus:** 2718 wpisów / 2715 unikalnych worków multi-order; CALIB≠served w 1184 (43.6%). **Okno danych: 2026-06-25 14:17 → 02.07 07:00 UTC (~6,5 dnia).** coverage under_z **1124/1184 = 94.9%** (≫ próg 20% → NIE inconclusive).

**① PUŁAP (surowy O2, freshness-blind — NIE polityka, NIE flipować):**
- improved_O2 (ΔO2≥2min): **612 (22.5%)**, med ΔO2 2.2 min; **regres_O2: 19 (1.6%)**.

**③ POLITYKA pod twardym capem świeżości (Opcja 3, to co realnie do flipu):**

| Cap Z | policy-improved | med ΔO2 zysk | detour med / p90 | feasible |
|---|---|---|---|---|
| **Z≤20 (rekom.)** | **214 (7.9%)** | 10.4 min | 0.04 / 7.93 min | 20.7% |
| Z≤32 | 316 (11.6%) | 10.4 min | −0.0 / 7.63 min | 27.7% |
| Z≤35 | 353 (13.0%) | 10.1 min | 0.15 / 7.62 min | 29.4% |

**Fizyczna prawda (#5b, GPS):** n=163 worki, 432/432 zleceń na fizycznym przyjeździe → **served realnie naruszał R6 (od gotowości) w 36.1% zleceń** = O2 celuje w REALNĄ nieświeżość, nie w predykcyjny artefakt.

**Sprawdzenie wzorca „12,4% improved ale 7 regresji ukrytych" (ETAP 5):**
- Instrument DZIŚ **odsłania regresje** (fix `477b731` zadziałał): pułap regres_O2 = 19 (1.6%), count-regres (więcej zleceń >35) = 78 (6.6%) — **widoczne, nie ukryte.**
- Te regresje siedzą na **pułapie freshness-blind**, który werdykt EXPLICITE zakazuje flipować („⚠ NIE flipować surowego O2 — łamie carried-first").
- Pod **regułą polityki (cap-Z hard, argmin overage)** genuine-regres **≈0 z konstrukcji** (audyt 28.06: 7 „regresów" overage = artefakt λ-kolektora, wszystkie cid=123 czas_late>0; dla czas_late=0 regresja świeżości matematycznie niemożliwa pod hard-cap). **NETTO przy Z=20: +214 worki lepsze (7.9%, med +10.4min O2), regres pod capem ~0, detour med 0.04/p90 7.93 min.**
- Wniosek: to **NIE** sytuacja ukrytych regresji — to odwrotność (instrument uczciwie pokazuje regresje pułapu, a rekomendowana wąska reguła je ogranicza capem).

---

## 3. JAKOŚĆ INSTRUMENTU (ORACLE-CAVEATS)

**Klasyfikacja: `proxy-certyfikowany` (KONSERWATYWNY), NIE `ground-truth`.** Werdykt GO na proxy-truth **dopuszczalny** — bo niesie jawny caveat + kierunek jest bezpieczny.

- **Caveat λ NIE domknięty u źródła, ALE obłaskawiony na read-side.** ORACLE-CAVEAT: „`bundle_calib` calib_seq z λ=1.5 ≠ sekwencja silnika dla worków czas_late>0 → replay proxy-skażony, re-collect z `BUNDLE_CALIB_LAMBDA_CZAS=0`". Review **NIE re-kolekcjonował** z λ=0 — zamiast tego przelicza bramkę **overage-only** na read-side. Skutek: sekwencja kolektora wciąż λ=1.5-pochodna, ale ocena overage jest **konserwatywna (silnik ≥ tyle, brak fałszywego GO)** — nagłówek werdyktu to deklaruje wprost. Kierunek werdyktu bezpieczny; **dokładna WIELKOŚĆ zysku (7.9%/13%) może być zaniżona** dla worków czas_late>0. Plan domknięcia = re-collect z `BUNDLE_CALIB_LAMBDA_CZAS=0` (podniesie proxy→ground-truth), opcjonalny bo kierunek już bezpieczny.
- **Button-truth caveat: częściowo domknięty.** Outcome-join korzysta z **fizycznego GPS** (#5b, 432/432 fizyczne) zamiast samego kliknięcia → „36.1% realnie >R6" jest fizycznie uziemione, nie przyciskowe. To był HOLD z 28.06 („czekaj na #5b") — **zdjęty**.
- **CORE instrumentu = VALIDATED** ([[shadow-jobs-registry]]): selekcja/metryki vs brute/OSRM zwalidowane; oś PEAK OSRM certyfikowana-czysta. Bramka O2 review = ✅ NAPRAWIONA 29.06 (`477b731`, walidacja 3-pkt improved 317→304=oracle silnik, regress 0→7 odsłonięte).
- **Okno vs deploy 11:45:** review zakończył zbiór o **07:00**, restart shadow był **11:45** → okno danych review **całe PRZED restartem**. **Zero skażenia post-deploy.** (Live shadow rośnie dalej: 2826 wpisów o 12:32, ale te po-07:00 NIE weszły do review.)

**Werdykt jakości:** instrument wiarygodny w KIERUNKU (GO uczciwe, regresje odsłonięte, fizycznie uziemione); wielkość zysku konserwatywna (dolne oszacowanie). Oznaczenie: **proxy-certyfikowany**.

---

## 4. bug4-gate — „WAIT z fałszywego powodu" (osobny pas, NIE konsumuję — surfacing)

`dispatch_state/bug4_reseq_verdict.txt` (okno 2026-06-29..30, mtime 30.06 20:22):
- próbek multi-zlec. RETIME **1249** (suspect/inwariant-naruszony WYKLUCZONE 153); deliv_seq_differs 168 (15%); **delta≥1min 240 (22%)**; delta drive median **5.3** / p90 16.4 / max 33.8 / suma 1788.8 min.
- ZDROWIE INSTRUMENTU: **suspect 153/1249 = 12% ⚠ >10% — pomiar wciąż skażony, oracle-recheck PRZED GO.**
- WERDYKT: **WAIT/NO** — „materialność poniżej progu (≥20% + median≥1.5min) LUB mała próba".

**„Z fałszywego powodu" POTWIERDZONE:** materialność jest **SPEŁNIONA** (delta≥1min 22% ≥ 20%; median 5.3 ≥ 1.5min), więc podany powód WAIT („materialność poniżej progu") jest **myląco fałszywy**. **Prawdziwy blocker = zdrowie instrumentu** (suspect 12% > 10% → wymaga oracle-recheck, bo `fresh` bywa gorszy od `frozen` = niemożliwe). Czyli WAIT jest SŁUSZNY co do decyzji (nie flipować), ale RAPORT gate'a wskazuje zły powód. **To osobny pas (bug4-reseq, L6/reseq), nie O2** — surfacing dla kalendarza, do domknięcia oracle-recheckiem przez właściwy pas (obniżyć suspect ≤10%, potem re-werdykt). NIE oznaczam tej sub-pozycji jako skonsumowanej.

---

## 5. REKOMENDACJA DLA ADRIANA

**GO na SPRINT silnika (nie na flip). Flip flagi as-is = NO-GO dziś.**

- **Co jest GO:** sygnał jest **materialny** (Z=20: +214 worki/7.9%, med +10.4min O2, detour med 0.04/p90 7.93min), **fizycznie uziemiony** (36.1% served realnie >R6 od gotowości, GPS), **bez ukrytych regresji** (pod capem genuine-regres ≈0). Warto budować.
- **Czego NIE robić dziś (NO-GO/CZEKAJ):** **NIE hot-flipować `ENABLE_O2_READY_ANCHOR_SWEEP=true` as-is** — flaga włącza SUROWY O2 (freshness-blind), który werdykt EXPLICITE zakazuje („łamie carried-first"). Wartość jest wyłącznie w **wąskiej regule** (detour≤X/Y ORAZ carried≤Z=20), której w silniku **jeszcze nie ma** — trzeba ją ZBUDOWAĆ.
- **Droga (za ACK, PRZYKAZANIE #0 ETAP 0-7):**
  1. Zbuduj wąską regułę cap-Z=20 (detour≤X/Y ∧ carried≤Z) w **trójce RAZEM**: `feasibility_v2` + `route_simulator_v2` + `plan_recheck` (Załącznik A protokołu — bliźniaki razem).
  2. Domknij **gate-fix SLA/R6-anchor pickup_at→ready** w 3 bliźniakach (`_count_sla_violations`, `feasibility_v2:~1156`, `plan_recheck._o2_key`) — finding `feas-r6-sla-anchor-gap` (open). Co-design z `ENABLE_ETA_QUANTILE_R6_BAGCAP` + `ENABLE_PACZKA_R6_THERMAL_EXEMPT` (protokół, „3 bliźniaki SLA-anchor RAZEM").
  3. (opcjonalnie, podniesie proxy→ground-truth) re-collect kolektora z `BUNDLE_CALIB_LAMBDA_CZAS=0` — domyka caveat λ; kierunek już bezpieczny bez tego.
  4. Flaga OFF → shadow (parytet ON≠OFF) → ON, **pełna regresja vs baseline + e2e + rollback**.
  5. Sam **FLIP = ACK Adriana**, off-peak, 1 restart.
- **Rollback flipu (gdy dojdzie):** flaga → OFF w flags.json (hot).

**Kalendarz 02.07:** pozycja **„bramka O2 (L6.B) — odczyt" = MOŻNA oznaczyć SKONSUMOWANĄ** (bramka odpalona 07:00, werdykt GO odczytany+zapisany, rekomendacja dostarczona; dalszy ruch = sprint za ACK, poprawnie odroczony). Sub-nota **„bug4-gate WAIT z fałszywego powodu" = surfaced, NIE skonsumowana** (osobny pas, oracle-recheck suspect 12%→≤10%).

---

*Źródła: `scripts/logs/bundle_calib_review.log` (07:00) · `dispatch_state/bundle_calib_review_verdict_2026-07-02.txt` · `dispatch_state/bundle_calib_verdict_reminder.log` (at-168) · `dispatch_state/bug4_reseq_verdict.txt` · `ZIOMEK_FINDINGS_LEDGER.md` l.48 · `ZIOMEK_STAN_AUDYTY_1i2.md` §5 l.97 · [[ziomek-change-protocol]] ORACLE-CAVEATS · [[top10-progressive-potential-2026-06-29]] #2 · [[shadow-jobs-registry]].*
