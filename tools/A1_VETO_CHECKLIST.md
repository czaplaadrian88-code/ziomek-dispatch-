# A1 STRAŻNIK — checklist VETO do PR-ów (advisory Faza 6.2)

Strażnik **BLOKUJE** merge, gdy PR łamie kanon. Odpalaj PRZED każdym flipem/mergem dotykającym silnika, feasibility, scoringu, kanonu trasy, capów, trybów.

## Komendy (systemowy python3 — check statyczny, zero importu silnika)
```
python3 tools/canon_static_check.py            # 0 = kanon czysty; 1 = VETO
python3 tools/canon_static_check.py --selftest # sondy mutacyjne (wszystkie KILLED)
venvs/dispatch/bin/python -m pytest tests/test_canon_static_check_a1.py -q
```

## Bramki VETO (każda „NIE" = blok merge)
1. **R6 = 35** (`BAG_TIME_HARD_MAX_MIN`, common.py, JEDNO źródło). Jedzenie NIGDY > 40. 40 = wyłącznie eskalacja ALARM/S3, nie tier świeżości.
2. **R27 = ±5** (`V3274_FROZEN_PICKUP_WINDOW_MIN`, `OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN`). ±10 tylko w S3.
3. **Eskalacja ratunkowa = 40** (`BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN`) — cap SELEKCJI, nie cel.
4. **Dial O2 = 35** (`O2_OVERAGE_CAP_MIN`) — parytet instrument↔silnik.
5. **Capy worka:** sanity = 8 (`MAX_BAG_SANITY_CAP`); per-klasa `HARD_TIER_BAG_CAP` gold6/std+6/std5/slow4/new4.
6. **RATCHET mode-consistency:** `BAG_TIME_HARD_MAX_MIN =` definiowane TYLKO w common.py. Druga definicja w innym pliku silnika = VETO (przyszły relaks trybu W1 idzie przez usankcjonowany dial, nie przez drugą stałą — SOFT nie osłabia HARD).
7. **RATCHET parytet kanonu trasy:** `_apply_canon_order_invariants` TYLKO w plan_recheck.py (w TYM repo). Kopie konsola/apka są w innych repo — piąta kopia tu = VETO.
8. **Lock-atomowość claim-ledger:** `tentative_assign` nie mutuje wejścia; claim-na-claim kumuluje warstwowo; nieznany cid = no-op. (`test_l6c_geometry_claim.py` + `test_canon_static_check_a1.py`.)
9. **Defer-completion (W1):** gdy pojawi się moduł deferu, `test_defer_completion_guard_armed_when_defer_exists` PADNIE — wykonawca W1 MUSI uzbroić inwariant „zero zleceń-sierot", nie skasować test.
10. **Nowy strażnik HARD = behawioralny + mutation-check** (C13): string-match nie łapie inwersji; ≥2 niezależne kille per bramka; sonda przeżywająca = VOID.

## Zasada dla mutacji sond (C14)
Sekwencja: edycje → testy zielone → commit → sonda → test PADA → restore → `git diff --stat == 0`. Sonda, która NIE zabija testu → STOP, zdiagnozuj czemu przeżyła, wzmocnij test/oracle. Nie „na oko".

## Raport VETO (format ≤10 linii)
`⛔ A1 VETO — naruszenia kanonu (N): • <dial>=<got> ≠ kanon <exp> [<plik>] — <reguła>`
Brak naruszeń → `✓ kanon nienaruszony`.
