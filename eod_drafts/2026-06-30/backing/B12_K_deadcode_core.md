# B12 — KLASA K (martwy/szczątkowy/wycofany-nieusunięty kod) w CORE

**Agent:** B12-K-deadcode-core · **Lane:** B · **Tryb:** READ-ONLY · **Data:** 2026-06-30 ~14:30 UTC · HEAD `8024705`
**Zakres:** 6 modułów rdzenia decyzji — `dispatch_pipeline.py` (7028L), `feasibility_v2.py` (1311L), `scoring.py` (288L), `route_simulator_v2.py` (1490L), `objm_lexr6.py` (88L), `plan_recheck.py` (2108L). Łącznie 12 313 LOC.
**Metoda:** świeży `grep -rn` (linie zweryfikowane dziś — DRYFUJĄ), reachability-sweep `def`→call-sites, weryfikacja stanu flag EFEKTYWNEGO (flags.json + `systemctl show -p Environment` drop-iny + env-default const). Martwy = zero importerów/osiągalności gałęzi, dowód grepem.
**Definicja K tu:** (a) gałąź NIEOSIĄGALNA na żywej konfiguracji, (b) symbol bez konsumenta, (c) flaga-na-zawsze-OFF z kodem (const-frozen / flags.json=false bez planu flipu), (d) reguła zneutralizowana stałą, (e) „legacy/retired" else-branch superseded. **Odróżniam K-RETIRED (wycofane/superseded, cleanup) od D-LATENT (shadow-pending z planem flipu) — oba raportuję, ale z etykietą.**

---

## STRESZCZENIE — 7 ROOTÓW K + 5 minorów + 3 korekty (non-findings)

| Root | Plik:linia (świeże) | Co martwe | Stan flagi | Klasa | Sev |
|---|---|---|---|---|---|
| **K1** r6_soft_penalty_c3_legacy „martwy+kłamie 0" | scoring.py:200/228-230 + feasibility_v2.py:1129 | kwarg + gałąź score + metryka zawsze 0 | `DEPRECATE_LEGACY_HARD_GATES=False` (const, NIE w flags.json) + ZERO live-callerów | K (+symptom F) | **P2** |
| **K2** R7 long-haul reject zneutralizowany | feasibility_v2.py:486-492 | HARD-reject NIGDY (potrzeba >99km) | `LONG_HAUL_DISTANCE_KM=99.0` (common.py:800) | K | P3 |
| **K3** B3 wait-gradient nigdy nie shipnięty | scoring.py:95-98 + common.py:2415-2419 | gałąź gradientu + 2 stałe | `ENABLE_B3_WAIT_GRADIENT` env-frozen OFF, NIE w flags.json, brak drop-inu | K (+D) | P3 |
| **K4** soon_free candidate-substitution efektywnie OFF | dispatch_pipeline.py:3622-3627/4074-4079/4181-4184 | substytucja pozycji/ETA (probe żyje jako telemetria) | `ENABLE_SOON_FREE_CANDIDATE` flags.json=**false** | K/D (latent) | P3 |
| **K5** carry_chain penalty+hard-reject latentny 34d | dispatch_pipeline.py:5070-5091 | cały blok + C.carry_chain_hard_reject | `ENABLE_CARRY_CHAIN_PENALTY` OFF (flags.json=false), „14d shadow" obiecane 27.05 | K/D (stalled) | P3 |
| **K6** legacy F1.8e pre_shift hard-exclude superseded | dispatch_pipeline.py:5890-5899 | else-branch | `ENABLE_V324A_SCHEDULE_INTEGRATION="1"` ON → else martwy | K | P3 |
| **K7** O2 ready-anchor sweep OFF, komentarz kłamie „ON" | route_simulator_v2.py:139 (komentarz) + gałąź sweep/select | sort O2 + select (liczone ZAWSZE dla shadow) | `ENABLE_O2_READY_ANCHOR_SWEEP=False` (const, NIE w flags.json) | K/D (+L kłamiący komentarz) | **P2** |
| minory | (niżej §MINOR) | BUG2_GAP_FROM_PLAN / V326_WAVE_VETO_NEW_DROP / post_shift 4-tuple / DROP_TIME OFF-path / LOADAWARE_SELECTION_SHADOW | różne OFF | K/D | P3 |
| **NON-1** R9 wait-courier HARD-reject tail = **ŻYWY** | scoring.py:150-151 → dispatch_pipeline.py:5637 | — (NIE martwy, koryguje A2 smell #11) | `ENABLE_V3273_WAIT_COURIER_PENALTY="1"` ON | korekta | — |
| **NON-2** `_v327_safe_fetch_czas_kuriera` = **ŻYWY** | dispatch_pipeline.py:260, ref :430 | — (false-positive sweepu: `executor.submit` bez `()`) | — | korekta | — |
| **NON-3** `_v326_*`/`_v325_*` funkcje = **ŻYWE** | dispatch_pipeline.py (15 defów) | — (wersjonowane nazwy, aktywne impl.) | mix ON | korekta | — |

**Najgroźniejsze (P2): K1** (kłamie zerem — dokładnie „r6_soft_penalty_c3_legacy 'martwy+klamie 0'" z briefu) i **K7** (komentarz w silniku mówi „ON" gdy efektywnie OFF — klasa, która realnie myli sesje, wzorzec #1 protokołu „16,2% fałszywie czystych"). Reszta = inertne wycofane (P3), dobrze udokumentowane jako martwe — ale wciąż NIEUSUNIĘTE (cleanup-dług).

---

## K1 — `r6_soft_penalty_c3_legacy` MARTWY + KŁAMIE 0 (root z briefu)  ★ P2

**Co:** Reguła C3 „deprecate legacy hard gates → soft" (R1/R5/R6/R7/R8 zsoftować przez kwarg `score_candidate`) ZAPROJEKTOWANA, NIGDY NIEAKTYWOWANA, superseded przez per-regułowe softowanie (R1/R3/R5/R8 już są metric-only osobno). Zostały 2 martwe site'y + 1 martwa stała + 1 kłamiąca-0 metryka.

**Dowód (świeży grep):**
- `scoring.py:200` `r6_soft_penalty_c3_legacy: float = 0.0,` (kwarg) — self-doc `:196-199`: „MARTWY kwarg: dodawany do score tylko gdy `DEPRECATE_LEGACY_HARD_GATES=True` (stała=False) i NIGDY nie przekazywany przez live caller".
- `scoring.py:228-230` `if DEPRECATE_LEGACY_HARD_GATES and r6_soft_penalty_c3_legacy != 0.0: total += ...` — gałąź dodająca do score.
- `common.py:912` `DEPRECATE_LEGACY_HARD_GATES = False` — **hardcoded `= False`, NIE w flags.json** (nie da się hot-flipnąć).
- `grep -rn r6_soft_penalty_c3_legacy --include=*.py | grep -v test | grep -vE 'scoring|feasibility'` = **PUSTE** → ZERO live-callerów przekazuje kwarg. **Podwójnie martwa**: (a) flaga const False na zawsze, (b) brak callera.
- `grep -rn DEPRECATE_LEGACY_HARD_GATES --include=*.py | grep -v test` → jedyni konsumenci: `scoring.py:14/197/228` (martwa gałąź) + `feasibility_v2.py:1123` (komentarz). Stała egzystuje WYŁĄCZNIE by trzymać martwą gałąź martwą.
- **„kłamie 0":** `scoring.py:283` serializuje `"r6_soft_penalty_applied": round(r6_penalty_applied, 2)` — `r6_penalty_applied` ZAWSZE `0.0` (gałąź :228 nigdy nie odpala) → metryka raportuje stałe zero „brak kary R6" (myli że R6-soft policzone tu).
- **Producent martwej wartości:** `feasibility_v2.py:1129` `metrics["r6_soft_penalty_c3_legacy"] = round(-3.0*(r6_max_bag_time-30.0),2)` — liczy `-3/min` w strefie (30,35], self-doc `:1127`: „Zero zmiany zachowania: martwa ścieżka pozostaje martwa". Wartość trafia do `metrics` ale ŻADEN konsument jej nie czyta (ŻYWA kara R6-soft = `dispatch_pipeline._r6_soft_penalty` -8/min → `bonus_r6_soft_pen`).
- Ekspozycja w logu: `grep -c '"r6_soft_penalty_c3_legacy"' shadow_decisions.jsonl` (próbka) = 0 → nieserializowane do ledgera (niska ekspozycja, ale wciąż w `metrics` dict).

**Dedup:** root = „C3-deprecate-legacy-hard-gates niezaktywowana migracja". 3 site'y (scoring kwarg+gałąź, feasibility producent, common stała) = JEDEN root. NIE liczyć jako 3 chaosy. Metryka `r6_soft_penalty_applied=0` = SYMPTOM (klasa F dryf-semantyki: nazwa sugeruje „applied" a zawsze 0).
**still_open:** TAK (nieusunięte). **Fix-kierunek (DRAFT, nie wykonywać):** usunąć kwarg+gałąź scoring + producent feasibility + stałą common (refaktor bez-zmiany-zachowania, dowód bajt-identyczności — `DEPRECATE_LEGACY_HARD_GATES` już nigdzie nie steruje żywą decyzją).

---

## K2 — R7 long-haul reject ZNEUTRALIZOWANY STAŁĄ  ★ P3

**Co:** HARD-bramka R7 (długa trasa w peaku → reject bundla) istnieje, ale stała `LONG_HAUL_DISTANCE_KM=99.0` czyni ją fizycznie nieosiągalną (Białystok ~15km średnica). Kod-zombie myli czytającego że R7 żyje.

**Dowód:**
- `feasibility_v2.py:486` `if bag and r7_ride_km > C.LONG_HAUL_DISTANCE_KM and r7_in_peak:` → `return ("NO", "R7_longhaul_peak ...")` (:487-492).
- `common.py:800` `LONG_HAUL_DISTANCE_KM = 99.0  # F2.1c: R7 wyłączone — 4.5km było za agresywne dla Białegostoku`.
- Self-doc `feasibility_v2.py:471-472`: „TODO C3 deferred (2026-04-18): refactor to soft penalty if LONG_HAUL_DISTANCE_KM threshold lowered from 99km. Currently dormant rule, no production impact." → reject 99km = martwy, TODO C3 nigdy nie zrobione.
- Telemetria `r7_ride_km`/`r7_is_longhaul`/`r7_in_peak` (`:481-485`) liczona ZAWSZE (nie martwa) — martwy jest TYLKO reject `:486-492`.

**Dedup:** root = „reguła zneutralizowana stałą (TODO-soft never done)". Pokrewne z K1 (oba = C3-deferred). **still_open:** TAK.

---

## K3 — B3 wait-gradient NIGDY NIE SHIPNIĘTY (flaga env-frozen OFF)  ★ P3

**Co:** B3 = gradient kary za `wait_min>60` (mający zastąpić sentinel `-1000`). Env-frozen OFF, NIE w flags.json, żaden drop-in go nie ustawia → gałąź gradientu + 2 stałe MARTWE. Sentinel `-1000` to żywa ścieżka.

**Dowód:**
- `scoring.py:94-99`: `if wait_min > table[-1][0]:` → `if _common.ENABLE_B3_WAIT_GRADIENT: ... return max(val, FLOOR)` (gradient, :95-98) `else: return V327_WAIT_PENALTY_HARD_FALLBACK` (-1000, :99 ŻYWY).
- `common.py:2415` `ENABLE_B3_WAIT_GRADIENT = _os.environ.get("ENABLE_B3_WAIT_GRADIENT","0") == "1"` — env-default OFF.
- `grep ENABLE_B3_WAIT_GRADIENT flags.json` = PUSTE; `grep -rn B3_WAIT_GRADIENT /etc/systemd/system/` = PUSTE → OFF wszędzie (nie da się nawet hot-flipnąć, tylko env).
- Martwe stałe: `common.py:2416-2419` `B3_WAIT_GRADIENT_SLOPE_PER_MIN=-40.0`, `B3_WAIT_GRADIENT_FLOOR=-2000.0` (czytane tylko w :97-98).
- Caller ŻYWY: `compute_wait_penalty` wołany `dispatch_pipeline.py:4374` pod `ENABLE_V327_WAIT_PENALTY="1"` ON (common.py:2389) → funkcja żyje; martwy jest sam gradient B3.

**Dedup:** root = „B3 retired/never-shipped (flaga env-frozen OFF z kodem)". **still_open:** TAK. Caveat: nie zmierzyłem runtime-częstości `wait>60` (gałąź `>60` osiągalna w zasadzie, ale B3 i tak OFF — code-level dead niezależnie od częstości).

---

## K4 — `soon_free` candidate-substitution EFEKTYWNIE OFF (probe żyje jako telemetria)  ★ P3

**Co:** Probe „kurier zaraz wolny" liczy się ZAWSZE (shadow), ale SUBSTYTUCJA wejść (pozycja=last_drop, ETA=soon_free) gated `ENABLE_SOON_FREE_CANDIDATE` (flags.json=false) → `soon_free_applied` ZAWSZE False na żywo. Gałęzie aplikacji martwe-live.

**Dowód:**
- `dispatch_pipeline.py:3620` `soon_free_probe = _soon_free_probe(...)` (ZAWSZE, telemetria — komentarz :3613 „Probe ZAWSZE").
- `:3622-3627` `if soon_free_probe ... and C.decision_flag("ENABLE_SOON_FREE_CANDIDATE"): courier_pos = ...; soon_free_applied = True` — substytucja.
- Martwe-live konsekwencje `soon_free_applied`: `:4074-4079` (`eta_source="soon_free"`), `:4181-4184` (free_at override).
- `flags.json:164` `"ENABLE_SOON_FREE_CANDIDATE": false`; `common.py:2084` env-default „0"; w ETAP4 (common.py:89). → effective OFF (flippable, NIE const-frozen).
- `_soon_free_probe` (def `:2342`) NIE martwy (probe+serializacja `soon_free_*` :5449-5455). Martwa jest TYLKO aplikacja.

**Dedup:** root = „soon_free substitution flaga-OFF (shadow-pending)". To D-LATENT (w flags.json, flippable) z martwą gałęzią aplikacji — NIE retired. Brief nazwał wprost „soon_free_probe efektywnie OFF". **still_open:** TAK. **C2-mina:** flip uzbroi substytucję pozycji (zmienia ranking) — pełny deploy, nie sama flaga.

---

## K5 — carry_chain penalty + HARD-REJECT latentny, 34 dni stalled  ★ P3

**Co:** Cały blok carry-chain (kara SOFT + HARD-reject KK-dinner) gated `if C.ENABLE_CARRY_CHAIN_PENALTY:` (OFF). Gdy OFF — NIE liczy się nawet dla shadow (inaczej niż soon_free). „14d shadow" obiecane 2026-05-27, dziś 30.06 (34 dni) wciąż OFF.

**Dowód:**
- `dispatch_pipeline.py:5070` `if C.ENABLE_CARRY_CHAIN_PENALTY:` opasuje :5071-5091 (cała kalkulacja + `:5081` `carry_chain_hard_rejected = C.carry_chain_hard_reject(...)`).
- Komentarz `:5618` (potwierdza martwość): „carry_chain_hard_reject ... zawsze False bo branch flagowy nie odpala".
- `common.py:3555` `ENABLE_CARRY_CHAIN_PENALTY = _os.environ.get("ENABLE_CARRY_CHAIN_PENALTY","0")=="1"`; `flags.json:92` `false`; `flags.json:90` komentarz „default FALSE (wymaga 14d shadow przed flip)".
- Helpery `C.carry_chain_penalty` / `C.carry_chain_hard_reject` (common.py) — martwe-live (jedyny caller za flagą OFF).
- Serializacja: `carry_chain_penalty` w `bonus_penalty_terms` (:5135) zawsze 0 (gdy OFF); `carry_chain_hard_reject` :5467 zawsze False.

**Dedup:** root = „carry_chain latentna (stalled-pending, nie shadow-liczona przy OFF)". D-LATENT, ale stalled 34d → K-adjacent (martwy-de-facto). **still_open:** TAK. **C2-mina:** flip uzbroi HARD-reject (verdict NO) — pełny deploy + shadow first.

---

## K6 — legacy F1.8e pre_shift hard-exclude SUPERSEDED (martwy else)  ★ P3

**Co:** Stary mechanizm F1.8e (hard-exclude pre_shift gdy nie zdąży na pickup) = `else`-gałąź `ENABLE_V324A_SCHEDULE_INTEGRATION`. Flaga ON → `else` (:5890-5899) nigdy nie wykona; `if` to tylko `pass`.

**Dowód:**
- `dispatch_pipeline.py:5885-5889` `if C.ENABLE_V324A_SCHEDULE_INTEGRATION:` → komentarz „V3.24-A zastępuje legacy F1.8e hard reject gradient" → `pass`.
- `:5890-5899` `else:` „Legacy F1.8e: hard exclude..." → `c.feasibility_verdict = "NO"`.
- `common.py:1914` `ENABLE_V324A_SCHEDULE_INTEGRATION = _os.environ.get(...,"1")=="1"` (env-default ON); NIE w flags.json; brak drop-inu → **effective ON** → `else` martwy.

**Dedup:** root = „legacy-else superseded przez ON-flagę (V3.24-A)". **still_open:** TAK. Inertne (else nieosiągalny dopóki V324A ON), ale nieusunięte → myli (czytelnik widzi 2 ścieżki pre_shift).

---

## K7 — O2 ready-anchor sweep OFF, KOMENTARZ KŁAMIE „ON"  ★ P2

**Co:** Sort/select O2 re-seq (ready-anchor) gated `ENABLE_O2_READY_ANCHOR_SWEEP` (const False, NIE w flags.json) = effective OFF, pending review 02.07. ALE komentarz `route_simulator_v2.py:139` mówi „ENABLE_O2_READY_ANCHOR_SWEEP **ON**" — kłamie. Sesja czytająca uwierzy że O2 żyje.

**Dowód:**
- `route_simulator_v2.py:139` komentarz: „Primary sort — O2 RE-SEQ (2026-06-27, ENABLE_O2_READY_ANCHOR_SWEEP **ON**) lub legacy".
- `:143-144` `_o2_on = _C_o2sel.flag("ENABLE_O2_READY_ANCHOR_SWEEP", getattr(_C_o2sel,"...",False))` — czyta flags.json→const.
- `common.py:249` `ENABLE_O2_READY_ANCHOR_SWEEP = False  # ... review 02.07`; `grep O2_READY_ANCHOR_SWEEP flags.json` = PUSTE; brak drop-inu → **effective OFF** (potwierdzone A3 §2a).
- Gałąź sort O2 (primary `:139+`) i select (`:772` „UŻYWANE tylko gdy ENABLE_O2_READY_ANCHOR_SWEEP ON") = martwe-live. Score liczony ZAWSZE (`:234` „liczone ZAWSZE") dla shadow — martwa jest TYLKO aplikacja sortu/selectu.

**Dedup:** root = „O2 sweep latentny + kłamiący komentarz ON↔OFF". D-LATENT (pending 02.07, at-job #168/#200) + **L (kłamiący komentarz)** = realny near-miss-generator (wzorzec #1 protokołu, C4/C9 — komentarz ≠ efektywny stan). **still_open:** TAK. Materialne: ktoś planujący flip 02.07 może pominąć go wierząc komentarzowi.

---

## MINOR — flag-OFF-z-kodem (P3, w większości D-adjacent, niska materialność)

| # | Plik:linia | Gałąź | Flaga (stan) | Nota |
|---|---|---|---|---|
| M1 | dispatch_pipeline.py:4754 | `if C.ENABLE_BUG2_GAP_FROM_PLAN ...` | `ENABLE_BUG2_GAP_FROM_PLAN` env-default „0" (common.py:1709), NIE w flags.json, brak drop-inu → OFF | wariant V326 BUG-2 gap, martwa-live; brak planu flipu = K-adjacent |
| M2 | dispatch_pipeline.py:4844 | `if C.ENABLE_V326_WAVE_VETO_NEW_DROP ...` | env-default „0" (common.py:2228), OFF | wave-veto na NOWEJ dostawie (os. od żywego v326_wave); martwa-live |
| M3 | objm_lexr6.py:44-46 | `if C.decision_flag("ENABLE_POST_SHIFT_OVERRUN_PENALTY"): return 4-tuple` | const-frozen OFF (common.py:1891, w ETAP4 ale NIE w flags.json) | by-design „robimy 3" (Adrian 24.06), bajt-identyczne przy OFF; martwa-live 4-krotka prepend |
| M4 | route_simulator_v2.py:586-589 | `if ENABLE_DROP_TIME_CONSTRAINT and ref ...` (OFF-path = no-op) | env-default „1" ON (common.py:1125) | kill-switch: OFF-PATH (legacy no-op) martwy; ON żyje. Nie retired — rollback capability |
| M5 | (gałęzie pod `ENABLE_LOADAWARE_SELECTION_SHADOW`) | shadow log-only | env-default „0" OFF (common.py:2912) | shadow-only, log-only; latentne |

Wszystkie M1-M5 = flaga-OFF-z-kodem; M1/M2 najbliżej „K" (superseded V326-warianty bez planu flipu), M3/M4/M5 = intencjonalnie-OFF (by-design/kill-switch/shadow). Niska materialność, ale nieusunięte.

---

## NON-FINDINGS (korekty — NIE raportować jako martwe; zapobiega double-count)

**NON-1 — R9 wait-courier HARD-reject tail = ŻYWY (koryguje A2-smell #11).**
A2 (`A2_rule_registry.md:224`) zgłosił „scoring.py:153-164 zwraca (penalty, False) → HARD-reject tail w SOFT, do potwierdzenia czy żyje". **Weryfikacja: ŻYWY.** `scoring.py:150-151` `if wait_min > V3273_WAIT_COURIER_HARD_REJECT_MIN: return (0.0, True)` (osobno od gałęzi gradientu :153-164). Konsumpcja: `dispatch_pipeline.py:4468` `_pen_273, _reject_273 = _v3273_wcp(...)` → `:4480` `v3273_wait_courier_hard_reject = True` → `:5637` `if v3273_wait_courier_hard_reject and verdict == "MAYBE":` **flipuje verdict→NO** (:5646 reason). Flaga `ENABLE_V3273_WAIT_COURIER_PENALTY="1"` ON. A2 patrzył tylko na gałąź gradientu (:153-164), przegapił `:150-151`. → NIE martwy.

**NON-2 — `_v327_safe_fetch_czas_kuriera` = ŻYWY.** Reachability-sweep `\bfn\s*\(` dał 0 call-sites (false-positive). Realny ref `dispatch_pipeline.py:430` `executor.submit(_v327_safe_fetch_czas_kuriera, oid)` — przekazany jako callable (bez `()`). LEKCJA: sweep `name(` gubi callable-refy (executor.submit/map/key=). → NIE martwy.

**NON-3 — funkcje `_v326_*`/`_v325_*`/`_v327_*`/`_v328_*` = ŻYWE.** 15 defów wersjonowanych nazwą (np. `_v326_fleet_load_balance:1447` = żywy R-10, `_v326_multistop_trajectory:876`, `_v325_new_courier_penalty:1808`). Nazwa wersji ≠ retired. Cross-check stanu flag: większość env-default „1" ON. → NIE martwe.

---

## TABELA POKRYCIA (jawne, nie cisza)

| Moduł | LOC | Reachability-sweep module-defs | Legacy/retired name-scan | Flag-OFF branch sweep | Status |
|---|---|---|---|---|---|
| `scoring.py` | 288 | ✅ pełny (0 dead top-level) | ✅ | ✅ (DEPRECATE_LEGACY, B3, R9-tail) | **ZBADANE** — K1,K3,NON-1 |
| `feasibility_v2.py` | 1311 | ✅ pełny (0 dead top-level) | ✅ | ✅ (R7=99, c3_legacy, DROP_TIME) | **ZBADANE** — K1,K2 |
| `dispatch_pipeline.py` | 7028 | ✅ module-level (1 FP skorygowany) | ✅ | ✅ (soon_free, carry_chain, F1.8e, BUG2, wave_veto) | **ZBADANE** — K1,K4,K5,K6,M1,M2 |
| `route_simulator_v2.py` | 1490 | ✅ module-level (0 dead) | ✅ | ✅ (O2 sweep, DROP_TIME) | **ZBADANE** — K7,M4 |
| `objm_lexr6.py` | 88 | ✅ pełny (lex_qual/bucket/pick/group_of żywe) | ✅ | ✅ (post_shift 4-tuple) | **ZBADANE** — M3 |
| `plan_recheck.py` | 2108 | ✅ module-level (0 dead) | ✅ | ✅ (route/canon flagi env-frozen ON via drop-in; brak OFF-dead) | **ZBADANE** — 0 K (no-opy to idempotencja, nie martwy) |

**LUKI POKRYCIA (jawne):**
1. **`drive_min_calibration.py` OFFSET „uśpiony"** (no_gps+6,5/pre_shift+15,3, MAIN OFF) — brief nazwał jako K-przykład, ALE `grep -nE 'drive_min_calibration|import.*drive_min' <6 core>` = PUSTE → moduł NIE importowany do rdzenia. **Poza moim zakresem 6-core** — cross-ref do agenta pokrywającego `drive_min_calibration.py`/scoring-peryferia (L6). Nie zbadałem wnętrza.
2. **Nested defy (def wewnątrz funkcji)** — sweep reachability tylko `^def` (module-level). Closures `_v327_eval_courier:3590`, `_legacy_r6_score:6104` itd. NIE swept exhaustively — spot-checked (żywe). Pełen nested-sweep = poza budżetem; ryzyko niskie (closures wołane lokalnie).
3. **Runtime-częstość martwych gałęzi** (np. ile razy `wait>60` / R7>99 by trafiło GDYBY flaga ON) — NIE zmierzona; deadness udowodniona CODE-LEVEL (flaga/stała), nie replay. Brief = read-only, deadness grepem wystarcza dla K.
4. **B3/soon_free/carry_chain — czy „pending" ma żywy plan flipu** — sprawdziłem komentarze/daty (carry_chain 34d stalled, O2 02.07), ale NIE potwierdziłem z Adrianem czy któreś są świadomie-porzucone (→ czysty K) vs realnie-zaplanowane (D). Etykietuję K/D-latent ostrożnie.
5. **common.py** poza zakresem 6-core — stałe/flagi cytowane jako kotwica stanu (DEPRECATE_LEGACY/LONG_HAUL/B3), ale pełen sweep martwych stałych common.py = inny agent.

**NIE-luki (świadomie):** Mailek/Papu (granica). Klasa D dryf-flag (A3 — tylko cytuję efektywny stan). Cross-repo render (konsola/apka — lane J). Instrumenty/shadow oracle (lane C).

---

## DEDUP ROLLUP (dla Fazy E — anty-double-count)

| Root K | Zwija się do | Site'y | Wspólny rdzeń |
|---|---|---|---|
| **K1** r6_soft_penalty_c3_legacy | „C3-deprecate-legacy-hard-gates niezaktywowana" | scoring×2 + feasibility×1 + common×1 + metryka-symptom | = root z **K2** (oba C3-deferred) |
| **K2** R7=99 | „C3 TODO-soft never done" | feasibility×1 | ↑ |
| **K3** B3 gradient | „flaga env-frozen OFF z kodem (never-shipped)" | scoring×1 + 2 stałe | rodzina z M1/M2 (OFF-warianty) |
| **K4** soon_free / **K5** carry_chain / **M5** loadaware | „shadow/latent flaga-OFF, gałąź aplikacji martwa-live" | dispatch_pipeline | D-LATENT (plan flipu) — NIE czysty retired |
| **K6** F1.8e else / **M4** DROP_TIME OFF-path | „legacy-else/kill-switch OFF-path superseded przez ON-flagę" | dispatch_pipeline / route_simulator | inertne (ON-flaga maskuje) |
| **K7** O2 sweep + komentarz | „latent + kłamiący komentarz ON↔OFF" | route_simulator | most do L (słownictwo) i C9 (przyrząd-werdykt 02.07) |

**Kluczowy wniosek:** K1+K2 = JEDEN root (C3-deferred). K3+M1+M2 = rodzina „OFF-warianty-z-kodem". K4+K5 = D-LATENT-z-martwą-aplikacją (nie czysty K — mają plan/shadow). K6+M4 = „ON-flaga maskuje legacy-OFF-path". K7 = jedyny z aktywnym ryzykiem mylenia (komentarz kłamie). Czyste-retired-do-usunięcia: **K1, K2, K3, K6** (+ M1/M2 warunkowo). Reszta = latent-z-kodem (cleanup po flipie/rezygnacji).
