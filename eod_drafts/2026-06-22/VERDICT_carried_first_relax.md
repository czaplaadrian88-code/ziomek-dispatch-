# Werdykt: carried-first „po drodze" relax (case Sioux→Wierzbowa, cid=393)

Data: 2026-06-22. Status: **kod za flagą OFF + testy; replay zero-harm; czeka na ACK do wdrożenia na żywo.**

## Problem
Kanon Ziomka twardo wpycha dostawy zleceń JUŻ ODEBRANYCH (`status=picked_up`) na
**front** trasy (`_apply_canon_order_invariants` krok 1, LIVE przez
`ENABLE_PLAN_CANON_ORDER_INVARIANTS=1`; mirror w build_view od 01.06,
`_prioritize_carried_dropoffs`). Reguła nie ma wyjątku na odbiór „po drodze" →
zygzaki. Case: kurier 393 z niesionym Goodboy→Wierzbowa, kanon ustawił
`Wierz.D → Sioux.P` zamiast `Sioux.P → Wierz.D` (Sioux 4 min po drodze).
Skutek: +8 min jazdy, odbiór Sioux 13 min po umówionym, dostawy później.

## Dlaczego carried-first istnieje (NIE usuwać)
Commit 9c1d2ec (01.06): optymalizator geo minimalizuje sumę czasów dostaw i
potrafił wrzucić niesione (stygnące) jedzenie na KONIEC trasy. Replay potwierdził:
**geo-optimum często dowozi niesione późno** — naiwne „ufaj geometrii" psuło
3346 dostaw (carried 37–50 min) vs 1975 poprawionych. Carried-first realnie
chroni jedzenie w tysiącach przypadków.

## Reguła (zwalidowana)
`_relax_carried_first`: wejście = kolejność carried-first. Wśród precedence-poprawnych
permutacji stopów worka wybierz **min-jazda** spełniającą:
1. każde niesione dowiezione w **≤ SOFT_MAX** (domyślnie 20 min) od `picked_up_at`,
2. żadna PRZYPISANA (inna) dostawa nie później niż **+DELAY_TOL** (3 min) vs carried-first,
3. żaden ODBIÓR nie później niż +DELAY_TOL vs carried-first (jedzenie nie czeka dłużej pod restauracją),
4. brak nowego przekroczenia R6 (>35 min w worku);
przyjmij tylko gdy oszczędza **>DRIVE_EPS** (0.3 min) jazdy. Inaczej zostaje carried-first.
**Z konstrukcji: tylko poprawa lub no-op (najgorszy przypadek = obecne zachowanie).**
Niesione jedzenie nigdy nie przekroczy SOFT_MAX (intencja z 01.06 zachowana).

## Replay (harness: `carried_first_replay.py`, import realnych funkcji prod + OSRM)
Źródło: `obj_replay_capture.jsonl` (78 807 rekordów, 18.05→22.06). Aktywne
carried-first: 34 892 (z czego 14.3% >8 stopów = poza zakresem brute-force →
zostają carried-first, zero zmiany). Unikalne sytuacje ≤8 stopów: **29 058**.

Kompletna reguła (4 guardy, w tym „odbiór nie później"), 29 072 sytuacje:

| soft | zmienia | wygrane | HARM_breach | HARM_drive | HARM_deliv>5 | jazda oszcz. | spóźn.odbioru oszcz. | max +inna dostawa |
|---|---|---|---|---|---|---|---|---|
| 18 | 2613 | 2613 | **0** | **0** | **0** | 11 157 min | 22 197 min | 3.0 min |
| 20 | 3374 | 3374 | **0** | **0** | **0** | 15 475 min | 30 301 min | 3.0 min |
| 22 | 4135 | 4135 | **0** | **0** | **0** | 19 900 min | 38 475 min | 3.0 min |
| 25 | 5408 | 5408 | **0** | **0** | **0** | 27 657 min | 50 957 min | 3.0 min |

**0 przypadków szkody na KAŻDYM progu i KAŻDYM wymiarze** (R6, opóźnienie innej
dostawy, jazda, punktualność odbioru). Wszystkie zmiany to wygrane. Najgorsze
opóźnienie INNEJ dostawy = 3.0 min (= limit DELAY_TOL). Domyślny próg **soft=20**:
3374 naprawionych zygzaków, ~15 500 min jazdy + ~30 300 min spóźnień odbioru
zaoszczędzone w 5 tygodni. (Wariant 3-guard bez ochrony odbioru: 3932 wygrane,
ale 558 z nich pogarszało punktualność odbioru → odrzucone 4. guardem.)

Dwie obalone hipotezy po drodze (czemu warto było replayować PRZED kodem):
1. „front-load tylko niesione które w geo-optimum >soft" — psuło (geo-optimum sam
   dowozi niesione późno). 2. „min suma-dostaw + freshness" — opóźniało pojedyncze
   inne dostawy / wydłużało jazdę. Dopiero **min-jazda + twarde guardy Pareto** = zero-harm.

## Implementacja (LIVE-ready, flaga OFF)
- `plan_recheck.py`: flagi `ENABLE_CARRIED_FIRST_RELAX` (OFF) + `..._SOFT_MAX_MIN`=20 /
  `..._DELAY_TOL_MIN`=3 / `..._DRIVE_EPS_MIN`=0.3 / `..._MAX_STOPS`=8 (env-overridable);
  `_relax_carried_first` + wpięcie na końcu `_apply_canon_order_invariants(stops, orders_state, pos, now)`
  (oba call-site: `_gen_one_bag_plan` + `_retime_one_bag_plan`).
- Test: `tests/test_carried_first_relax.py` (7/7) — Sioux fix, ochrona starego jedzenia,
  no-op gdy optymalne, ≤SOFT_MAX, determinizm, flaga-OFF identyczność.
- Backup: `plan_recheck.py.bak-pre-carried-relax-2026-06-22`.

## Do wdrożenia (ACK-gated) — spójność POWIERZCHNI
Kanon to źródło prawdy (plan → konsola + Telegram + apka). ALE apka build_view ma
`ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=0` → **re-front-loaduje** plan kanonu
(`_prioritize_carried_dropoffs`, courier_orders.py:1119). Sam flip kanonu da
rozjazd: konsola/Telegram = naprawione, apka = dalej zygzak. Opcje:
- (A) flip `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1` (apka ufa kanonowi verbatim — intencja
  flagi F5; wymaga walidacji że TRUST=1 bez relaksu = no-op = kanon≡build_view dziś), albo
- (B) mirror `_relax_carried_first` w courier_orders.py (jak `_repair_dropoffs_after_pickups`).
- Konsola (`_build_route`/fleet_state, repo nadajesz_clone/panel) — sprawdzić że renderuje
  kolejność planu verbatim (nie re-front-loaduje).

## Plan flipu (po ACK)
1. `ENABLE_CARRIED_FIRST_RELAX=1` w env dispatch-shadow + dispatch-plan-recheck → restart.
2. Spójność apki (A lub B) + restart courier-api. 3. Monitor `grep CARRIED_FIRST_RELAX_applied`
   + obserwacja peak. Rollback: flaga=0 + restart (hot, ~5s). SOFT_MAX można zacieśnić do 18.
