# REG_new_audits — rejestr znormalizowanych findingów z korpusu 30.06 (AUDYT 2.0, lane REJESTR)

**Data:** 2026-07-02 · **Tryb:** READ-ONLY wobec produkcji (zero kodu/flag/systemctl/git). **Wejście:** 5 dokumentów audytowych 30.06 (+ 1 z 29.06). **Wyjście:** `findings_new.jsonl` (94 wiersze) + ten rejestr.

Wszystkie findingi znormalizowane do schematu `{src, id, sev, verdict, title, mech, files, owner_claim}`. Walidacja: **94/94 poprawny JSON, 0 błędnych, 0 duplikatów id, pełny schemat wszędzie.**

---

## 1. LICZBY PER ŹRÓDŁO

| src | wierszy | CONFIRMED | PLAUSIBLE | REFUTED | CAPPED | n.d. |
|---|---|---|---|---|---|---|
| `faza1-roots` | **52** | 20 | 7 | 11 | 14 | 0 |
| `faza1-konflikty` | **13** | 7 | 4 | 0 | 0 | 2 |
| `alloc-family` | **9** | 3 | 2 | 3 | 0 | 1 |
| `preshift` | **13** | 9 | 3 | 0 | 0 | 1 |
| `no-gps` | **7** | 3 | 4 | 0 | 0 | 0 |
| **RAZEM** | **94** | **42** | **20** | **14** | **14** | **4** |

**Rozkład severity:** P0 = 4 · P1 = 34 · P2 = 52 · P3 = 4.
**Rozkład owner_claim (top-warstwa):** L0=5 · L1=4 · L2=4 · L3=4 · L4=6 · L5=6 · L6=21 · L7=16 · L8=10 · **brak=18**. **Owned (L0-L8): 76/94 = 80%.**

---

## 2. TRZY ROZSTRZYGNIĘCIA NORMALIZACYJNE (jawnie, nie po cichu)

1. **`faza1-roots` REFUTED: nagłówek mówi „13", tabela ma 12 wierszy** — rozbieżność wewnątrz źródła FAZA1_01 (§3 header vs tabela). Znormalizowałem **12 faktycznie wypisanych** wierszy REFUTED-tabeli (13. nie istnieje w tekście). Odnotowane w JAWNE LUKI.
2. **`coord-sentinel-no-ingest-chokepoint` przeklasyfikowany REFUTED→CONFIRMED.** Leży w tabeli REFUTED FAZA1_01 §3, ale §4 dokonuje rekoncyliacji: **framing obalony** (walidator `coords_in_bialystok_bbox` ISTNIEJE `common.py:513`), **ale HARM = CONFIRMED-LIVE** (2046× V328 + 14456× COORD_GUARD + 8 ofiar 30.06, brak alertu), i roadmapa §5 daje mu L2.1/CONFIRMED. Znormalizowałem verdict=**CONFIRMED**, mech niesie notę o obalonym framingu. **Skutek na liczby:** faza1-roots CONFIRMED = 20 (19 z §1 + sentinel), pure-REFUTED-tabela = 11 (12−1).
3. **owner_claim — metoda dwuwarstwowa.** Primary = **§5 tabela 26 rootów→warstwa** (`backing/F_roadmap.md`, exact-slug). Secondary = **szczegółowe kroki L0-L8** (`F_roadmap.md §2`) dla nazwanych mechanizmów — dotyczy 14 odłożonych FAZA1 (§5 jawnie: „Domykane w L6.E/L7/L8") i CAPPED/REFUTED zwijających się w rodzica. Gdy slug nieobecny w §5 **i** żaden krok L-step go nie nazywa → **`brak`** (kandydat na sierotę). Verdict findingów `faza1-konflikty` przypięty do werdyktu zmapowanego rootu §5 (1:1) lub `n.d.` gdy sporny/niezmapowany.

---

## 3. FINDINGI z owner_claim = `brak` (18 kandydatów na sieroty)

### 3a. REALNE LUKI POKRYCIA — 12 (verdict ≠ REFUTED: CONFIRMED / PLAUSIBLE / n.d.)

| src | id | sev | verdict | dlaczego sierota |
|---|---|---|---|---|
| `no-gps` | **TOR1-duch-reassignment-forward-59pct-falszywych** | **P0** | CONFIRMED | ⭐ **DOMINUJĄCY ŻYWY problem** (59% fałszywych ratunków `a_late`), ale roadmapa §5 NIE ma nazwanego rootu — faza1-roots REFUTED „out-of-engine gates" jako źródło. **Konflikt werdyktów cross-audit.** |
| `no-gps` | **incomplete-twin-map-bucket-duplication** | P1 | CONFIRMED | 8 bliźniaków pozycji zduplikowane w kluczach/narzędziach; unifikacja-twins nie jest osobnym rootem §5 (rozproszona po L6.C1/twins). |
| `preshift` | TROP1-pozycja-sprzed-25min-jak-aktualna | P2 | CONFIRMED | `LAST_KNOWN_POS_TTL=25` + rescue 5639×/dzień; brak nazwanego rootu (klasa świeżości-danych). |
| `preshift` | TROP2-119-except-exception-silent | P2 | CONFIRMED | 119 gołych `except` łyka V328 100s/dzień; dotykane częściowo L2.2/L2.3 fail-loud ale bez jednego ownera. |
| `no-gps` | v325-pre-shift-soft-penalty-osobna-polityka | P2 | CONFIRMED | Kara −20 poza flagami równości; **polityka rozstrzygnięta Adrianem Q1b** (równość ZOSTAJE) — sierota „z decyzji", nie luka do fixu. |
| `faza1-konflikty` | K-E-equal-treatment-vs-out-of-engine-gates | P1 | n.d. | Ten sam temat co no-gps ghost; sporny (silnik unified vs gates diverged). |
| `faza1-konflikty` | K-M-kanon-regul-sam-ze-soba-sprzeczny | P1 | n.d. | Sprzeczność wewnątrz `ZIOMEK_REGULY_KANON` (§4:86 vs §7:151); doc-coherence, brak rootu §5. |
| `no-gps` | best-effort-fastest-pickup-shadow-hardcoded-bucket | P2 | PLAUSIBLE | Bliźniak-mina (shadow/log-only dziś); latentne po awansie. |
| `no-gps` | auto-assign-gate-g7-blokuje-pozycje | P2 | PLAUSIBLE | Latentne (ENABLE_AUTO_ASSIGN=False); temat autonomii, nie rodzina §5. |
| `no-gps` | drive-min-calibration-main-off-discrimination | P2 | PLAUSIBLE | Latentna mina re-flipu MAIN; jawnie „NIE ruszać" (artefakt 05.06). |
| `preshift` | TROP4-spietrzone-inwersje-rownosci-demote-regresja | P2 | PLAUSIBLE | Regresja V3.16 demote tylnymi drzwiami; temat pozycji, brak ownera §5. |
| `preshift` | TROP8-wielosesyjny-shared-deploy-kolizja | P3 | n.d. | Problem procesowy/ops (kolizja na `fleet_state.py`), poza roadmapą silnika. |

### 3b. BRAK „OCZEKIWANY" — 6 (verdict = REFUTED, sierota z obalenia, NIE luka)

| src | id | sev | dlaczego brak = OK |
|---|---|---|---|
| `faza1-roots` | out-of-engine-position-gates | P2 | Obalony jako żywy źródłowy root (gates shadow/console-only lub rozbrojone). |
| `faza1-roots` | out-of-engine-position-classifier-drift | P2 | Zwija się w feas-carry/reassignment void; nie osobny. |
| `faza1-roots` | equal-treatment-vs-discriminate-position | P2 | Zwija się w position-gates; część-silnikowa unified. |
| `alloc-family` | R5-pool-universe-name-drop | P2 | 0/14 on-shift zgubionych (autopair seeduje); latentna nota. |
| `alloc-family` | R7-no-gps-position-fiction | P2 | Równe traktowanie działa; 447 wygrywa realną pozycją, 370=luka danych ortogonalna. |
| `alloc-family` | R10-plan-ownership-no-prune | P2 | 8 phantomów inertne (0 retimów); 0 szkodliwych mixed-bag live. |

---

## POKRYCIE

- **94 findingi z 5 źródeł** znormalizowane; **76 (80%) ma właściciela w roadmapie L0-L8**, 18 = `brak`.
- **Roadmapa jest gęsta na warstwach naprawczych:** L6 (kanon+bliźniaki) = 21 findingów, L7 (hardening) = 16, L8 (sprzątanie) = 10 — 47/94 (50%) findingów celuje w te 3 warstwy. Fundament L0-L2 (wiarygodność+prawda-przyrządów+sentinel) = 13 findingów — cienki, ale to keying-point (jego prawdziwość warunkuje resztę).
- **Zbieżność cross-audit (dowód dedupu, nie N niezależnych bugów):** te same korzenie wracają w wielu źródłach —
  - **sentinel (0,0)/V328**: `faza1-roots` coord-sentinel §4 ⟷ `preshift` BUG#2 ⟷ `alloc-family` most K5 → 1 root, owner L2.1.
  - **earliest-pickup-floor**: `faza1-roots` ⟷ `preshift` BUG#1 → owner L4.
  - **frozen vs floor**: `faza1-roots` frozen-committed ⟷ `faza1-konflikty` K-F ⟷ `preshift` TOP-3#1 → owner L7.2.
  - **geometria-ślepa selekcja (P0-A)**: `faza1-roots` geometry-blind ⟷ `faza1-konflikty` K-H ⟷ `alloc-family` P0-A → owner L6.C2.
  - **plan_recheck cofacz (K2)**: `faza1-konflikty` K-D ⟷ `preshift` TROP3 ⟷ courier-plans-lifecycle → owner L3.
  - **R6-cap 35/40**: `faza1-roots` r6-cap ⟷ `faza1-konflikty` K-B → owner L6.B2.
  - **kalibracja zła oś / ETA optymizm**: `faza1-roots` calibration ⟷ `alloc-family` R8/R9 ⟷ `preshift` TOP-3#2/TROP6 → owner L5.1.
- **12 z 13 klastrów konfliktów** (`faza1-konflikty`) mapuje się na rooty rodzin alokacji/pre-shift (owner L0-L7); jedyny bez ownera = **K-M** (sprzeczność wewnątrz kanonu-dokumentu) + **K-E** (temat position-twins/ghost).
- **Wszystkie 14 odłożonych (CAPPED)** znalazły warstwę-domknięcia w krokach L-step (L3/L4.2/L5.2/L6.E1/L6.E2/L7.5/L8.x) — zero CAPPED-sierot, spójne z deklaracją FAZA1 „Domykane w L6.E/L7/L8".

---

## JAWNE LUKI

1. **⭐ NAJWIĘKSZA LUKA — „duch przerzutu" (no-gps TOR1, P0, CONFIRMED) NIE MA WŁAŚCICIELA I MA KONFLIKT WERDYKTÓW.** `reassignment_forward_shadow` (`a_late`/`_SYNTH_POS`) produkuje **59% fałszywych ratunków** ripujących zlecenia kurierom bez GPS/pre_shift — audyt no-gps (29.06) mierzy to jako DOMINUJĄCY ŻYWY problem, widoczny w konsoli (`feed.py` bez `_pos_trusted`, TTL 7min). Roadmapa 30.06 (`faza1-roots`) **REFUTED** te gates jako „shadow/console-only → nie żywy źródłowy root" i nie przydzieliła L-ownera. **To sprzeczność między audytem 29.06 (LIVE, zmierzone) a 30.06 (odbrojone).** Wymaga rozstrzygnięcia Adriana: czy ghost jest nadal live (kandydat na osobny root/warstwę), czy rozbrojony.

2. **Cała rodzina „position-equality twins" jest rozproszona bez jednego rootu §5.** 9 findingów (no-gps: ghost, incomplete-map, fastest_pickup, auto-gate, drive-calib, pre-shift-penalty; faza1: K-E, equal-treatment-vs-discriminate, out-of-engine-position-gates/classifier-drift; preshift TROP4) dotyka TEGO SAMEGO tematu (8 bliźniaków pozycji, `ziomek-change-protocol`), ale roadmapa traktuje część jako REFUTED (silnik unified), część jako latentne miny (flag-OFF), część jako politykę (Q1b). **Brak jednego „owner = unifikacja-twins + checker anty-hardcode-bucket"** — a to dokładnie klasa, którą FAZA1 §0 nazywa „łataną ≥4× i wracającą".

3. **Rozbieżność liczby REFUTED w źródle FAZA1_01** (nagłówek §3 „13" vs 12 wierszy tabeli) — nie doszło do rozstrzygnięcia, który 13. root miał tam być. Zarejestrowałem 12 faktyczne + 1 (coord-sentinel) przeklasyfikowany do CONFIRMED.

4. **Tropy diffuse bez ownera** (preshift TROP1 stale-pos-25min, TROP2 119-silent-except): CONFIRMED z twardymi metrykami (5639×/d, 100s/d) ale nie mają jednego kroku naprawczego — silent-except tylko częściowo dotknięty przez L2.2/L2.3 fail-loud, stale-pos w ogóle nienazwany. Kandydaci na osobne pozycje backlogu.

5. **Magnitudy nie w tym rejestrze.** `files` i `mech` niosą lokalizację + mechanizm, ale ile worków/dzień każdy root psuje = replay/oracle (Faza C, PRZED flipem). Rejestr mówi CO i GDZIE, nie ILE.

6. **Numery linii DRYFUJĄ** (≥3 sesje/dzień/repo; HEAD silnika `8024705` z 30.06). `files:line` w `findings_new.jsonl` = stan z dokumentów źródłowych 30.06 — ETAP-0 każdego fixu MUSI re-grepować.

7. **Zakres źródeł.** Rejestr pokrywa 5 wskazanych dokumentów. NIE obejmuje: pełnych 241 findingów Fazy B (`backing/B*`), 49 werdyktów przyrządów Fazy C, 81 par konfliktowych Fazy D w surowej postaci — te są zdedupowane w 5 dokumentach-wejściach (i tu). 7 „kłamiących przyrządów" z `alloc-family` §C11 (lane C11) zmapuje się na rooty E-klasy FAZA1 (feas-carry, objm-canary, bug4) + no-gps ghost — ujęte przez te wiersze, nie dublowane.
