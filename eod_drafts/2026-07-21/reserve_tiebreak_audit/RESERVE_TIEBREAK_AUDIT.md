# RESERVE_TIEBREAK_AUDIT — audyt obserwacyjny (READ-ONLY)

**Data:** 2026-07-21 · **Charakter:** krok 2 drabiny nauki #22 (obserwacja, ZERO zmian kodu/flag)
**Obiekt:** `dispatch_v2.dispatch_pipeline._reserve_aware_tiebreak_eval` (shadow-only)
**Dane:** `logs/shadow_decisions.jsonl` (+ `.jsonl.1` rotacja, diagnostycznie) · `dispatch_state/learning_log.jsonl`
**Skrypt:** `analyze.py` (obok) · surowy wynik: `analyze_output.txt`

---

## 1. Jak mechanizm działa (kod + telemetria)

**Definicja:** `dispatch_pipeline.py:3125-3170` (`_reserve_aware_tiebreak_eval`).
**Call-site:** `core/selection.py:243-257`, za bramką flagi
`ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW` (**stan: ON** w `flags.json`).
**Serializacja telemetrii:** top-level pole `reserve_tiebreak_shadow` w
`shadow_dispatcher.py:1106` (→ `shadow_decisions.jsonl`). `RESERVE_TIEBREAK_MARGIN`
= `common.py:617` = **30.0** (nie ma w flags.json → używana stała).

**Warunki odpalenia (`would_fire=True`), po kolei z kodu:**
1. `dispatch_pipeline.py:3137` — zwycięzca (`_winner`) MUSI być WOLNY: `bag_size_before == 0` (inaczej `winner_free=False`, koniec).
2. `dispatch_pipeline.py:3142` — kandydat rozważany tylko gdy **ten sam tier late-pickup** co zwycięzca (`lp_tier_fn(c) == wtier`) — brak inwersji committed-odbioru.
3. `:3144` — kandydat MUSI już wieźć (`bag_before(c) >= 1`, „jadący").
4. `:3147` — pomiń jawnie zablokowanych przez V325 (`_v325_score_blocked`) lub bez score.
5. `:3152` — **pomiń kandydata gdy `max_bag_time_min > 40.0`** (to miejsce defektu — patrz §4).
6. `:3154` — margines score: `(winner.score - c.score) <= 30.0`. Uwaga: bramka jednostronna — przepuszcza też kandydatów o score WYŻSZYM od wolnego (Δ ujemna).
7. Jeśli został ≥1 taki „jadący" → `would_fire=True`, wybór = jadący o najwyższym score.

**Pola zapisywane gdy `would_fire=True`:** `winner_cid` (wolny), `carry_cid`
(jadący, którego dołożyłby tie-break), `carry_bag_before`,
`carry_r6_max_bag_time_min`, `dscore_free_minus_carry`, `same_late_pickup_tier`,
`n_carrier_candidates`. **ZERO mutacji** decyzji — feasible/winner nietknięte,
to czysty obserwator.

---

## 2. Częstotliwość i rozkład (POST-A8: ts ≥ 2026-07-19T23:39:21Z)

Okno post-A8 w bieżącym pliku: **209 decyzji**, z czego **182** mają pole
(flaga ON), **98** = winner_free (zwycięzca wolny).

| Metryka | POST-A8 | PRE-A8 (bieżący, diag.) | Rotacja .1 (diag.) |
|---|---|---|---|
| decyzji w oknie | 209 | 870 | 1161 |
| winner_free=True | 98 | 694 | 940 |
| **would_fire=True** | **10** | 31 | 55 |
| % fired / winner_free | 10.2% | 4.5% | 5.9% |
| carry R6 max (min) | 28.9 | 29.8 | 29.5 |
| carry R6 avg (min) | 18.1 | 16.7 | 18.0 |
| **defekt 35-40 (szt.)** | **0** | **0** | **0** |

- **Ile razy wskazuje innego kandydata niż best silnika:** z definicji `would_fire=True` ⇒ jadący ≠ zwycięzca, więc **wszystkie 10** przypadków post-A8 to wskazanie innego kandydata (o to chodzi w mechanizmie).
- **Rozkład per pora dnia (Warsaw), fired post-A8:** 11h=6, 16h=1, 17h=3 — skupienie w lunch peak.
- **Per cid — wolny (winner):** 179×3, 370×3, 503×2, 484×1, 538×1. **Per cid — jadący (carry):** 400×2, 531×2, 284×2, 541×2, 447×1, 370×1.
- **n_carrier_candidates:** w 8/10 był tylko 1 jadący-kandydat, w 2/10 — dwóch (wybór max-score).
- **dscore_free_minus_carry:** avg −484.8, zakres [−868.7 ; +27.5]. Ujemne = jadący ma score WYŻSZY niż wolny zwycięzca (silnik wybrał wolnego mimo lepszego jadącego — to właśnie sytuacja „rezerwy"). Uwaga: w rotacji widać sentinel −1e9 (score-block), post-A8 bez sentineli.

---

## 3. Zestawienie z nadpisaniami ownera (join po order_id)

Join fired post-A8 (n=10) z `learning_log.jsonl` (PANEL_OVERRIDE/PANEL_AGREE):

- 8/10 → **PANEL_OVERRIDE** (człowiek nadpisał propozycję), 1/10 → PANEL_AGREE, 1/10 → brak śladu.
- W tych 8 nadpisaniach:
  - owner wybrał **JADACEGO (= dokładnie carry tie-breaka): 3**
  - owner wybrał **WOLNEGO (= best silnika): 0**
  - owner wybrał **kogoś INNEGO: 5**

**Interpretacja (uczciwie, n=8 — ZA MAŁO na wniosek):** kierunek jest spójny z
hipotezą „owner nie trzyma wolnego w takiej sytuacji" — w **0/8** przypadków
człowiek zostawił wolnego zwycięzcę silnika. Ale tie-break trafił w KONKRETNEGO
jadącego tylko **3/8**; w 5/8 owner wskazał jeszcze innego kuriera. Czyli dane
mówią raczej „owner odrzuca wolnego best-silnika", niż „owner preferuje właśnie
tego jadącego, którego typuje tie-break". Przy n=8 to sygnał, nie dowód —
potrzeba tygodni akumulacji zanim liczby cokolwiek rozstrzygną.

---

## 4. Defekt R6 ≤ 40 wskazany przez Sola — WERDYKT

**POTWIERDZONY W KODZIE, NIEZMATERIALIZOWANY W DANYCH.**

- **Dowód kodowy:** `dispatch_pipeline.py:3152` → `if mb is not None and mb > 40.0: continue`. Guard odrzuca dopiero >40, więc **przepuszcza kandydatów z wynikowym bag-time w (35, 40]** — strefie ALARMOWEJ. Kanon: `common.py:1336 BAG_TIME_HARD_MAX_MIN = 35`, a `common.py:1928` „HARD BAG_TIME > 35 min (R6) pozostaje — to SINGLE hard constraint". Zgodnie z OD-07: 35 = norma, 40 = tylko-alarm. Tie-break używa więc budżetu +5 min ponad twardą normę R6 jako rutynowego budżetu — bez ACK, wbrew OD-07. `mb` = `max_bag_time_min` kandydata liczonego JUŻ Z dołożonym zleceniem, więc realnie mechanizm mógłby zepchnąć bundle w świeżość 35-40.
- **Dowód danych:** we WSZYSTKICH oknach (post-A8, pre-A8, rotacja) **0** fired-przypadków miało `carry_r6_max_bag_time_min` w (35,40]. Maks. zaobserwowana wartość ≈ 29.8 min; średnia ~17-18. Cała masa fired siedzi ≤ ~30 min, z dużym zapasem do 35.

Innymi słowy: bramka jest zbyt luźna o 5 minut względem kanonu (błąd realny), ale
w dotychczasowym ruchu ani razu nie „ugryzła" — jadący-kandydaci w marginesie
score mieli i tak niski bag-time. Ryzyko jest **latentne**: pojawi się dopiero,
gdy w wąskim marginesie score trafi się jadący z wynikowym bag-time 35-40
(gęstszy ruch / większe bundlé). Mechanizm i tak jest shadow-only (nic nie
przełącza), więc defekt nie wpływa dziś na produkcję — jest długiem do usunięcia
PRZED ewentualnym flipem na żywo.

---

## 5. Rekomendacja następnego kroku (bez implementacji)

1. **Poprawić guard u źródła przed jakimkolwiek flipem:** `mb > 40.0` → `mb > 35.0`
   (albo stała `BAG_TIME_HARD_MAX_MIN`), by tie-break nie sięgał strefy alarmowej —
   zgodnie z OD-07. Zmiana idzie protokołem #0 (bliźniak: sprawdzić czy nie ma
   drugiej kopii tej samej logiki 40-cap w best_effort/objm_lexr6). Ponieważ pole
   jest shadow-only, poprawka jest nisko-ryzykowna, ale i tak per #0.
2. **Nie flipować na żywo na obecnych danych:** n(fired)=10 post-A8, join n=8 —
   za mało, by udowodnić POZYTYWNY wpływ (wymóg #0 etap 5). Zbierać dalej w cieniu
   ≥2 tygodnie, aż będzie kilkadziesiąt fired z joinem do override.
3. **Uczciwa hipoteza do dalszego pomiaru:** „owner deprioritetyzuje wolnego
   best-silnika" ma wstępne wsparcie (0/8 zostań-przy-wolnym), ale „owner preferuje
   tego konkretnego jadącego" — nie (3/8). Kolejny krok drabiny: gdy n urośnie,
   policzyć zgodność owner↔carry vs owner↔winner na większej próbie i rozbić per
   pora/gęstość ruchu.
4. **Rozważyć telemetrię, której brakuje:** log nie zapisuje bag-time wynikowego
   PO stronie wolnego zwycięzcy ani ile jest wolnych w rezerwie w danym momencie —
   bez tego nie ocenimy realnej „oszczędności rezerwy". Do dorzucenia przy okazji
   poprawki guardu.
