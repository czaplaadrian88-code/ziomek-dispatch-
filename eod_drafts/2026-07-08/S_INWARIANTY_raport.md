# SPRINT B — Bezpieczniki/inwarianty alokacji (zakres NIE-ETA) — RAPORT

**Sesja-wykonawca:** tmux 37 · **Data:** 2026-07-08 · **Worktree:** `wt-invariants` (branch `quality/invariants-alloc`)
**Baseline:** master `6e1af23` · **Commit sprintu:** `fffdf69`
**Status:** ✅ ZAKOŃCZONY (kod + testy + dowody), **flip live + twarda blokada ODŁOŻONE ZA ACK.**

---

## 1. Co uzbrojono — SLOT `INV-FEAS-NO-DOUBLE-BOOK` 🔴→🟢 (Kontrakt ②, `ZIOMEK_INVARIANTS.md` l.36)

**Reguła (od Adriana, zakodowana):** kurier nie zaproponowany do **2 SPRZECZNYCH** zleceń w jednym
przebiegu de-konflikcji (greedy pile-on, K6). Mechanizm, który to gwarantuje, JUŻ istnieje —
`claim_ledger.tentative_assign` doklejający zwycięzcę do worka między eventami, by kolejna ocena
widziała obciążenie (korzeń pomiaru: **447 proponowany 127×/32 zlecenia, g_maxpile=7**). **Brakowało
STRAŻNIKA**, który łapie regres tego mechanizmu — to była pusta 🔴, bez xfail-ratchetu, bez guarda.

**Inwariant sprawdzalny (zero-FP z konstrukcji):** w jednym sweepie/ticku KOLEJNE claimy TEGO SAMEGO
kuriera muszą widzieć worek rosnący **dokładnie o +1** (poprzedni claim doklejony). Ślad
`[(cid, oid, bag_seen)]`, gdzie `bag_seen` = rozmiar worka, który zwycięska ocena widziała.
- Poprawne zachowanie (w tym **legalny bundling** 2-3 zlecenia jednemu kurierowi): `+1` per claim → **0 naruszeń**.
- Regres / pile-on (flota niemutowana): worek nierosnący → naruszenie `stale`.

To rozwiązuje pułapkę naiwnego cap-a maxpile (który fałszowałby na legalnym bundlingu): tripwier
patrzy na **wzrost worka między claimami**, nie na samą liczbę zleceń kuriera.

### Wpięcie (bliźniaki claim-ledger RAZEM — ETAP 3 mapa kompletności)
| Miejsce | Plik | Rola |
|---|---|---|
| Weryfikator (leaf, pure) | `claim_ledger.py` | `verify_no_stale_claim(trace)` + `check_sweep_trace(trace, log, ctx)` — log-loud |
| Bliźniak 1 (resweep de-pile) | `tools/pending_global_resweep.py` | ślad w `global_allocate`, verify po pętli; metryka `g_claim_ledger_breaches` → jsonl + summary (`run_once`) |
| Bliźniak 2 (shadow tick) | `shadow_dispatcher.py` | ślad w bloku `ENABLE_ENGINE_CLAIM_LEDGER`, verify po pętli eventów |
| Flagi | `common.py` (ETAP4) | `ENABLE_CLAIM_LEDGER_INVARIANT_CHECK` (obserwacja) / `_HARD` (blokada) — default OFF, **NIE w flags.json** |

`reassignment_global_select` (de-pile przerzutu) idzie PRZEZ `global_allocate` → pokryty pośrednio.
Twin-parity przypięty testem (oba używają JEDNEGO `claim_ledger.tentative_assign` + `check_sweep_trace`).

---

## 2. Dowody (nie deklaracje)

**Regresja pełna** (worktree via pkgroot `ZIOMEK_SCRIPTS_ROOT`): **4464 passed / 27 skipped / 10 xfailed**,
**0 realnych regresji**. Dwa „faily" to NIE-regresje:
1. `test_grafik_fetch_schedule::…[fetch]` — **pre-existing** (identyczny na kanonie `6e1af23`; deploy_staging
   mirror-drift `grafik_fetch.py`, poza zakresem — schedule, nie alokacja).
2. `test_flag_effect_coverage::test_no_new_untested_decision_flag` — **artefakt pkgroot** (checker hardkoduje
   kanoniczny `tests/` + `sys.path.insert(kanon)`, więc czyta kanoniczne testy BEZ mojego jeszcze-niescalonego
   pliku, a `common` bierze z worktree). **Dowiedziony PASS post-merge** przy spójnym źródle: oba flagi
   `in ETAP4=True`, `in tests txt=True`, `in base=False` → `new_gap=[]`. (Merge-sesja: re-run `pytest tests/`
   na kanonie = zielone — C12-e/g.)

**Zero-FP DOWÓD (offline, seed-fixed):** `scratchpad/fuzz_zero_fp.py` — 5000 losowych sweepów przez PRAWDZIWY
`global_allocate`, CHECK ON:
- INTACT (`tentative_assign` żywy): 22 702 claimów, **3092 sweepy z legalnym bundlingiem → 0 fałszywek**.
- MUTATED (`tentative_assign` = no-op): naruszenia w **3630 sweepach** (9654 breachy) → detekcja nie-vacuous.

**Testy** `tests/test_claim_ledger_no_double_book_inv.py` — **18/18** (via worktree):
- weryfikator: empty/singleton/correct-growth/stale/gap/interleaved + probe leksykalny + log-loud tylko na naruszeniu,
- wpięcie `global_allocate`: zero-FP na bundlingu (ślad `[(A,o1,0),(A,o2,1)]`), **mutation-probe** (neutralizacja
  `tentative_assign` → `[(A,o1,0),(A,o2,0)]` → breach `stale` wykryty),
- **flaga ON≡OFF co do allocation** (strażnik ≠ reguła — nie zmienia decyzji),
- HARD-block raise; CHECK-off ⇒ brak weryfikacji (bramkowanie),
- metryka `g_claim_ledger_breaches` w jsonl+summary (clean=0, pile-on>0),
- twin-parity (single-source `claim_ledger` w obu bliźniakach) + rejestracja flag w ETAP4.

**C14 mutation-probe (source-level, post-commit):** verifier oślepiony (`return []`) → **7 testów detekcji RED**
(stale/gap/probe/log-loud/wiring-pileon/HARD/metryka), 11 zero-FP zielonych (poprawnie — ślepy verifier trywialnie
nie fałszuje). `git checkout` → diff czysty → 18/18 zielone. Testy NIE-vacuous.

**Flaga ON≠OFF / measurability:** `g_claim_ledger_breaches` w jsonl; log-loud `CLAIM_LEDGER_INVARIANT breach [...]`
potwierdzony w fuzzie. Rejestr flag: obie w `ETAP4_DECISION_FLAGS` + module-const (fingerprint je widzi);
`flag_registry`/`flag_doc_coverage`/`conftest_strip` zielone (flags.json nietknięty).

---

## 3. Świadomie ODŁOŻONE — sloty ETA (powód: kalibracja ETA w cieniu)

Zgodnie z handoffem NIE tknięto slotów ETA/pickup/SLA — **kalibracja ETA dojrzewa w cieniu (`eta_calib_*`)
i ~10.07 może przedefiniować obietnicę → asercja czasu = strzał do ruchomego celu:**
`INV-FEAS-PICKUP-FLOOR`, `INV-TWIN-SLA-ANCHOR` (już armed), `INV-COH-CLAMP-CHOKEPOINT` (effective_pickup_at),
`INV-SEM-ETA-SPLIT`, `INV-COH-R-DECLARED` (ck≥odbiór — czas/obietnica). Czekają na osobny sprint po ustabilizowaniu ETA.

**Inne NIE-ETA sloty poza zakresem (nie „arm guard", to fala silnika):** `INV-SRC-EQUAL-TREATMENT`,
`INV-LIFE-LOADPLAN-PURE` — mają już xfail-ratchet w `test_invariant_slots_l04.py` (naprawa = zmiana silnika,
nie dołożenie strażnika). `INV-SRC/TWIN-ROUTE-ORDER` — w toku u tmux 15/27. `INV-FEAS-R6-ONE-SOURCE` —
dial-family już armed (B2); strukturalna unifikacja = L6.B2 (i R6 to termika/czas → poza zakresem).

**Nie tknięto:** `route_simulator_v2` (read-only), config solvera OR-Tools (Sprint A), `flags.json`, żaden restart/flip.

---

## 4. CO CZEKA NA ACK (ETAP 5/6 — do decyzji Adriana)

1. **Flip `ENABLE_CLAIM_LEDGER_INVARIANT_CHECK` ON** (obserwacja live) — pod `dispatch-shadow` (global_allocate/
   Faza C) + resweep. To flip flagi decyzyjnej = pełny deploy (C2). Da 2-dniowe okno pomiaru `g_claim_ledger_breaches`
   na żywym ruchu → potwierdzenie **ZERO fałszywek na żywo** (offline już = 0).
2. **Dopiero po (1) + zero-FP live: flip `ENABLE_CLAIM_LEDGER_INVARIANT_HARD`** (twarda blokada — raise przy naruszeniu).
   ⚠ `HARD` w `global_allocate` propaguje wyjątek; `run_once` woła bez try/except → HARD zatrzyma tick resweepu
   (celowo — naruszenie = realny bug de-konflikcji). Rozważyć czy blokada ma być raise vs. „drop feralnego claimu".
3. **Merge `quality/invariants-alloc` → master** (seryjnie, po ACK). Po merge: `pytest tests/` na kanonie
   (nie worktree) — potwierdzi zielony `flag_effect_coverage` (artefakt pkgroot znika).

**Rollback:** obie flagi OFF = no-op bajt-parytet (default). `git revert fffdf69`.

---

## 5. Pliki

`claim_ledger.py` (+69) · `common.py` (+18, 2 flagi ETAP4) · `tools/pending_global_resweep.py` (+48) ·
`shadow_dispatcher.py` (+26) · `tests/test_claim_ledger_no_double_book_inv.py` (+259, nowy) ·
`ZIOMEK_INVARIANTS.md` (slot + dashboard + nota 08.07). Dowód offline: `scratchpad/fuzz_zero_fp.py`.
