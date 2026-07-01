# FAZA 1 — DELIVERABLE #5: ZBIEŻNA ROADMAPA KONSOLIDACJI + PLAN 1 PoC

**DRAFT · Audyt spójności Ziomka · sesja tmux 2 · 2026-06-30 · READ-ONLY** (zero kodu/flipów/restartów). Exec-summary; pełny detal per-krok = `backing/F_roadmap.md` (335 linii), pełny PoC = `backing/F_poc_plan.md`.

> **Zbieżna = każdy krok ściśle REDUKUJE ≥1 z 8 metryk entropii i NIGDY nie dodaje kopii** (bramka „zero nowych kopii"). Godzi 5 osi kolejności: kręgosłup zależności F1-F7 · zwrot-na-nawroty · gradient-ryzyka · bramki-czasowe · zero-nowych-kopii. **Każdy krok dotykający kodu = OSOBNY mini-sprint ETAP 0→7 + ACK Adriana.**

---

## 1. WIDOK GŁÓWNY — 9 warstw L0-L8 (zależnościowo uporządkowane)

Legenda ryzyka: 🟢 doc/shadow (0 ryzyka, brak ACK) · 🟡 tooling-nie-silnik (low) · 🔴 **P0 SILNIK** (ETAP 0→7 + ACK + off-peak>14:00 + replay ON↔OFF + parytet + pełna regresja) · ⛔ dotyka inwersji HARD↔SOFT → **ACK Adriana WPROST**.

| L | Warstwa (co konsoliduje) | F1-F7 | Metryki ↓ | Ryzyko | Bramka czasu |
|---|---|---|---|---|---|
| **L0** | **Fundament wiarygodności**: 1 rejestr-flag + harness-prawdy (join GT) + instrumenty-dziedziczą-env + strażniki-shadow | F6 | dead-flag 5→0; fingerprint 63→all; void(false-parity)→0 | 🟢 | — |
| **L1** | **Prawda przyrządów**: serializer-kompletność + reader-rotation-aware + 1 append_jsonl | F6 | metrics-vanish 14 HARD→0; wrong-source 2→0; stale-txt→0 | 🟡 | odblokowuje O2 02.07 |
| **L2** | **Sentinel chokepoint (most K5)**: 1 walidator-ingest + `if coords:`→`_valid()` RAZEM + catch-all rozróżnia | **F3** | sentinel 2046+14456→0; 6-def→1 | 🔴 **P0, LIVE harm** | — |
| **L3** | **plan_recheck przestaje cofać (K2)**: courier-plans GC + pure-read + prune-by-status; regen przez TE SAME bramki | **F2** | zombie 43→0; read-side-effect→0; twin(recanon)→0 | 🔴 + ACK | — |
| **L4** | **Dostępność 1 źródło (najgłębsze)**: `available_from=max(now,shift_start)` RAZ + północ + fail-policy + inwariant | **F1** | copy-floor 17→1; inwariant 0→1; twin(start↔end)→0 | 🔴 + ACK (Q1/Q2 już ACK) | — |
| **L5** | **ETA load-aware (K3)**: kalibracja na osi POŚLIZGU-odbioru + `eta_pickup` decision/display | **F4** | wrong-axis-live→0; display-feeds-decision→0 | 🔴 ⛔HARD | O2/04.07 |
| **L6** | **Kanon + bliźniaki (F5)**: route-order golden + sprint O2 + geometria/de-pile + objm/frozen-lex + słownictwo | **F5** | twin(route 44-75)→0; copy(r6-cap 6→1, route 5→1); layer(geom) | 🔴 + ACK | **≤07-10 / 02.07 / 03.07** |
| **L7** | **Hardening/koherencja (F7)**: R-DECLARED tripwire + frozen↔floor 1-chokepoint + split-guard + concurrency + load | **F7** | unresolved-conflict→0; layer(split)→0; concurrency 4→0 | 🔴/🟡 ⛔ACK D5 | C2 przed re-enable TG |
| **L8** | **Objawy usunięte/sprzątanie (po GO)**: dead-code + caches + dead-producer + clutter + reszta progów | cleanup | dead-code→0; cache 6→0; clutter→0 | 🟡 | — |

**Sekwencja krytyczna:** `L0 (mierzalność) → L1 (widoczność HARD) → L2 (odbuduj pulę, most K5) → L3 (plan_recheck, najwyższy zwrot-na-nawroty) → L4 (dostępność, najgłębsze „nigdy nie wraca") → L5 (ETA prawda) → L6 (kanon+bliźniaki, DATE-GATED) → L7 (hardening) → L8 (sprzątanie).`
**Najgłębsze „nigdy nie wraca" = L4(F1) + L5(F4) + strażniki-L0(F6).** L6.A (route-order) może iść **równolegle z L1** (test, nie zmiana zachowania) bo ma twardy deadline 07-10.

---

## 2. KALENDARZ BRAMEK CZASOWYCH (nadpisują kolejność lokalnie)

| Data | Bramka | Kroki | Zależność krytyczna |
|---|---|---|---|
| **≤ 2026-07-10** | **route-order monitor WYGASA** (`MONITOR_STOP_AFTER`) + pod-certyfikuje dziś | **L6.A1 golden-CI** | niezależne od L2-L5 → **start równolegle z L1** |
| **2026-07-02** | O2-review (at-168/200) + bug4 checkpoint + bundle_calib próg | **L6.B cały** + L6.D3 | **L1.1 serializer MUSI być PRZED** (odsłania SLA-detail) |
| **2026-07-03** | objm peak-verdict (at-200) + frozen-lexqual | L6.D1 + L6.D2 | L0.1 (POST_SHIFT znany) |
| **2026-07-04** | load-aware ETA review (`pickup_slip_monitor`) | L5.1 | monitor poślizgu LIVE od 29.06 |
| **PENDING** | D5 load triple-tax (measure-first→ACK) | L7.6 | oracle „ile razy potrójna kara odbiera LEPSZEMU" |
| **PENDING** | C2 re-enable Telegrama | **MUSI po L7.5** (fcntl) | twardy gate (pending_proposals 3-writer) |

---

## 3. PLAN 1 PoC — „one route-order module" (root R1-A) — TYLKO OPIS, ZERO KODU

**Wybór (vs „one selection key"):** route-order = **najwyższa dźwignia × jedyny z twardym deadline'em (07-10) × najtańszy 1. krok jest bezryzykowny** (golden harness = test, 0 zmiany zachowania). „One selection key" (lex_qual) jest ~już-unified i bramkowany zewnętrznie (03.07) → forsowanie łamie świadomą inwersję. Pełny plan (a-d): `backing/F_poc_plan.md`.

**Stan dziś (świeży grep):** kolejność-jazdy w **5 kopiach / 3 repa / 3 języki, BRAK importu repo↔repo**; `route_podjazdy.order_podjazdy` deklaruje „JEDYNE źródło" ale `fleet_state._build_route` (konsola) NIE importuje go (własna kopia); `panelsync` = martwa 5. kopia; `PICKUP_MERGE_MIN=10` w 4 miejscach; monitor 142 rozjazdy dziś, wygasa 07-10.

**Kształt dwuwarstwowy:**
- **PoC-MIN (1. ACK, właściwy „dowód wykonalności", 0 zmiany zachowania):**
  1. **Golden-fixture equivalence harness** — `proj(order_podjazdy(X)) == proj(_build_route(X)) == proj(build_view(X))` (równość PORZĄDKU: `[(typ, sorted(order_ids))]`; ETA/coords per-powierzchnia legalnie różne) na wspólnym korpusie (replay 142 monitora + edge-case'y kanonu). **Zastępuje wygasający monitor** testem CI bez daty. [NISKIE, ⏱ deadline 07-10]
  2. Usuń MARTWĄ `panelsync/courier_orders.py` (665L) [copy 5→4]
  3. `PICKUP_MERGE_MIN` → 1 importowana stała [threshold-sprawl 4→1]
  4. `courier_api` import fail-soft → **fail-LOUD** (alert, nie `print`) [odsłania cichy twin na zerwaniu importu]
- **PoC-TARGET (osobny pod-ACK, HARD):**
  5. konsola importuje wspólny `route_order` (lub golden-bound) [twin-divergence→0]
  6. **P1+P2 ekstrakcja rdzenia** `route_order.canonical_order` (silnik = źródło) — **DOTYKA HARD** (`_apply_canon_order_invariants` karmi decyzję/regen) → byte-identity ON==OFF na CAŁYM korpusie OBOWIĄZKOWE + pełna regresja + replay
  7. **P3 2. producent** `_save_plan_on_assign` woła kanon + guard `(0,0)` (sprzężony sentinel M) — ZMIENIA zachowanie (zamyka 5-min okno „kanon bez carried-first") → dowód pozytywnego wpływu, nie tylko ON==OFF

**Bliźniaki RAZEM (Przykazanie #0 pkt 3):** rdzeń P1+P2 (render-noga + decyzja-noga) · 2.producent P3 + guard-(0,0) + tripwire `courier_plans.sequence` · importery P4/P5/P7-Kotlin/P8-tsx + 4× `PICKUP_MERGE_MIN` + 2× klaster-odbiorów · higiena P6(usuń martwą)+P4(fail-loud). **Dotyka HARD: TAK częściowo** (P1+P2+P3 = ścieżka kanon/decyzja; P4/P5/P7/P8 = render). **Deadline 07-10 dotyczy tylko PoC-MIN** (test) — bezpieczny, czasowo-krytyczny.

---

## 4. INNE KANDYDATURY PoC / PIERWSZYCH RUCHÓW (gdyby Adrian wolał inny start)

| Kandydat | Dźwignia | Ryzyko | Kiedy |
|---|---|---|---|
| **L1.1 serializer-kompletność** | odsłania 14 HARD-metryk, bramkuje O2 02.07, zero zmiany zachowania (dowód = klucz pojawia się w ledgerze) | 🟡 low | PRZED 02.07 (odblokowuje) |
| **L0.1 fragment: fingerprint + usuń 2 martwe-ON** | kasuje fałszywy-parytet, keying-point całej reszty | 🟢 0 | rozgrzewka |
| **L6.E1 tier-rename (4 nazwy)** | natychmiastowa redukcja audit-friction (join-by-tier), golden ON==OFF | 🟢 0 | rozgrzewka |
| **L2.1 sentinel-ingest** | 🔥 8 kurierów/dzień przestaje znikać z puli (most K5→P0) | 🔴 P0 (bliźniaki haversine↔osrm RAZEM) | wysoki realny harm |

---

## STATUS / CAVEATY
- **DRAFT do przeglądu Adriana** — target + roadmapa = propozycja, nie decyzja. Kolejność godzi 5 ograniczeń; bramki czasowe mogą wymusić lokalne przetasowanie.
- **PoC = TYLKO PLAN.** Wykonanie kodu PoC = OSOBNY ACK + ETAP 0→7. Zero `.bak`/`py_compile`/flip/restart/git w Fazie 1.
- **Linie/daty DRYFUJĄ** — ETAP 0 każdego kroku re-grepuje (m.in. `MONITOR_STOP_AFTER=07-10` do potwierdzenia świeżym grepem).
- **Pełny detal:** `backing/F_roadmap.md` (L0-L8 per-krok + §4 dowód zbieżności „zero nowych kopii" + §5 mapa 26 rootów→warstwa) + `backing/F_poc_plan.md` (a-d + §6 świeże greppy).
