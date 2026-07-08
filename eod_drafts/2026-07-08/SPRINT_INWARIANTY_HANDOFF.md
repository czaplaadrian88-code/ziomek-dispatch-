# HANDOFF — SPRINT B „Bezpieczniki alokacji: uzbrojenie pustych slotów inwariantów (zakres NIE-ETA)"
**Sesja-wykonawca: tmux 37. Data: 2026-07-08. Baseline: master `6e1af23` (kanon 4448/0).**
**Twój worktree (PRACUJ TYLKO TU): `/root/.openclaw/workspace/scripts/wt-invariants` (branch `quality/invariants-alloc`).**

---

## 0. ZANIM COKOLWIEK TKNIESZ — PROTOKÓŁ #0 (obowiązkowy)
Wklej sobie na start `memory/ziomek-change-protocol.md` i przejdź ETAP 0→7. Skróty krytyczne:
- **ETAP 0:** `cd` do swojego worktree → potwierdź stan na żywo → **odpal baseline `pytest tests/` i potwierdź ZIELONE (≈4448/0)** ZANIM cokolwiek zmienisz.
- Fix **U ŹRÓDŁA**. **MAPA KOMPLETNOŚCI** — wszystkie miejsca danej klasy inwariantu, **bliźniacze ścieżki RAZEM**. Dowody, nie deklaracje. Zmiana częściowa = niezakończona.
- **Żaden flip/restart/flags.json bez ACK Adriana.** Budujesz OFF/log-loud, twarda blokada dopiero po dowodzie zero fałszywek.

## 1. CEL (co i po co)
Uzbroić **puste sloty inwariantów** w ścieżce ALLOCATION/FEASIBILITY — miejsca gdzie kod POWINIEN pilnować „to musi zawsze zachodzić", a nie pilnuje. To korzeń nawrotów typu „carried-first naprawiane 10×": bug wraca, bo żaden bezpiecznik go nie łapie. Uzbrojenie = zła trasa łapana i blokowana w momencie powstania, zamiast trafiać do kuriera.
**Referencja slotów:** `ZIOMEK_INVARIANTS.md` (~21 slotów; ~5 już uzbrojonych: AVAILABLE-FROM / LEXQUAL / PICKUP-FLOOR / TWIN-LEXQUAL / TWIN-SLA-ANCHOR). Ty bierzesz pozostałe **ALLOCATION/GEOMETRY**.

## 2. ZAKRES — KTÓRE SLOTY
**WOLNO uzbrajać (nie-ETA):** carried-first (kurier z jedzeniem nie zawraca do restauracji), geometria `global_allocate` (brak ślepoty na geometrię/zygzak), spójność claim-ledger, oraz pokrewne alokacyjne sloty korektności.
**⛔ NIE tykaj slotów ETA-owych:** PICKUP-FLOOR, SLA-ANCHOR (i ich bliźniaki), cokolwiek o czasie odbioru/dostawy/obietnicy. **Powód: kalibracja ETA dojrzewa w cieniu (`eta_calib_*`) i ~10.07 może przedefiniować obietnicę — uzbrojenie asercji ETA teraz = strzał do ruchomego celu.** Te sloty czekają na osobny sprint po ustabilizowaniu ETA.

## 3. ZAKRES PLIKÓW
**WOLNO:** asercje w feasibility/allocation/geometrii (carried_first_guard, global_allocate, claim ledger), `ZIOMEK_INVARIANTS.md`, testy w `tests/`, docs/eod_drafts.
**NIE WOLNO (twarde granice anty-kolizyjne):**
- ⛔ `route_simulator_v2` — **TYLKO DO ODCZYTU** (współdzielona ze Sprintem A i insercją; asercja może CZYTAĆ jego wynik, nie modyfikować pliku).
- ⛔ Warstwa solvera OR-Tools / config solvera — należy do Sprintu A (perf).
- ⛔ Sloty/logika ETA/pickup/SLA (patrz §2).
- ⛔ `flags.json` — nie dotykasz.

## 4. WATCHPOINTY (dlaczego nie kolidujesz z resztą)
- Kalibracja ETA = cień; Ty pomijasz sloty ETA → rozłączne.
- Sprint A rusza config solvera; Ty ruszasz asercje feasibility/geometrii. **Jeśli oba dotkną `global_allocate`: Ty DOKŁADASZ asercje (append), on zmienia stałe configu — inne regiony.** Koordynacja: osobne worktree, merge sekwencyjny, commit po jawnych ścieżkach, backup przed nadpisaniem ([[feedback-multisession-shared-deploy]]).

## 5. BEZPIECZEŃSTWO ZMIAN (krytyczne — asercja może dać fałszywkę)
- Każdy inwariant **najpierw SHADOW/log-loud**: obserwuj na żywych danych/replayu, jak często by odpalił. **Twarda blokada TYLKO gdy udowodnisz ZERO fałszywych odpaleń na kanonie** + realnym korpusie.
- Za flagą (default bezpieczny). Mutation-probe: udowodnij, że asercja łapie dokładnie ten bug, przed którym stoi (test, który BEZ fixu pada, Z fixem przechodzi).
- Nie zmieniamy happy-path decyzji — inwariant to strażnik, nie nowa reguła.

## 6. DEFINICJA UKOŃCZENIA (DoD — dowody, nie deklaracje)
1. **Regresja pełna** `pytest tests/` z worktree — ZIELONA (≥4448/0).
2. Per uzbrojony slot: (a) asercja + fail-loud u źródła, (b) test łapiący bug (mutation-probe zabity), (c) dowód log-loud/replay ZERO fałszywek przed twardą blokadą.
3. `ZIOMEK_INVARIANTS.md` zaktualizowany (który slot uzbrojony, który świadomie odłożony i dlaczego).
4. **Commit PRZED końcem.** Merge sekwencyjny po ACK.
5. Raport `eod_drafts/2026-07-08/S_INWARIANTY_raport.md`: sloty uzbrojone / odłożone (z powodem ETA) / dowody / co czeka na ACK.

## 7. GDY WĄTPLIWOŚĆ CO DO PRIORYTETÓW/INWERSJI → PYTAJ ADRIANA, NIE ZGADUJ.
