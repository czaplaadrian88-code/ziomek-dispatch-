# Ziomek — korpus błędnych picków (2026-05-24, lunch peak ~14:26–14:33 Warsaw)

**Cel:** zebrać twardą prawdę z `learning_log.jsonl` dla picków, które Adrian oznaczył jako błędne.
FAZA = ZBIERANIE. Naprawy później. NIE implementować nic na tym etapie.

**Źródła:** `dispatch_state/learning_log.jsonl` (pełne `decision.best` + `plan`), geokody z `dispatch.log` /
`geocode_cache.json` / `restaurant_coords.json`. Kod zweryfikowany na żywo: `feasibility_v2.py:328-387`
(R1 directionality / FIX 2), `dispatch_pipeline.py:2205-2232` (próg `bonus_r1_corridor`).

---

## 🔬 USTALENIA PRZEKROJOWE (powtarzają się między przypadkami)

### F1 — `bonus_r1_corridor` = próg-KLIF na średnim pairwise cosine CAŁEGO bagu, nie gradient
`dispatch_pipeline.py:2213-2222`:
```
avg_cos > 0.85  → +20
avg_cos > 0.5   → +5
avg_cos > 0.0   →  0
avg_cos > -0.5  → -35   ← KLIF
else            → -40
```
Każdy bag z avg pairwise cosine w (−0.5, 0] dostaje płaskie **−35**, bez różnicy czy bundel jest dobry
czy zły. Odpaliło we **wszystkich 4 przypadkach** (avg_cos: −0.306, −0.371, −0.307, −0.054).
Case D miał avg_cos = **−0.054** (praktycznie neutralny, deliveries de facto zbieżne) → też pełne −35.

### F2 — cosine liczony na CAŁYM mieszanym bagu z pozycji kuriera, nie na istotnej parze/fali
`feasibility_v2.py:359-387` (FIX 2 z 2026-05-22): kierunek nowej dostawy vs **średnia kierunków
WSZYSTKICH dropów w bagu**, mierzona z `courier_pos` (`last_picked_up_pickup` / `last_assigned_pickup`).
Skutek: dropy które **zostaną doręczone i znikną z bagu ZANIM zacznie się fala nowego ordera** wciąż
zanieczyszczają średnią. Dobra para (cos +0.92) tonie pod nie-powiązanymi/starymi nogami trasy.

### F3 — `bonus_r4` ("po drodze" korytarz, do +100) ZEROWANY przez ten sam trigger cross-quadrant
"Z-OWN-1 corridor mult" (`common.py:~1753`, flaga `ENABLE_V327_BUG_FIXES_BUNDLE`). Gdy odpala kara
cross-quadrant → `bonus_r4 *= 0`. W Case C zarobione **raw=100** → 0; Case A raw=28 → 0. Dobre pickupy
na korytarzu nie dostają kredytu.

### F4 — niskie / UJEMNE score'y wciąż prezentowane jako "🟡 ACK — sensowny wybór"
- Case D: score **−20.34**, ACK bo `pool_feasible=1<2` (jedyny wykonalny kandydat) → "sensowny wybór".
- Case A/B/C: score 1.8–2.7, ACK bo `score_margin<15`.
Próg "sensowności" w UX nie oddaje że to picki marginalne / złe.

### F5 — brak zakazu powrotu do tej samej restauracji z jej dowozem w bagu (reguła Adriana, NIEZAKODOWANA)
Patrz Case B. `same_restaurant_grouper.py` nie scala dwóch wizyt gdy jest luka gotowości jedzenia.

### F6 — brak limitu/penalty za nadmiar pickupów przed doręczeniami (thermal + detour)
Patrz Case D: 4 pickupy zbierane zygzakiem (detour 6.98 km, span 33 min), jedzenie wożone do granicy
R6 (34.4 / 35 min) tylko po to by dozbierać dowozy.

---

## 📋 PRZYPADKI

### CASE A — 475694 · Rany Julek → Białostoczek 11/17 · K-409 Mateusz Bro
- **Score 1.796 · ACK · PANEL_OVERRIDE → cid 508**
- **Problem Adriana:** "nie łączy w jedną falę Baanko i Rany Julek, to byłby świetny bundel".
- **Werdykt analizy:** Ziomek FIZYCZNIE połączył (Rany Julek dorzucony do K-409 który wiózł Baanko;
  `n_waves=1`, `bonus_wave_clean=+10`), ale SCORING zabił bundel.
- **Geometria pary (świetna):** drop Baanko (Wierzbowa) ↔ drop Rany Julek (Białostoczek) = 2.18 km,
  cos **+0.922** (22.7°). Restauracje 0.93 km.
- **Dlaczego kara:** bag zawiera też 2 dostawy Rukoli (Łąkowa cos −0.82, Sybiraków cos −0.98) które
  są doręczane (14:33/14:40) ZANIM zacznie się fala Baanko+Rany Julek (14:56→15:19). Średnia bagu
  → −0.83 → `bonus_r1_corridor=-35`, `bonus_r4` 28→0, `bundle_bonus=0`. Suma kar −88.1.
- Metryki: `r1_avg_pairwise_cosine=-0.306`, `r1_new_drop_cosine=-0.827`, `deliv_spread=7.17`,
  `bug2_continuation=+30`, `timing_gap_bonus=+15`, `bonus_bug4_cap_soft=-20` (std cap=3, to 4.).
- **Wzorce:** F1, F2, F3, F4.

### CASE B — 475698 · Retrospekcja → sudecka 10/8 (Skorupy) · K-520 Michał Rom
- **Score 2.726 · ACK** (`score_margin=12<15`)
- **Reguła Adriana (TWARDA, do zakodowania):** „kurier nie odbiera z restauracji bez doręczenia i
  później znowu jedzie do TEJ SAMEJ restauracji — to zakazane. Jak wyjeżdża, może wrócić, ale BEZ ich
  dowozów w bagu."
- **Co się dzieje:** K-520 ma 2 ordery z Retrospekcji — 475685 (→Piłsudskiego, ready ~14:50) +
  475698 NOWY (→sudecka, ready 15:01). **Renderowana trasa (to co widzi Adrian):**
  Chicago 14:42 → **Retrospekcja 14:50** → Dubois (dostawa Chicago) 14:53 → **Retrospekcja 15:01** →
  Piłsudskiego 15:11 → sudecka 15:22. ⇒ DWIE wizyty w Retrospekcji z dostawą pośrodku, nosząc 1. order
  Retrospekcji = wzorzec zakazany.
- **⚠ Rozbieżność plan vs render do zweryfikowania w fazie naprawy:** zapisany plan OR-Tools grupuje
  pickupy (`pickup_at` 475685=15:00, 475698=15:01, seq `[475682,475685,475698]`), ale `r8_pickup_span_min=15.4`
  i renderowana trasa pokazują SPLIT/powrót. Trzeba ustalić, którą sekwencję kurier realnie wykonuje.
- **Przyczyna split:** luka gotowości 11 min (475685 14:50 vs 475698 15:01) — grouper nie scala w 1 wizytę.
- Metryki: `bundle_level1=Retrospekcja` (wykryty SR-bundle!), `bundle_bonus=20`, `r1_new_drop_cosine=-0.531`,
  `bonus_r1_corridor=-35` (deliveries Piłsudskiego vs sudecka rozbieżne), `deliv_spread=7.46`,
  `r5_pickup_detour_total_km=2.46`, `bonus_penalty_sum=-113.0`, `r6_max_bag_time=24.8`.
- **Wzorce:** F5 (główny), F1, F2.

### CASE C — 475699 · Pizza Dealer → Bolesława Chrobrego 1A (Piasta I) · K-409 Mateusz Bro
- **Score 1.999 · ACK** (`score_margin=9.5<15`)
- **Problem Adriana:** „Mateusz nie pojedzie na Chrobrego i Wierzbowa — to dwa inne kierunki."
- **Co się dzieje:** K-409 wiezie Baanko (→Wierzbowa). Nowy Pizza Dealer (→Chrobrego). Pickupy świetne
  (Baanko + Pizza Dealer blisko: `pickup_spread=1.21`, `r5_detour=0.11`, restauracje dev 0.1 → L3 bundle),
  ale DOSTAWY rozbieżne: Chrobrego (Piasta) vs Wierzbowa = przeciwne kierunki.
- **Tu kara R1 odpaliła SŁUSZNIE** (`avg_pairwise=-0.307` → −35), ale order i tak zaproponowany bo:
  pool cienki + `bug2_continuation=+30` + `bundle_bonus=19.2` przeważyły. `bonus_r4` raw=**100**→0 (F3).
- **Niuans:** to "pickup-bundle, delivery-split" (Bug Z cross-quadrant). Kara istnieje, ale za słaba by
  odrzucić. `r1_new_drop_cosine=+0.292` (mylące — bo średnia bagu zawiera już-doręczany Sybiraków).
- Metryki: `deliv_spread=7.17`, `bonus_r9_stopover=-16`, `bonus_penalty_sum=-63.2`, `r6_max_bag_time=25.7`
  (worst=475693 Baanko — Wierzbowa dostarczona dopiero 15:20, bo kurier nadkłada na Chrobrego najpierw).
- **Wzorce:** F2 (mylący new_drop_cosine), F3, F4. Kontrast do A: tu bundel NAPRAWDĘ zły, kara słuszna ale za słaba.

### CASE D — 475700 · Miejska Miska → Upalna 68/16 (Słoneczny Stok) · K-370 Jakub OL
- **Score −20.34 · ACK** (`pool_feasible=1<2` — JEDYNY wykonalny) · **PANEL_OVERRIDE → cid 409**
- **Problem Adriana:** „czemu Jakub miałby jechać do Raju, potem na Nowe Miasto do Goodboya, wracać do
  centrum po Miejską Miskę i wozić Raj ~18 min tylko żeby zbierać kolejne dowozy — zupełnie nielogiczne."
- **Co się dzieje — PICKUP ZYGZAK:** start 14:33 → Raj 14:31 → Goodboy 14:37 (Nowe Miasto) →
  Miejska Miska 14:49 (powrót do centrum, NOWY) → Rumiankowa (dostawa Raj) 15:01 → Ogniomistrz 15:04 →
  Aleja JP2 15:11 → Upalna 15:19 → Choroszczańska 15:29.
  Raj odebrany 14:31, doręczony 15:01 ⇒ **~30 min w bagu**. 4 pickupy w przeciwnych rejonach przed dostawami.
- **DOSTAWY są OK** (`deliv_spread=3.74`, `r1_new_drop_cosine=+0.997` — Upalna idealnie pasuje). Problem to
  ZBIERANIE pickupów: `r5_pickup_detour_total_km=6.98`, `pickup_spread=3.4`, `r8_pickup_span_min=33.0`,
  `r6_max_bag_time=34.4` (worst=475686 Goodboy — na granicy 35 min termiki).
- **Mimo wszystko ACK** bo jedyny wykonalny kandydat (kwestia dostępności floty + nielogiczny plan razem).
- Metryki: `bonus_r1_corridor=-35` (mimo avg_cos=−0.054 → F1 klif), `bonus_r9_stopover=-24`,
  `bonus_penalty_sum=-204.34`.
- **Wzorce:** F6 (główny — over-stacking pickupów / thermal / detour), F1, F4.

---

## ❓ DO USTALENIA W FAZIE NAPRAWY (nie teraz)
1. Case B: plan OR-Tools vs renderowana trasa — która sekwencja realnie wykonywana? (grouping vs powrót)
2. Czy R1 directionality liczyć tylko na dropach OTWARTYCH w momencie startu fali nowego ordera (F2)?
3. Czy `bonus_r1_corridor` zmienić z klifu na gradient + liczyć per-fala zamiast per-cały-bag (F1)?
4. Reguła „no return-to-same-restaurant z jej dowozem w bagu" — gdzie egzekwować (grouper / feasibility veto)? (F5)
5. Penalty/limit za nadmiar pickupów przed dostawami (thermal-aware pickup batching) (F6).
6. Kalibracja kiedy ACK ma być ALERT/odrzucenie (score ujemny, pool=1) (F4).
