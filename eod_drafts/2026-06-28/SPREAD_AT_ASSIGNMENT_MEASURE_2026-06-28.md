# Rozrzucanie przy przydziale — pomiar rozmiaru (2026-06-28)

**Geneza (Adrian 28.06):** „jeśli dwa dalekie zlecenia, po co dwóch wysyłać i tracić ich na 35min —
to pogarsza jakość, bo nie trzeba będzie czekać tyle na wolnego kuriera". Czyli: czy SILNIK przy
PIERWSZYM przydziale za bardzo rozrzuca bundlowalne zlecenia na osobnych kurierów (zżera rezerwę),
zamiast bundlować na jednego (drugi zostaje wolny na napływ). Profilaktyka — przerzut leczy objaw.

## Metoda + uczciwa progresja (dyscyplina kalibracji C9/C11 — ucinanie napompowania na każdym kroku)
1. **Surowo (BŁĄD):** pary przypisane ≤12min, bliski pickup+dostawa → 83% rozrzucone. ⚠ użyłem okna
   PRZYPISANIA — a bundlowalność zależy od okna ODBIORU (`czas_kuriera`). Para #483775(17:28)+#483790(18:05)
   = odbiory 37min od siebie → fizycznie nie do zbundlowania. Confound złapany.
2. **Okno odbioru (`czas_kuriera` ≤10min):** same-restaurant **74% ZBUNDLOWANE** (silnik `same_restaurant_grouping`
   działa!), reszta rozrzutu = głównie CROSS-restaurant (różne bliskie restauracje).
3. **Walidacja R6 (OSRM + committed + dwell, `scratchpad/validate_bundle_r6.py`):** 198 cross-rest par
   → **100% R6-WYKONALNYCH** (max bag-time bundla 15-27min ≤35). ⚠ to UPPER BOUND (zakłada kuriera tylko
   z tą parą; ignoruje inne worki + sekwencyjny napływ). R6 NIE jest blokerem — jest zapas.
4. **DOKŁADNY ROZMIAR (`scratchpad/measure_spread_size.py`):** klastry (union-find) bundlowalnych +
   JEDNOCZEŚNIE-przydzielalnych (oba ≤10min przydziału, ŻADEN nie odebrany zanim drugi przydzielony),
   konserwatywnie tylko pary (ceil(K/2) kurierów wystarczy): **≈26 ekstra kurierów-kursów/dzień**
   (44 klastry, 24 z marnotrawstwem; lower bound — z trójkami więcej). 119 krawędzi (z 268 surowych).

## Werdykt
- **Silnik bundluje same-restaurant dobrze (74%)** — nie jest zepsuty.
- **Rozrzut to głównie cross-restaurant**, R6-wykonalny do bundla (jest zapas), więc to NIE twarde
  ograniczenie tylko **miopia floty: zachłanny per-zlecenie przydział do najlepszego WOLNEGO** (+ sekwencyjny
  napływ: O2 wpada gdy kurier O1 już ruszył) — TA SAMA choroba co palenie wolnych przy przerzucie.
- **Rozmiar: ~15-30 uwolnionych kursów/dzień (lower 26), głównie peak** (gdzie median 1 wolny → materialne
  dla rezerwy/jakości nadchodzących). Modest, nie transformacja. ~7-8% kursów / ~15 kuriero-h/dzień.
- ⚠ 26 wciąż lekko optymistyczne (zakłada pojemność/pozycję kurierów-do-bundla). Jeden dzień.

## Rekomendacja
**Średni priorytet, NIE pilne.** Lewar = „hold-and-bundle" / „przydzielaj do JADĄCEGO w tym kierunku
zamiast palić wolnego" = duży sprint rdzenia selekcji (P0-klasa, przykazanie #0, wiele warstw). Koszt
wysoki vs ~26 kursów/dzień. **Live reassignment-bundling (oszczędność→busy, zbudowany 28.06) zbiera
runtime'ową część** — najpierw obserwować ile realnie zbiera, potem decydować o sprincie przydziału.
Przed budową: dokładniejszy pomiar przez REPLAY `assess_order` (realne worki + pozycje + sekwencyjny napływ)
zamiast izolowanego modelu OSRM.

Skrypty (scratchpad, do odtworzenia): `measure_spread.py`, `validate_bundle_r6.py`, `measure_spread_size.py`.
Powiązane: reassign-ghost sprint (`REASSIGN_GHOST_SPRINT_SPEC.md`), [[reassignment-forward-shadow-v2]].
