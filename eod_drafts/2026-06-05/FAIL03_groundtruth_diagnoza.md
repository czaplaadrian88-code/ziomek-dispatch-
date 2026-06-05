# FAIL-03 — diagnoza ground-truth: cisza KOORD vs realny wybór człowieka + realne dowozy

**Data:** 2026-06-05 | **Próbka:** 72 zlecenia-w-ciszę (K1 `always_propose_would_redirect_shadow`, 06-04/05)
**Cel (Adrian):** nie uczyć Ziomka imitacji omylnego człowieka — odtworzyć ground-truth (realne dowozy + koordy + flota w momencie T) i sprawdzić, czy istniała opcja LEPSZA niż best_effort Ziomka *i* niż człowiek. Docelowo: propozycje najlepsze z możliwych, lepsze od człowieka.

## Dane źródłowe
- `shadow_decisions.jsonl` — K1 (best_effort Ziomka + cała oceniona flota + powód ciszy)
- `events.db` audit_log — realne `COURIER_ASSIGNED/PICKED_UP/DELIVERED` (wybór człowieka + outcome)
- `events.db` events (NEW_ORDER) — koordy pickup/delivery + `pickup_at_warsaw`
- `courier_api.db` gps_history — pozycje floty (replay) — **patrz LIMIT niżej**
- Skrypt: `fail03_groundtruth_recon.py` | Korpus: `fail03_silent_corpus.json`

## Wyniki (twarde, z realnych outcome'ów)

### 1. CZY proponować — UDOWODNIONE (strukturalnie)
- **100% (72/72)** zleceń-w-ciszę dostało realne przypisanie człowieka. 0 do koordynatora (id=26), 0 przepadło.
- Gdy człowiek wziął kuriera, którego Ziomek oceniał (56/72): **56/56 ten kurier też był u Ziomka infeasible/best_effort**. Ani razu człowiek nie wziął kuriera oznaczonego przez Ziomka jako feasible.
- → Lepszej (wykonalnej w modelu Ziomka) opcji NIE BYŁO. **Cisza była ściśle gorsza niż propozycja.**

### 2. JAKOŚĆ proponowania — to NIE słabe propozycje (kluczowe dla obawy Adriana)
- **76% (55/72) wyborów człowieka REALNIE dowiozło ≤35min (R6 OK).** Per powód: r6_breach_v2 50/66 (76%), low_score 4/5 (80%).
- **Mechanizm: odroczenie odbioru.** Mediana defer człowieka = **22min** (52/72 odroczyli >10min). OK-dowozy: median defer 22min; breache: tylko 14min → **więcej odroczenia = lepszy wynik R6** (te zlecenia są timing-hard, nie geometry-hard).
- **Ziomek jest PESYMISTYCZNY:** est_breach (best_effort) > realny leg w **50%** przypadków, mediana (est − realny) = **+7min**. Ziomek liczy breach przy założeniu odbioru NATYCHMIAST + pełnego detoura; realnie z odroczeniem leg jest krótszy. To źródło fałszywego KOORD.
- → Proponowanie best_effort **z odroczonym odbiorem** dałoby w 76% przypadków DOBRY, punktualny dowóz — nie słaby.

### 3. "Lepszy od człowieka" — NIEROZSTRZYGALNE dla przeszłych 72 (brak danych)
- Pozycje GPS dostępne tylko dla **7/72** wyborów. 06-04 cały dzień = **956 fixów** (okno 13:00-13:10 = 12 fixów, 1 kurier). **GPS celowo wyłączony na ~całej flocie** (apka na paru kontach: 123, 484).
- To także DLACZEGO 22% zleceń-w-ciszę dostało kuriera „NOT_SEEN" (526/530/509 realnie pracowali — 509: 77 przypisań/33 odbiory 06-04 — ale bez GPS niewidoczni dla Ziomka).
- **Wniosek:** geometrycznego „czy istniał lepszy kurier" NIE da się odtworzyć wstecz bez pozycji floty. To wymaga danych GOING-FORWARD (→ K1+outcome+pozycje, gdy GPS się upowszechni — vc40 rollout). **NIE twierdzić, że człowiek był optymalny — twierdzić: nieoznaczone.**

### 4. 24% (17/72) realny breach mimo odroczenia
Genuinely hard (geometry, nie timing). Best_effort + uczciwy baner = właściwe. Model powinien tu odraczać MOCNIEJ (breache odraczały tylko 14min).

## Implikacje dla nauki Ziomka (K2 + model selekcji)
1. **K2 (cisza→PROPOSE) uzasadnione z nawiązką** — nie tylko „lepsze niż cisza", ale 76% to dobre dowozy.
2. **Kluczowy fix jakościowy: ODROCZ ODBIÓR w propozycji** (policz target_pickup tak, by leg dowozu zmieścił R6) — nie tylko baner „łamie R6". To zamienia 76% pesymistycznych KOORD w punktualne propozycje. Zgodne z [[feedback_two_hard_rules_defer_over_extend]] + [[feedback_always_propose_defer_pickup]].
3. **Skalibrować pesymizm best_effort** — est_breach przeszacowany medianowo o 7min (założenie immediate-pickup). Recalc breach pod realny defer.
4. **"Lepszy od człowieka" wymaga GPS floty** — tor #2 (model selekcji / M3) trenować na danych going-forward z pozycjami + outcome (tool `fail03_outcome_join.py`).

## Zastrzeżenia
2 dni, n=72, 06-04 dominuje (wysoki KOORD). Wnioski #1/#2 strukturalne→odporne; rates potrzebują więcej dni. Recalib ETA z 06-05 zmniejszy wolumen r6_breach (nie zmieni kierunku).
