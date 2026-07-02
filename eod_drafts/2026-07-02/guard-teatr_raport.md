# Lane C — feasibility-guard-teatr — RAPORT (FALA-2 audytu 2.0, tmux 11)

**Data:** 2026-07-02 · **Branch:** `fix/guard-teatr` · **Tryb:** TYLKO testy + harness (feasibility_v2 i rdzeń = READ-ONLY; sam fix guardów = osobna fala SERIAL, nie ten pas)
**Fundament:** protokół C13 (zielony strażnik ≠ łapie regres; behawioralny vs tekstowy; mutation-test) + audyt 2.0 L09 („siła strażników").

---

## 1. Wynik jednozdaniowy

Zbudowany **mutation-probe** (`tools/guard_mutation_probe.py`) empirycznie potwierdza tezę L09: **na 6 zbadanych HARD-bramek feasibility 3 są strzeżone TEATRALNIE** (mutant przeżywa dotychczasowe testy) — bag-cap off-by-one, verdict-gate polaryzacja, oraz próg SLA (maskowany R6). Nowe testy behawioralne (`tests/test_feasibility_guards_behavioral.py`, 13 kills) + polaryzacyjny wariant verdict-gate zabijają 4/5 mutantów feasibility i mutanta polaryzacji; **2 luki niemożliwe do zamknięcia bez fixu U ŹRÓDŁA są zamarkowane `xfail` (L-TEATR-1/2)** — regresja pozostaje zielona.

---

## 2. Deliverables

| Plik | Rola |
|---|---|
| `tools/guard_mutation_probe.py` | NOWY harness: mutuje cel IN-MEMORY (feasibility_v2 przez sys.modules-injection w trybie plugin; verdict-gate przez transformację źródła-stringa), odpala właściwe testy, raportuje KILLED/SURVIVED per bramka + jsonl. Read-only wobec plików źródłowych. Self-locate (`Path(__file__).parents[1]`, scripts-root z conftest — zero hardcode worktree). |
| `tests/test_feasibility_guards_behavioral.py` | NOWY: 13 testów behawioralnych wołających `check_feasibility_v2` z realnym workiem/kandydatem → asercja WERDYKTU (nie tekstu). 5 bramek, ≥2 kills/bramka. + 2 `xfail` L-TEATR. |
| `tests/test_verdict_gate_guards.py` | ZAKTUALIZOWANY: dołożony wariant POLARYTETOWY (`gate_guard_polarity` + 2 testy) obok istniejących tekstowych (tekstowe zostają — pinują strukturę). |

---

## 3. Tabela KILLED / SURVIVED (z probe'a, `eod_drafts/2026-07-02/guard_mutation_probe.jsonl`)

### 3a. Mutanty feasibility_v2 (BEHAWIORALNE = nowy plik, LEGACY = scale01 + c3)

| Mutacja | Bramka (feasibility_v2) | BEHAWIOR. | LEGACY | Wniosek |
|---|---|---|---|---|
| `bagcap_ge_to_gt` (`>=`→`>`) | bag_full sanity cap ~460 | **KILLED** | SURVIVED | **teatr LEGACY**: string-match czyta DYSK → inwersja runtime niewidoczna; nowy behawioralny łapie |
| `pickup_far_flip` (`>`→`<`) | pickup_too_far ~656 | KILLED | KILLED | dobrze strzeżona (c3 + nowy) |
| `r6_per_order_disable` (`if X`→`if False and X`) | R6_per_order carried-age ~1234 | KILLED | KILLED | isolable ready-anchor case (nowy) zabija |
| `sla_threshold_999` (35→999) | sla_violation ~53 | **SURVIVED** | SURVIVED | **teatr**: brak testu SLA-only na progu (R6 dzieli 35, maskuje) → L-TEATR-2 |
| `hard_tier_cap_neuter` (`> cap`→`> cap+99`) | hard_tier_bag_cap ~468 | **KILLED** | SURVIVED | **teatr LEGACY**: brak testu behawioralnego (flaga OFF domyślnie); nowy z flagą ON łapie |

**Liczby:** przebadano **5 mutantów feasibility**; BEHAWIORALNE zabiły **4/5**, LEGACY przeżyło (teatr) **3/5** (bag-cap, sla, hard_tier).

### 3b. Mutant polaryzacji verdict-gate (dispatch_pipeline)

| Mutacja | Bramka | Stary strażnik (token-presence) | Nowy strażnik (polaryzacja) |
|---|---|---|---|
| `not _always_propose_on()` → `_always_propose_on()` | `all_candidates_low_score` (QUALITY) | **SURVIVED** (guarded=True — widzi token) | **KILLED** (polaryzacja='bare') |

Potwierdza L09 pkt 2: stary `test_verdict_gate_guards` sprawdza OBECNOŚĆ tokenu → inwersja `not` przechodzi zielona; nowy wariant `test_quality_gates_use_negated_guard` rozróżnia POLARYTET i zabija mutanta.

---

## 4. Luki `xfail` (L-TEATR-n) — do zdjęcia w fali SERIAL (fix U ŹRÓDŁA)

- **L-TEATR-1** (`test_r6_not_masked_by_sla_when_now_anchored`): R6 per-order HARD reject (`feasibility_v2` ~1234, `if r6_per_order_violations`) jest **maskowany przez bramkę SLA** (~1226, ten sam próg 35 min) dla klasy assigned-not-picked z kotwicą=teraz. Mutacja wyłączająca WYŁĄCZNIE R6 przeżywa, bo SLA łapie te same przypadki. **Fix U ŹRÓDŁA (SERIAL):** rozdzielić R6-ready-anchor od SLA-now-anchor albo świadomie zdeduplikować (jeden HARD z jawną kotwicą), z osobnym testem na R6-ready-anchor breach, którego SLA-now nie widzi.
- **L-TEATR-2** (`test_sla_threshold_is_35_exact_boundary`): próg SLA (`DEFAULT_SLA_MINUTES=35`, param `sla_minutes`) nie ma izolowanego strażnika — mutacja 35→999 przeżywa (potwierdzone probe: `sla_threshold_999` SURVIVED w OBU grupach), bo każdy carried-case łapiący SLA łapie też R6. **Fix U ŹRÓDŁA (SERIAL):** dedykowany SLA-only boundary case (picked_up-order z elapsed tuż-ponad 35, R6-ready-anchor nieaktywny) lub test parametryzowany progiem `sla_minutes`.

Obie luki = **redundancja/maskowanie 35-min między R6 i SLA** — jedna rekomendacja korzeniowa: **skonsolidować 35-min HARD w JEDNO źródło z jawną kotwicą** (spójne z 3-bliźniaki SLA-anchor z protokołu: `route_simulator._count_sla_violations` + `feasibility_v2` SLA-loop ~1156/1188 + `plan_recheck._o2_key`), po czym każda z bramek staje się niezależnie killable.

---

## 5. Regresja (vs baseline WORKTREE)

`fix/guard-teatr` HEAD `60084fa`. **Kluczowe:** baseline KANONU z handoffu (3907/0) NIE równa się baseline WORKTREE — worktree ma pre-existing delta.

| Stan | passed | failed | xfailed | skipped |
|---|---|---|---|---|
| WORKTREE baseline (moje zmiany usunięte, zmierzone) | 3884 | **23** | 11 | 23 |
| WORKTREE + moje zmiany | 3899 | **23** | 13 | 23 |
| **Delta moja** | **+15** | **0 nowych** | **+2 (xfail L-TEATR)** | 0 |

**23 pre-existing failów = NIE moje** i NIE dotyczą moich plików (padają w izolacji bez ich importu). Wszystkie w `tests/test_courier_reliability.py`.

⚠ **Root-cause 23 failów (do przekazania koordynatorowi — poza moim pasem):** `test_courier_reliability.py` hardcode'uje `MODULE_PATH = /root/.openclaw/workspace/dispatch_v2/tools/courier_reliability.py` (ścieżka absolutna), która NIE istnieje dla worktree (`wt-guard-teatr` ≠ `dispatch_v2`) → helper rzuca `SkipTest` raportowany jako FAILED. To **dokładnie klasa C12(e)** z protokołu (hardcode ścieżki worktree wywala kolekcję/bieg). Na kanonie plik przechodzi 8/8. **Nie tknąłem** (poza pasem — to nie strażnik feasibility). Rekomendacja: naprawić na self-lokalizację `Path(__file__).parents[1] / "tools" / "courier_reliability.py"`.

Moje pliki testowe używają self-lokalizacji/monkeypatchowanych flag i przechodzą identycznie na worktree i kanonie.

---

## 6. Rekomendacje per bramka (fix U ŹRÓDŁA — dla fali SERIAL)

| Bramka | Miejsce (symbol, linie dryfują) | Rekomendacja |
|---|---|---|
| bag_full sanity cap | `feasibility_v2.check_feasibility_v2`, `if len(bag) >= _bag_cap` | Fix = TESTOWY (już dostarczony behawioralny). Kod OK; usunąć poleganie na string-matchu `test_feasibility_bag_filter_honors_override` jako jedynym strażniku. |
| verdict-gate polaryzacja | `dispatch_pipeline` bramki QUALITY z `not _always_propose_on()` | Docelowo: test behawioralny na realnym `assess_order` pod ALWAYS-PROPOSE ON/OFF (ciężki, osobny). Dziś polaryzacyjny AST-guard zamyka mutanta `not`. |
| R6 per-order ↔ SLA (35min) | `feasibility_v2` ~1188 (SLA) + ~1234 (R6) + ~1156; bliźniaki `route_simulator._count_sla_violations`, `plan_recheck._o2_key` | **Konsolidacja 35-min HARD do jednego źródła z jawną kotwicą** (ready vs now). Odblokowuje niezależne killowanie obu (zdejmuje L-TEATR-1/2). Twin-parity per protokół „3 bliźniaki SLA-anchor RAZEM". |
| hard_tier_bag_cap | `feasibility_v2` ~468 `would_hard_cap = bag_after > _hard_cap` | Kod OK; behawioralny (flaga ON) dostarczony. Utrzymać przy flipie `ENABLE_HARD_TIER_BAG_CAP`. |
| MAX_PICKUP_REACH_KM martwa stała | `feasibility_v2:51` (realny czytelnik = `C.MAX_PICKUP_REACH_KM`, ~105) | (INFO z L09) usunąć martwą stałą — poza tym pasem. |

---

## 7. Ryzyka

- **Mutation-probe = przyrząd-werdykt (C9):** jego liczba (KILLED/SURVIVED) napędza decyzję „który guard naprawić". Skalibrowany: (a) baseline bez mutacji przechodzi; (b) mutacje mają unikalny fragment (grep -c==1, walidowane runtime w `_load_mutated_feasibility`); (c) sys.modules-injection PRZED kolekcją (test importuje mutanta). **Caveat:** SURVIVED string-match LEGACY dla bag-cap wynika z tego, że mutacja jest IN-MEMORY a string-match czyta DYSK — to POPRAWNE odsłonięcie teatru (test tekstowy nie widzi zachowania), NIE fałszywka.
- **xfail(strict=False):** L-TEATR-1/2 mogą przypadkiem XPASS przy przyszłej zmianie R6/SLA — wtedy (non-strict) NIE wywala regresji, ale sygnał „lukę zamknięto" trzeba odczytać ręcznie (zamienić na strict przy fixie SERIAL).
- **Flagi at-202/203 (flip 04.07):** testy pinują stan flag monkeypatchem (`load_flags` → dict, `ENABLE_HARD_TIER_BAG_CAP` per test), więc flip produkcyjny ich nie ruszy. Gdyby dołożono NOWĄ bramkę HARD za flagą — dopisać behawioralny ON/OFF (nie zakładać).
- **23 pre-existing faili worktree (C12(e)):** utrudniają czytanie „0 failed vs 3907" z handoffu — koordynator musi porównywać vs baseline WORKTREE (23), nie kanonu. Zgłoszone do naprawy poza pasem.
- **Zakres:** nie dotknąłem feasibility_v2/rdzenia/flags.json/ledgera (zgodnie z pasem). Fix samych guardów = fala SERIAL.
