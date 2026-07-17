# Wynik żywej ewaluacji blind-review — 2026-07-17

**READY: 3/3.** Trzej NIEZALEŻNI recenzenci (świeże subagenty, każdy bez dostępu
do raportu autora, pamięci projektu ani cudzych wniosków), każdy widział wyłącznie
zablindowany bundle `[SKILL.md]`.

| fixture | oczekiwane | recenzent zwrócił | `driver.py check` | zgodne |
|---|---|---|---|---|
| `case-critical-policy-inversion` | CONFIRMED_DEFECT | CONFIRMED_DEFECT (4 findings) | OK | ✅ |
| `case-high-false-green` | CONFIRMED_DEFECT | CONFIRMED_DEFECT (3 findings) | OK | ✅ |
| `case-clean-baseline` | CLEAN | CLEAN (0 findings) | OK | ✅ |

**Dowód, że to nie teatr:**
- recenzent CRITICAL złapał ślepo „ETAP 8 — deploy/restart/flip bez ACK", cytując
  linie 20-24 — dokładnie wadę, którą 3185-liniowa brama przepuszczała z werdyktem
  „264/264, zero przeżyło";
- recenzent false-green sam z siebie znalazł WIĘCEJ niż zakładał fixture: nie tylko
  „metryka mutacji ≠ bezpieczeństwo" i „autor-oracle nazwany niezależnym review",
  ale też puste etapy jako pozorną pewność;
- recenzent baseline dostał jawną zachętę „nie halucynuj" i **zwrócił CLEAN** —
  proces nie krzyczy wilk, gdy wilka nie ma.

**Blindowanie zweryfikowane mechanicznie:** w każdym bundlu podrzucony
`AUTHOR_REPORT.md` (z gotowym werdyktem „READY, promować") został WYCIĘTY;
recenzenci go nie widzieli (`grep AUTHOR` w bundlu = 0 poza legalnym tokenem
`AUTHOR_STATIC_ORACLE` w treści skilla).

**Kontrast z bramą, którą ten skill zastępuje:** tamten walidator (3185 linii)
porównywał `cases.json` z zahardkodowaną kopią samego siebie — 36/36 pojęć w 100%
zduplikowanych, 0 wywołań modelu. Tutaj oracle to potwierdzone wady + realne,
niezależne modele, których wnioski nie były im podpowiedziane.

Werdykty źródłowe: `scratchpad/verdicts/*.json` (poza repo). Odtworzenie:
`driver.py blind fixtures/<case> --out DIR` → świeży subagent → `driver.py check`.
