# SPEC — Eskalacja best_effort 3-stopniowa (reguła Adriana)

**Data:** 2026-06-23 · **Status:** SPEC + SHADOW v1 (log-only) · selekcja produkcyjna NIE zmieniona
**Kontekst:** case #482817 + sesja 23.06 — Ziomek w przeładowaniu wciska nowe do pełnych worków
(psuje wiezione jedzenie), zamiast eskalować jak człowiek robi to ręcznie (i jak próbowano 16.05).

---

## 1. Reguła docelowa (3 stopnie, słowa Adriana)

1. **TIER 1 — zmieść wszystko:** dowieź wszystko ≤35 min (worek) i nie spóźnij odbioru. [feasible]
2. **TIER 2 — pierwszy wolny:** jeśli się nie da, **dołóż worek kurierowi, który NAJWCZEŚNIEJ
   zwolni** się (min `free_at`). Odbiera nowe **po rozładowaniu obecnego** → obecne nietknięte,
   nowe ma własny licznik 35 od (późniejszego) odbioru. Koszt: klient czeka dłużej, NIC nie ginie.
3. **TIER 3 — kontrolowane rozciągnięcie:** jeśli i to nie wystarcza (dzień jak 16.05, realny brak
   kadry) → wydłuż **odbiór do +10 min** i **dowóz (worek) do 40 min**, minimalizując straty.

Cel = minimalizować **zmarnowane jedzenie** (>35 w aucie), nie czas. Kolejność: 1 → 2 → 3.

---

## 2. Co robimy TERAZ vs docelowo

| | TERAZ (live) | DOCELOWO |
|---|---|---|
| **Tier 1** | ✅ `BAG_TIME_HARD_MAX=35` + `LATE_PICKUP_HARD_MAX=5` | bez zmian (odbiór 5, nie 0) |
| **Tier 2** | ⚠️ mechanizm `_soon_free_probe` ISTNIEJE ale flaga `ENABLE_SOON_FREE_CANDIDATE=OFF` + okno tylko **≤12 min** | pierwszy-wolny BEZ limitu 12, selekcja = min `free_at` |
| **Tier 3** | ❌ brak kontrolowanego stopnia; >35/>5 wpada w **ślepy best_effort** (`_best_effort_sort_key`, PRIMARY=`r6_per_order_violations` = ślepy na carry) | kontrolowane +10 odbiór / 40 worek, carry-aware |
| **Kolejność** | ❌ feasible → **od razu** ślepy best_effort → SOLO → KOORD | feasible → Tier2 → Tier3 |

Skutek dziś: w przeładowaniu Ziomek przeskakuje Tier 2 i wpada w ślepe wciskanie — wybiera
załadowanego, którego nowe „się mieści" (r6_pov=0), nie widząc że psuje mu wiezione (np. 482800 = 93 min).

---

## 3. Jak by to poprawiło propozycje (dane z logów, 06-11→06-23)

- **W 56% (132/234) decyzji best_effort ktoś zwalnia się WCZEŚNIEJ** niż kurier, którego Ziomek
  wybiera. To są kandydaci na Tier 2.
- **7% decyzji ma wolnego od ręki** (free_at≈0); w **12** z nich Ziomek wybrał ZAŁADOWANEGO mimo
  wolnego — np. 482601/482603: wybrany 393 (4 zlecenia, psuje **52-61 min**) vs wolny 515 (carry 3).
- **Ślepe wciskanie psuje wiezione do 190 min** (cid 457 dostawał 4× zlecenia psujące worek 120-196 min).
- **#482817:** pierwszy-wolny = **393** (za 64 min). Brak wolnego ≤30 → Tier 3 → carry-aware = **531**
  (= dokładnie ręczny wybór koordynatora). Czyli reguła trafia w wybór człowieka.

---

## 4. Shadow v1 — co wpięte (log-only, ZERO zmiany werdyktu)

`_best_effort_objm_shadow` (`dispatch_pipeline.py`) liczy w gałęzi best_effort i pisze do
`best.metrics` (prefix `best_effort_objm_*` auto-serializowany). Flaga `ENABLE_BEST_EFFORT_OBJM_SHADOW`.

Pola eskalacji (nowe):
- `best_effort_objm_t2_cid` / `_t2_free_min` / `_t2_bag` — Tier 2 (pierwszy-wolny = min free_at) + za ile zwolni + jego worek.
- `best_effort_objm_esc_tier` (2/3) — który stopień rekomenduje (Tier2 gdy free_at ≤ `BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN`, domyślnie 30; hot flags.json).
- `best_effort_objm_esc_cid` / `_esc_vs_live` — rekomendowany kurier + czy ≠ live.
- Tier 3 (już było): `_cid`/`_raw_cid`/`_cap_min`/`_d_r6`/`_d_newbag` — carry-aware cap-stretch.

### Zakres v1 vs v2
- **v1 (TERAZ):** mierzy SELEKCJĘ eskalacji — kogo wskazałby Tier 2 (pierwszy-wolny) vs Tier 3 vs
  live, i za ile zwolni się pierwszy-wolny. To największa dźwignia (56% rozjazd).
- **v2 (później, wymaga re-runu):** policzenie nowego jako odbiór PO rozładowaniu (substytucja
  `soon_free` bez limitu 12) → faktyczny worek nowego ≤35 + potwierdzenie 0 naruszeń obecnego +
  efekt „+10 odbiór / 40 worek" na FEASIBILITY (ile zleceń Tier 3 by uratował). v1 NIE rusza bramek.

---

## 5. Plan measure-first → live

1. **Shadow v1** (jest) — kilka peaków → przegląd: ile decyzji Tier2 vs Tier3, za ile zwalnia
   pierwszy-wolny (rozkład), ile carry-psucia by zniknęło.
2. **Próg Tier 2** (`ESC_TIER2_MAX_FREE_MIN`) skalibrować z danych (jak długo klient może czekać
   vs ile jedzenia ratujemy). Hot w flags.json.
3. **v2 re-run** — potwierdzić 0 naruszeń obecnych worków + worek nowego ≤35 po rozładowaniu.
4. **Live flip** = osobna flaga `ENABLE_BEST_EFFORT_ESCALATION` + ACK, dopiero po pkt 1-3.

## 6. Rollback
Shadow: flaga `ENABLE_BEST_EFFORT_OBJM_SHADOW=0` (hot) / `rm` `.bak-pre-besteffort-objm-shadow-*` + restart.
Progi (`ESC_TIER2_MAX_FREE_MIN`, `CAP_MIN`) — liczby w flags.json, hot.

## 7. Progi (decyzja Adriana 23.06) — LIVE w flags.json (hot)
- **Tier 3 worek cap = 40** (`BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40`). Stretch do 40 TYLKO w Tier 3.
- **Próg Tier 2 = 90 min** (`BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN=90`): czekaj na pierwszego-wolnego
  aż do 90 min; Tier 3 (stretch 40) dopiero gdy **nikt nie zwolni się <90 min** (= „wszyscy mają
  2 worki, kurier nie przyjedzie wcześniej niż za 90 min"). Reguła Adriana.
- **Tier 3 odbiór +10:** dziś hard limit 5; relaksacja do 10 = zmiana bramki feasibility (v2).
- (opcja) log sygnału „flota pełna" (min bag w puli) by potwierdzić warunek „2 worki" gdy Tier 3 fire.
