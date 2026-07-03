# ZIOMEK — DEFINITION OF DONE (jeden ekran)

> **STATUS: ZATWIERDZONY 01.07.2026** (nagłówek zaktualizowany 03.07.2026, audyt N3). Skrót #0 do sprawdzenia na KOŃCU każdej zmiany. Pełny protokół: `memory/ziomek-change-protocol.md` (ETAP 0→7, C1-C11). Kontrakty: [[ZIOMEK_ARCHITECTURE.md]] · inwarianty: [[ZIOMEK_INVARIANTS.md]].

## ✅ 7 ptaszków — zmiana jest UKOŃCZONA gdy WSZYSTKIE zielone
1. **U ŹRÓDŁA, nie na krawędzi** — fix w właściwej z 10 warstw; „to tylko display" UDOWODNIONE grepem każdego konsumenta (scoring? feasibility? committed? inny solver?).
2. **WSZYSTKIE bliźniaki RAZEM** — sprawdziłeś rejestr bliźniaków (ARCHITECTURE §4); każda kopia dotknięta lub N-D+powód. Nic „na wszelki wypadek".
3. **HARD ≥ SOFT** — SOFT nie osłabia HARD; P0 feasibility przed scoringiem; świadome inwersje P-1..P-7 nietknięte bez ACK.
4. **Flaga ON≠OFF (test)** + metryka w `shadow_decisions.jsonl` (`grep -c` > 0) + parytet bliźniaków (test) + checkery flag/inwarianty zielone.
5. **PEŁNA regresja Ziomka** (`pytest tests/` z venv `dispatch`, vs BIEŻĄCY baseline — 2026-07-03: **4109 passed / 0 failed**; liczba rośnie z nowymi testami, porównuj z ostatnim zielonym biegiem, nie z tym nagłówkiem) + e2e przez WSZYSTKIE dotknięte warstwy (nie tylko unit klastra).
6. **DOWÓD POZYTYWNEGO wpływu** — replay ON↔OFF, metryka docelowa MIERZALNIE lepsza (≥2% netto, regresja rozliczona); refaktor bez zmiany zachowania → dowód bajt-identyczności. + okno 2 dni.
7. **ROLLBACK gotowy** (flaga=false / .bak / git revert) PRZED ryzykiem.

## 🚫 Bramka ANTY-ENTROPII (Adrian: „każda sesja zostawia entropię NIŻSZĄ niż zastała")
Zmiana, która ZWIĘKSZA którąkolwiek z 8 metryk entropii = **NIEUKOŃCZONA**, choćby działała. Konsoliduj, nie dodawaj. RED-check — ODRZUĆ jeśli zmiana wprowadza:
- nową powierzchnię renderu kolejności/ETA (bez importu kanonu)
- nową flagę decyzyjną poza `ETAP4_DECISION_FLAGS`
- nowe re-liczenie czasu-odbioru bez `available_from`
- nowy klucz HARD bez widoczności-decyzji (serializer A+B)
- nowy reader stanu bez rotation/master
- nowy `.txt`/cache bez TTL
- nowy próg-kopię bez nazwanej-stałej
- nowy caller geometrii z `if coords:` (zamiast `_valid()`)
- nową kalibrację luzującą HARD bez outcome-join
- nowy plik multi-writer bez fcntl
- nowy void-claim bez świeżego grepa master-ledgera

## 📏 Miernik (po każdej naprawie fundamentu)
Re-run `tools/entropy_dashboard.py` → **liczby mają MALEĆ** (D4 strażników rośnie). Zmiana bez ruchu miernika w dobrą stronę = brak progresu.

## 🚦 Kiedy PYTAĆ Adriana (nie zgaduj)
- dotyka inwersji HARD↔SOFT (P-1..P-7) lub ⛔ w roadmapie
- flip flagi / restart / deploy silnika / peak / re-enable Telegrama (C2 = pełny deploy)
- konflikt priorytetów / niejasna precedencja (→ `memory/ZIOMEK_REGULY_KANON.md` najpierw)
- masz zbudować >30 linii kodu silnika → to jest praca kodowa (nie architektura) — potwierdź zakres

## ⚙️ Mechanika deployu (ETAP 6, tylko za ACK)
`.bak` → `py_compile` + import → test (kanoniczna ścieżka, NIE worktree) → `git log -3` (kolizja sesji, C1-git: commit atomowo jawne ścieżki) → ACK → 1 restart (NIGDY telegram/peak bez OK) → logi → aktualizuj ref/memory.
