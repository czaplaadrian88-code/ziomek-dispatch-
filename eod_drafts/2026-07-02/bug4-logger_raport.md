# Pas RDZENIA `bug4-logger` — raport (2026-07-02/03, WZNOWIONY)

**Branch:** `fix/bug4-logger` (worktree `/root/.openclaw/workspace/wt-bug4-logger`)
**Commit:** `529757a` (na `f7875df`) — `feat(bug4-logger): oś OBJEKTYWU (schema 2) w loggerze reseq + oracle reader`
**Tryb:** BUILD-ONLY. Zero flip / restart / push. KANON dispatch_v2 nietknięty.

---

## 0. Kontekst wznowienia

Poprzedni agent padł na limicie sesji ~23:16 z NIEZACOMMITOWANĄ pracą (3 pliki dirty,
0 commitów). Przejąłem, **zweryfikowałem poprawność (nie zaufałem ślepo)**, domknąłem do
DoD, zacommitowałem. Praca poprzednika okazała się **kompletna i poprawna** — nie wymagała
korekty, tylko weryfikacji + dowodów + commita + raportu.

### Co przejąłem (praca poprzednika, zweryfikowana jako poprawna)
- `plan_recheck.py` `_bug4_reseq_shadow`: pola `schema:2`, `fresh_total_duration`, `fresh_sla`
  (z `plan_fresh` — atrybut read, ZERO extra solve/OSRM), `frozen_total_duration=null`,
  `frozen_sla=null`, `obj_axis_note`.
- `tools/bug4_reseq_oracle.py`: `read_obj`, `obj_tripwire`, statystyki osi objektywu w
  `reverdict_from_log`, branch `RESIDUAL` w `_run_reverdict`.
- `tests/test_bug4_logger_schema2_2026_07_02.py` (NOWY, 11 testów).

### Co dopisałem/wykonałem
- Weryfikacja fundamentów (sygnatura z param `R`, `_EPS=0.05`, klucze reverdict, że
  `plan_fresh` niesie `total_duration_min`/`sla_violations` — używane w `key` powyżej, więc
  gwarantowane non-None).
- Pełny łańcuch dowodowy (niżej §3).
- Commit + ten raport.

---

## 1. Zakres (zgodny ze spec `bug4-oracle_raport.md §5` pkt 1)

Spec §5 pkt 1: „serializować `frozen_total_duration/fresh_total_duration/frozen_sla/fresh_sla`
(nie tylko drive) + POPRAWNY tripwire `fresh_obj ≤ frozen_obj`".

**Napięcie spec vs constraint zadania** (poprawnie rozwiązane przez poprzednika):
- Spec chce OBU osi (frozen + fresh).
- Zadanie: „TYLKO z już-policzonych obiektów planu — **ZERO dodatkowych solve/OSRM**;
  brak w zasięgu → null + nota".
- `fresh_*` = DARMOWE: `plan_fresh` policzył je świeży solve wyżej (attr read).
- `frozen_*` = NIEDARMOWE: objektyw zamrożonej sekwencji wymaga `_simulate_sequence`, który
  pod `ENABLE_PICKED_UP_DROP_FLOOR` odpala `osrm_client.route(pickup,drop)` POZA legami trasy
  → **NIE gwarantuje zero-OSRM w żywym ticku**. → `frozen_*=null` + nota, oracle rekonstruuje
  frozen brute-force **offline** (`score_bag`, |Δ|=0 vs niezależny walk) i domyka tripwire.

To jest dokładnie „brak w zasięgu → null + nota" z zadania. Kanał żywy dostarcza fresh-objektyw
(łapie SUBOPTYMALNY OR-Tools = residual, którego drive nie wykrywał); frozen domyka offline.

---

## 2. Charakter zmiany + gdzie żyje

- **Log-only, parytet decyzji STRUKTURALNY.** Call-site `plan_recheck.py:2217` (gałąź RETIME,
  po sukcesie `_retime_one_bag_plan`) wywołuje `_bug4_reseq_shadow(...)` jako **statement —
  return odrzucany**. Funkcja zwraca `None`, jedyny efekt uboczny = dopis do
  `dispatch_state/bug4_reseq_shadow.jsonl`. Ścieżka decyzyjna nie konsumuje wyjścia.
- **⚠️ `dispatch-plan-recheck` = `Type=oneshot` + `OnUnitActiveSec=5min`** (świeży proces co
  tick). **Po ewentualnym merge do KANON kod jest ŻYWY od następnego ticku (~5 min), BEZ
  restartu.** To zmiana log-only (dodatkowe pola w shadow-jsonl), nie dotyka decyzji — ale
  ktokolwiek merge'uje musi być świadom, że efekt wchodzi automatycznie następnym tickiem.

---

## 3. Dowody (DoD)

| Dowód | Wynik |
|---|---|
| `py_compile` obu plików | OK |
| Nowy test `test_bug4_logger_schema2` | **11/11 PASS** |
| — schema-2 completeness (`set(keys)-OLD == NEW`) | PASS |
| — byte-parytet replay 120 worków (wejścia niezmutowane, return None, tylko `_NEW_KEYS`) | PASS |
| — read_obj (prefer schema-2 / fallback reconstruct) | PASS |
| — obj_tripwire True/False/None (lex sla>total) | PASS |
| — **mutation ×2** (podbij `_EPS`→∞ zabija człon total; mutant-bez-sla → tripwire gryzie) | PASS (tripwire NOŚNY) |
| — reverdict statystyki osi + wstecz-kompat | PASS |
| Oracle selfcheck (brute-force vs niezależny OSRM-walk) | `|Δ|=0.000`, inwarianty+determinizm OK |
| Reverdict na ŻYWYM logu (2534 rek. schema-1) | bez crashu; `schema2_n=0`, stare metryki nietknięte (deliv_seq_differs 22.0%, wrong-axis FP 277/277, suspect 0.0%) |
| **Pełna regresja worktree (z moimi zmianami)** | **4062 passed / 23 failed / 23 skip / 9 xfail / 2 xpass** |
| Pełna regresja na CZYSTEJ bazie (stash) | 4050 passed / 24 failed / 23 skip / 9 xfail / 2 xpass |
| KANON `git status --porcelain` (moje pliki) | CLEAN — zero wycieku do KANON |

### Interpretacja regresji (porównanie WEWNĄTRZ-worktree, per DoD)
- **23 stabilnych porażek** = znane artefakty path-layout: `test_a2_selection_shadow.py` (15) +
  `test_courier_reliability.py` (8). Przyczyna dowiedziona: hardcoded `MODULE_PATH =
  /root/.openclaw/workspace/dispatch_v2/tools/...` (layout BEZ `scripts/`) → `SkipTest: moduł
  nie istnieje` liczony jako fail. **Niezwiązane z plan_recheck/oracle.** Identyczne na bazie.
- **Delta baza(24) vs dirty(23) = flaky `test_v319c_sub_a.py::script_run`**, NIE regresja.
  Dowiedzione: na czystej bazie (moje zmiany zestashowane) 3 biegi izolowane =
  **fail, pass, pass** (zależny od stanu/kolejności script-runner). Mój diff go nie dotyka.
- **Bilans mojego diffu:** `dirty_passed(4062) = base_passed(4050) + 11 nowych + 1 flaky-flip`;
  `dirty_failed(23) = base_failed(24) − 1 flaky-flip`. **Netto: +11 zielonych, 0 nowych
  porażek.** Baseline DoD `4071/0/26/9xf/2xp` = liczby KANON; w worktree stała baza = 23 path-
  layout fail (+1 flaky), i to jest właściwy punkt odniesienia „wewnątrz-worktree".

---

## 4. Checklist re-collect λ=0 (PRZYGOTOWANY, **NIE wykonany** — per zadanie; źródło `o2-capz_raport.md §5`)

Nie należy do tego pasa; wpisany do raportu jako referencja deploy-za-ACK:
1. Drop-in `Environment=BUNDLE_CALIB_LAMBDA_CZAS=0` do `dispatch-bundle-calib-shadow.service`
   → `daemon-reload` (env-frozen per-proces kolektora — wzorzec #9, nie tylko modułu).
2. Świeży `OUT_JSONL` (np. `bundle_calib_shadow_l0.jsonl`) LUB czyść
   `bundle_calib_shadow_state.json` — inaczej λ=1.5 i λ=0 zmieszane (skażenie).
3. Zbieraj ≥2-3 dni, potem `tools/bundle_calib_review.py` na nowym korpusie → werdykt
   ground-truth (overage bez λ-zawyżenia).
4. Porównaj engine-improved% z 7.3% (obecny konserwatywny) — oczekiwane ≥.
5. Opcjonalne — NIE blokuje flipu Kroku 1 o2-capz.

---

## 5. Za ACK / poza tym pasem
- **Merge do KANON** (kod żywy następnym tickiem plan-recheck) — decyzja/ACK.
- **Domknięcie caveatu frozen-objektyw w żywym logu** (dziś null): wymagałoby policzenia
  objektywu frozen bez extra-OSRM (np. re-użycie legów planu) LUB świadomego dopuszczenia OSRM
  w loggerze — **P0, osobny pas + ACK** (constraint zero-OSRM to celowo respektowałem).
- **Merytoryczny następny krok reseq** (spec §5 pkt 3): zmierzyć benefit w minutach objektywu
  na collect-window, potem decyzja czy RETIME ma re-sekwencjonować — **decyzja silnika P0 → ACK.**

---

## Rollback
`git -C /root/.openclaw/workspace/wt-bug4-logger revert 529757a` (lub porzucenie brancha przed
merge). Zmiana log-only, brak stanu do cofania.
