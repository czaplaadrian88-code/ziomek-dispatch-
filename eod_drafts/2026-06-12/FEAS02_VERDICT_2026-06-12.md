# FEAS-02 — WERDYKT: NIE-ROBIĆ w kluczu selekcji; dźwignia = adopcja GPS + GPS_AGE_DISCOUNT

**Data:** 2026-06-12 (nocna sesja, kontynuacja po SEL-01)
**Finding (audyt 03.06):** no_gps/blind EMPTY wygrywa 16-21% decyzji z fikcyjnej
pozycji BIALYSTOK_CENTER; REKO m.in. „nie pozwalać wygrać gdy istnieje
JAKIKOLWIEK informed feasible".

## Stan po LAST-KNOWN-POS (flip 08.06)

- no_gps jako best: pre 8,0% (n=1523) → post 11,4% (n=501). Store NIE zjadł
  problemu, bo **adopcja GPS się zawaliła** (źródło `gps` w best: 368 → 13!) —
  store ratuje tylko kurierów z pozycją ≤25 min wstecz; kurier bez GPS cały
  dzień dalej spada do fikcji centrum.
- `pos_from_store` w best: 11/131 na bieżącym pliku — mechanizm działa, ale
  ma mało paliwa.

## Replay „informed zawsze przed blind" (2024 PROPOSE, 02-11.06)

- blind/pre_shift/none jako best: **318 (15,7%)** — zgodne z audytem.
- **60% z nich NIE MA żadnej informed alternatywy w topie** (scarcity —
  klucz nic nie zmieni).
- Pozostałe 126: flip na najlepszego informed kosztuje **medianę 111 pkt
  score (p90=380, max=sentinel 1e9)**; informed alternatywa ma ujemny score
  w 116/126, sentinel w 10/126, a **GORSZY tier late-pickup w 126/126**.

To jest dokładnie patologia SEL-01 (leksykograficzny wymiar przed score),
tylko ostrzejsza: 100% flipów nadpisywałoby uzasadnienie late-pickup,
mediana kosztu 111 pkt vs 12 pkt przy SEL-01.

## Werdykt

1. **NIE dodawać twardego „informed-first" do klucza selekcji.**
2. Fikcja pozycji to problem DANYCH, nie klucza. Dźwignie (wszystkie już
   istnieją): (a) **ops: adopcja GPS 18→60%** (zadanie z audytu 10.06 —
   po zawale `gps` 368→13 w best to jest PRIORYTET); (b) **GPS_AGE_DISCOUNT**
   (shadow uzbrojony 11.06, flaga OFF) — flip po rolloucie apki v2 da
   gradientowe dyskonto zamiast binarnej fikcji; (c) last-known-pos store
   (LIVE) zacznie realnie działać gdy (a) da mu paliwo.
3. Demote blind+EMPTY (bucket 2) już jest w kluczu i działa — reszta
   przypadków to scarcity i late-pickup-uzasadnione wybory.

Skrypt replay: inline w sesji (predykaty jak `sel01_direction_key_replay.py`).
