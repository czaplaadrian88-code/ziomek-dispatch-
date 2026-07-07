# SPRINT WIELOAGENTOWY ZIOMKA — plan bezkolizyjny (2026-07-07, ~17:35 UTC)

**Autor:** sesja architekta (rekonesans ETAP 0 protokołu #0, stan zweryfikowany NA ŻYWO).
**Cel:** jednym sprintem, kilkoma agentami RÓWNOLEGLE, domknąć zaległości Ziomka BEZ kolizji (flags.json, restarty, wspólne pliki silnika).
**Zasada nadrzędna:** żaden flip/restart/deploy bez jawnego ACK Adriana (protokół #0 ETAP 6). Kolejność dźwigni = P1→P6 [[priorytety-stabilnosc-jakosc-skala]].

---

## ⚡ STATUS URUCHOMIENIA — 2026-07-07 ~17:40 UTC (ACK Adriana)
**Decyzje Adriana:** Pas A ✅ · Pas B ✅ · Pas C ✅ (= kolejność trasy) · **Pas 0 = wersja ① „dyżur na żądanie"** ✅ · flip O2-K1 **WSTRZYMANY** (wyjaśniony — bezpieczna optymalizacja, nie must-have; wraca bez pośpiechu) · gold furtka odłożona.

**Pas 0 = MODEL ① (zatwierdzony):** FLIPMASTER NIE jest żyjącym procesem. Żadnych flipów między-czasem. Gdy przyjdzie ACK na konkretny flip → uruchamiany jednorazowy agent na tę czynność (flip hot / 1 restart, monitor 1h, rollback gotowy), potem kończy. Werdykty okien czyta bezosobowy timer / agent Pasa A. Póki flipy wstrzymane — Pas 0 buduje tylko GOTOWOŚĆ (runbook 4 flipów). Odrzucone: ② bramka-automat (tylko dla flipów bez ACK), ③ żywa sesja tmux (umiera z oknem — patrz tmux 20 cod-weekly).

**7 agentów odpalonych (~17:40):**
| Agent | Pas | Tryb | Artefakt |
|---|---|---|---|
| A1-o2k2-parity | A | read-only | `A1_o2k2_parity.md` — pomiar parytetu O2-K2 |
| A2-worldreplay | A | read-only | `A2_worldreplay_minus40.md` — diagnoza różnicy −40 |
| A3-okna-zdrowie | A | read-only | `A3_okna_zdrowie.md` — zdrowie 3 okien + adopcja 5b |
| B1-przyrzady | B | worktree | `B1_przyrzady_fix.md` — fix flip-gate verify + coords(0,0) |
| B2-inwarianty | B | worktree | `B2_inwarianty.md` — dozbrojenie pustych slotów |
| C-routeorder | C | worktree | `C_route_order_unified.md` — jedno źródło kolejności (mapa+kontrakt+moduł OFF+golden) |
| Pas0-flipmaster | 0 | read-only | `PAS0_FLIPMASTER_RUNBOOK.md` — runbook 4 flipów + gotowość |

**Reguła sprintu:** nikt poza Pasem 0 nie dotyka flags.json; B/C w worktree, ZERO merge do master; wdrożenie/flip/restart każdego wyniku = OSOBNY ACK Adriana po przeglądzie raportów.

---

## ✅ WYNIKI — SPRINT ZAMKNIĘTY 2026-07-07 ~18:18 UTC (7/7 agentów)
**Dowód bezkolizyjności:** master `39fb1c9` NIETKNIĘTY (0 zmian .py) · flags.json mtime 2026-07-06 22:14 NIETKNIĘTY (O2_CAPZ/ROUTE_ORDER_UNIFIED = BRAK=OFF) · 3 gałęzie worktree (B1/B2/C) NIE zmergowane · zero flipów/restartów.

| Pas | Wynik | Artefakt |
|---|---|---|
| A1 | O2-K2 parytet **MEASURED n=24** (próg 10 ✅), 3 zmiany picku (12,5%) wszystkie 3/3 w dobrą stronę, 0 regres | `A1_o2k2_parity.md` (master) |
| A2 | −40 = kara loadgov (LOADGOV_BAG_PENALTY), **LUKA NAGRYWANIA wr0 nie bug**; wr1 zamyka; naprawy silnika BRAK; opcjonalny bucket schema-aware | `A2_worldreplay_minus40.md` (master) |
| A3 | conditional-ETA 🟢 zdrowe (208 korekt); mode-observer 🟡 cienkie (100% S1, 0×S2/S3); 5b 🔴 adopcja ~2-3 kur./62; **znalezisko: 31/208 gubi restaurację przez HTML-escape nazw → fix przed LIVE** | `A3_okna_zdrowie.md` (master) |
| Pas0 | runbook 4 flipów (O2-K1/K2/K4b/K5); O2-K1 GOTOWY do ACK (po 21:00 Warsaw); **K2 werdykt ZIELONY/PARETO ON na stałe**; K3 lock zdrowy; **0 rozbieżności flag** | `PAS0_FLIPMASTER_RUNBOOK.md` (master) |
| B1 | cmd_verify fix u źródła (czyta plik logu; ON≠OFF: 0→45 markerów); (0,0) guard = zaprojektowany chokepoint → nietknięty (nie zgaduj); regresja czysta | `B1_przyrzady_fix.md` (gałąź `worktree-agent-a3c3127c8de36e50c`) |
| B2 | 3 strażniki/12 testów (R6-one-source, K2-geometria-SOFT<HARD, carried-first lock_first), każdy mutation-probe RED; **dashboard STALE — większość „🔴 slotów" już uzbrojona** | `B2_inwarianty.md` (gałąź `worktree-agent-aa67d97afe1632707`) |
| C | **route_order.py — jedno źródło** (PURE, flaga OFF); mapa 5 luster/3 repa (nie 4); dowód parytetu dziesiątki tys. porównań 0 rozjazdów, canon 10/10 sond KILLED; gotowy do przeglądu | `C_route_order_unified.md` (gałąź `worktree-agent-a8a36495468ae05f0`) |

**Wspólna lekcja (ADR-007):** 3 agenty worktree (B1/B2/C) NIEZALEŻNIE wykryły ten sam artefakt — współdzielony symlink `pkgroot/dispatch_v2` w scratchpadzie + canon-checker widzący sąsiednie worktree (`.claude` poza `_EXCLUDE_DIRS`) → ~23-29 „faili", które NA KANONIE przechodzą. Każdy zweryfikował na kanonie przed zaufaniem. Do utrwalenia: worktree izoluje repo, ale współdzielony pkgroot-symlink i skan `.claude` to punkty styku.

**CZEKA NA ACK ADRIANA (nic nie ruszone):** (1) merge B1+B2 do master (higiena, bezpieczne); (2) przegląd+merge C, potem flip `ENABLE_ROUTE_ORDER_UNIFIED` po replay 0-diff (osobny cykl) + migracja K3/K4/apka; (3) flip O2-K1 (po 21:00) — decyzja Adriana, wstrzymany; (4) fix HTML-escape przed wpięciem conditional-ETA; (5) opcjonalny world-replay bucket. **WYMAGA RĘKI ADRIANA (nie kod):** rozdystrybuowanie apki 5b po flocie (blokuje 5b/feas_carry/autonomię).

---

## 1. STAN NA ŻYWO — CO SIĘ TOCZY W TLE (zweryfikowane 07.07 ~17:30 UTC)

### Okna obserwacji (żywe, czekają na werdykt)
| # | Co | Flaga / stan | Start | Okno domyka | Po werdykcie |
|---|---|---|---|---|---|
| 1 | **K2 geometria** | `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK=True` LIVE | 05.07 18:51 | **DZIŚ ~18:52 UTC** | odczyt → potwierdza K3/O2 |
| 2 | **K3 claim ledger (W0.4 lock)** | `ENABLE_ENGINE_CLAIM_LEDGER=True` LIVE | 06.07 ~22:00 | **~08.07 22:00** | monitor g_maxpile↓, 0 konfliktów |
| 3 | **conditional-ETA shadow** | `ENABLE_ETA_CELL_RESIDUAL_CORRECTION=OFF` (obserwacja) | 07.07 06:47 | **~09.07 06:47** | karta WPIĘCIA w obietnicę (+5,14% MAE) + ACK |
| 4 | **mode-observer 7d** | `dispatch-mode-observer.timer` (shadow) | 07.07 07:17 | **~14.07** | E-4 werdykt → wpięcie mode-layer za flagą + ACK |
| 5 | **GPS 5b geofence** | kod LIVE, adopcja apki ~1/62 🚨 | 05.07 17:19 | **~08-09.07 TYLKO przy onboardingu kotwic** | odblokowuje feas_carry #483000 + dowód autonomii |
| 6 | **world-replay-gate** | `dispatch-world-replay-gate.timer` 02:00, INFORMACYJNY | 06.07 | co noc | ⚠ różnica −40 pkt (485986/7/8) = luka nagrywania v0 → diagnoza przy world_record v1 |
| 7 | **night-guard** | `dispatch-night-guard.timer` 01:15 | — | co noc | ✅ ZIELONY 2 noce (06.07: 4239/0, 07.07: 4394/0) |

**Dowody żywotności (nie deklaracje):** conditional-ETA pisze 195 rekordów `eta_cell_corrected_min` dziś (kod `shadow_dispatcher.py:656`); mode_observer 202 rekordy (ost. 17:27, S1); night_guard_history zielony; shadow_decisions 684 linii, mtime 17:29. flags.json = 272 klucze. master == origin/master (0/0), HEAD `39fb1c9` (mode-layer inkr. 7 KOMPLET).

### Świeże werdykty do KONSUMPCJI (okna domknięte — atq PUSTE, at-jobsy się odpaliły)
- **✅ at-208 O2-K1 λ=0 = GO** (Pn 19:30): Z=20 policy-improved **10,0%**, **regres_o2 = 0**, med ΔO2 +9,65, detour med −2,33/p90 5,84; doubts-readout: 67% tras krótszych, **bilans −59 km/dzień**, koszt capa Z=20 = 5,7% worków. → **flip `ENABLE_O2_CAPZ_RESEQ` czeka ACK** (klucz DO DOPISANIA do flags.json).
- **⏳ O2-K2 pick parity = INCONCLUSIVE** (n=3<10, ost. bieg 05.07): przeliczyć po peakach Pn+Wt (narzędzie `tools/o2_k2_pick_parity.py` gotowe; ⚠ ścieżka uruchomienia `python -m` nie działa z katalogu repo — użyć venv `/root/.openclaw/venvs/dispatch` + cwd `scripts/`).

---

## 2. CO NIEDOKOŃCZONE — KOLEJKA (kod OFF / dowody gotowe / czeka na bramkę)

**A. Kolejka flipów silnika (FLIPMASTER, sekwencyjnie, za ACK):**
1. **O2-K1** `ENABLE_O2_CAPZ_RESEQ` — werdykt GO gotowy → flip po ACK (hot, dopisać klucz).
2. **O2-K2** `ENABLE_SLA_GATE_READY_ANCHOR` — po O2-K1 ON + parytet picku (n≥10) + L3 ≥2d.
3. **K4b** `ENABLE_ETA_LOAD_AWARE` — flip za ACK z jawnym trade-offem (bias odbioru −3,73→+0,42 KOSZTEM p90 +6,1→+10,7).
4. **K5** `PENDING_RESWEEP_LIVE` (live-resweep konsoli) — dry-run driver na wiszących → flip za ACK; ⚠ wymaga geometria ON.
5. **conditional-ETA wpięcie w obietnicę** — po oknie 2d (09.07) + osobna karta + ACK.
6. **mode-layer wpięcie za flagą** — po E-4 (7d, ~14.07) + ACK.
7. **PLANNER_UNIFIED główny** — flip po 2 dniach ciszy SHADOW plannera (refaktor 06-07.07); parytet n=88 DIFF PUSTY.
8. **feas_carry #483000** — po werdykcie 5b (bramkowane adopcją apki).

**B. Decyzje POLITYCZNE Adriana (nie kod):**
- **Gold furtka** `ENABLE_ETA_QUANTILE_R6_BAGCAP` (dziś ON) — nieredukowalna niepewność ogona gold≤4, NIE fix predykcji (potw. 4×). Tradeoff 127 odzysków / 37 realnych spóźnień + kanon. Odłożone.
- objm „ON na stałe" (Faza 4); termin 1. nadzorowanej autonomii; 2 klucze DR off-machine; BFG historia git.

**C. Nowy sprint silnika (czeka GO Adriana):** „**OCHRONA OBIECANEGO ODBIORU PRZY INSERCJI**" — dekompozycja poślizgu (`SLIP_DECOMPOSITION_raport.md`) wskazała DORZUCANIE zleceń po decyzji = 1. śruba (+9,3 med; inna restauracja +14,8; <15 min do odbioru +39). Kara/gate przy insercji przesuwającej już-obiecany odbiór.

**D. Higiena / dług strukturalny (P3-P5):**
- **INV-SRC-ROUTE-ORDER** — jedno źródło kolejności trasy cross-repo (4 kopie/3 repa; monitor wygasa 10.07). Największa dźwignia P3.
- **Puste sloty inwariantów ALOKACJA/FEASIBILITY** (21 slotów — tam bugi wracają).
- **world_record v1 diagnoza** różnicy −40 (case 485986).
- fix przyrządu `scheduled_flip_gate.cmd_verify` (czyta journal zamiast pliku logu → wieczne marker=0).
- źródło (0,0) coords łapanych przez COORD_GUARD (~25/2h); rzut oka log autopair (~08.07); perf peak p95 (osobne źródło ogona).

---

## 3. ZASADY BEZKOLIZYJNOŚCI (wąskie gardła — projekt pasów je respektuje)

| Zasób współdzielony | Reguła |
|---|---|
| **`scripts/flags.json`** | **JEDEN pisarz = FLIPMASTER (Pas 0).** Żaden inny agent nie dotyka. |
| **restart `dispatch-shadow` / `nadajesz-panel`** | tylko FLIPMASTER, off-peak, za ACK. Jeden na raz. |
| **trójka `feasibility_v2` + `route_simulator` + `plan_recheck`** | GORĄCE gardło: O2 (już zbudowane, flip=HOT bez zmiany kodu), route-order-unify, insertion-protection — **wszystkie trzy chcą tej trójki**. → w JEDNYM sprincie budujemy w niej TYLKO JEDNO; reszta = worktree sekwencyjnie lub read-only. |
| **repo dispatch_v2** | równoległe buildy w **worktree per agent** (ADR-007). Commit po ścieżkach, nie cofać cudzego. |
| **panel nadajesz** | osobny obszar (repo+deploy wspólne, inna domena) — [[feedback-multisession-shared-deploy]]. |
| **peak** | Sob 16-21; codz. lunch 11-14 + dinner ~17-21 Warsaw. **Bez restartów w peak.** |

---

## 4. PASY SPRINTU (bezkolizyjne, równoległe)

### 🎛️ PAS 0 — FLIPMASTER / KOORDYNATOR (1 agent, WYŁĄCZNOŚĆ na flags.json + restarty)
Jedyny właściciel stanu żywego. NIE buduje kodu. Konsumuje werdykty, wykonuje flipy sekwencyjnie za ACK.
- Odczyt werdyktu **K2** (domyka 18:52 UTC) → zamknięcie obserwacji.
- Po ACK: **flip O2-K1** (dopisz `ENABLE_O2_CAPZ_RESEQ=true`, hot; monitor 1h; rollback=klucz false).
- Po pomiarze z Pasa A: **flip O2-K2** za ACK.
- Trzyma kolejkę K4b/K5/conditional-ETA/mode-layer — flip dopiero po ich bramkach.
- **DoD:** każdy flip = ACK w czacie + monitor + wpis do trackera; zero flipów spekulacyjnych.

### 🔬 PAS A — POMIARY / WERDYKTY (read-only, 0 kolizji — 2-3 agentów)
Wszystko READ-ONLY, żadnej zmiany w systemie. Wynik = raporty do `eod_drafts/2026-07-07/`.
- **A1:** przeliczyć **O2-K2 pick parity** po peakach Pn+Wt (naprawić ścieżkę uruchomienia venv) → werdykt MEASURED/INCONCLUSIVE dla Pasa 0.
- **A2:** diagnoza **world-replay −40** (case 485986/7/8: skąd stała różnica 40 pkt replay vs zapis; czy luka nagrywania v0 czy realny rozjazd) → raport.
- **A3:** zdrowie okien — conditional-ETA (rozkład korekt, trafienia mapy), mode-observer (rozkład S1/S2/S3, defer_eligible), K3 monitor (g_maxpile), 5b adopcja (DISTINCT kurierzy vc60) → 1 raport zbiorczy „stan okien".
- **A4 (opcjonalnie):** świeża mapa 0a `eta_truth_map --since 2026-07-02T12:00` (miarodajna dopiero ~10.07 — przygotować przyrząd, oznaczyć „za wcześnie" jeśli <7d).

### 🧹 PAS B — HIGIENA ROZŁĄCZNA z trójką O2 (worktree, flagi OFF — 2 agentów)
Pliki NIE-kolidujące z feasibility/route_simulator/plan_recheck.
- **B1:** fix przyrządu `tools/scheduled_flip_gate.py::cmd_verify` (czytać też plik logu, nie tylko journal — koniec fałszywego marker=0). + fix źródła (0,0) coords w COORD_GUARD (diagnoza + guard). Osobne pliki tools/ + osrm_client.
- **B2:** **puste sloty inwariantów** ALOKACJA/FEASIBILITY (`ZIOMEK_INVARIANTS.md` + `tests/`) — dopisać brakujące inwarianty jako testy-strażniki (nie zmienia silnika, tylko dozbraja). Rozłączne z buildem C (testy vs kod).
- **DoD:** worktree, regresja kanonu zielona, PR-gałąź gotowa do merge po sprincie.

### 🏗️ PAS C — JEDEN duży build silnika (worktree, flaga OFF) — WYBÓR Adriana
Dotyka trójki O2 → **w tym sprincie tylko JEDNO z poniższych** (reszta = następny sprint):
- **C-opcja-1: INV-SRC-ROUTE-ORDER** — jedno źródło kolejności trasy cross-repo (P3, największa dźwignia, monitor wygasa 10.07). Usuwa korzeń „carried-first naprawiane 10×".
- **C-opcja-2: OCHRONA OBIECANEGO ODBIORU PRZY INSERCJI** — kara/gate na dorzutkę przesuwającą obiecany odbiór (1. śruba poślizgu; czeka GO Adriana).
- **DoD:** flaga OFF→shadow→dowód (ON≠OFF, replay pozytywny, pełna regresja + e2e), worktree, PLIK MAPY KOMPLETNOŚCI (bliźniaki RAZEM). Flip = następny cykl za ACK.

### 🟩 PAS D — PANEL NADAJESZ (opcjonalny, całkiem osobny obszar)
Jeśli chcesz równolegle ruszyć panel/konsolę — osobne repo/deploy, zero kolizji z silnikiem. (Do dookreślenia — poza tym rekonesansem.)

---

## 5. SEKWENCJA / HARMONOGRAM (bezkolizyjny)

```
TERAZ (peak dinner)      : Pas A + Pas B + Pas C startują (read-only + worktree, ZERO restartów)
                           Pas 0 czeka na koniec peaku
~18:52 UTC (koniec peak) : Pas 0 odczytuje werdykt K2
wieczór off-peak + ACK   : Pas 0 flip O2-K1 (po Twoim GO)  ← pierwsza dźwignia
po peaku Wt (dane)       : Pas A oddaje pomiar O2-K2 → Pas 0 flip O2-K2 za ACK
~08.07 22:00             : werdykt K3 lock → Pas 0 potwierdza
~09.07 06:47             : okno conditional-ETA → karta wpięcia + ACK
~14.07                   : E-4 mode-layer → wpięcie za flagą + ACK
Pas B/C mergują po sprincie (regresja zielona), flipy w kolejnym cyklu za ACK
```

## 6. CO WYMAGA TWOJEJ DECYZJI (zanim ruszę)
1. **Zakres uruchomienia** — całość (A+B+C+0) czy podzbiór?
2. **Pas C** — route-order-unify (dług/dźwignia) czy insertion-protection (jakość obietnicy)?
3. **ACK na flip O2-K1** — werdykt GO gotowy; flip wieczorem off-peak? (osobno, nie ten sam wieczór co inne flipy).
4. Panel nadajesz w tym sprincie (Pas D) — tak/nie?

---
Powiązane: [[ziomek-status-konsolidacja-2026-07-05]] · [[ziomek-advisory-tura1-tura2-exec-2026-07-07]] · [[shadow-jobs-registry]] · [[priorytety-stabilnosc-jakosc-skala-2026-07-03]] · [[ziomek-change-protocol]] (protokół #0) · ADR-007 (worktree).
