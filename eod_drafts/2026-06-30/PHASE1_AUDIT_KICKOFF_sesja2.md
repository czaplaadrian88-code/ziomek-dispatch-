# FAZA 1 — KICKOFF: pełny audyt spójności Ziomka (zlecenie sesja 4 → sesja tmux 2)

**Od:** sesja tmux 4 (Adrian) · **Do:** sesja tmux 2 · **Data:** 2026-06-30 · **Tryb:** READ-ONLY (ZERO zmian w kodzie/serwisach).
**Twoje zadanie:** wykonać FAZĘ 1 = pełny, wyczerpujący audyt spójności architektonicznej Ziomka, który znajdzie WSZYSTKIE niespójności i wyprodukuje DRAFT drogi do „architektonicznego ideału". To audyt — nic nie naprawiasz.

## 0. PRZECZYTAJ NAJPIERW (dokładnie, w tej kolejności)
1. `eod_drafts/2026-06-30/ZIOMEK_COHERENCE_AUDIT_DESIGN.md` — **TWÓJ plan**: taksonomia 15 klas/7 rodzin (§1), fazy A-F (§2), mechanizm pokrycia moduł×klasa (§3), kontrakty stanu docelowego + dashboard entropii (§4), kształt workflow/skala (§5), bezpieczniki (§6).
2. `eod_drafts/2026-06-30/ZIOMEK_UNIFIED_AUDIT_2026-06-30.md` — **SEED**: 2 piloty (alokacja + pre-shift) już zwalidowały 7 wspólnych korzeni K1-K7 i fundament F1-F7. To HIPOTEZA do POTWIERDZENIA i ROZSZERZENIA, nie kopiowania.
3. `/root/.claude/projects/-root/memory/ziomek-change-protocol.md` — reguły C1-C11, mapa kompletności, 8 bliźniaków pozycji, wzorce #1-#18. Stosuj C9/C11 (oracle) i C1 (multi-sesja).

## 1. ETAP 0 — RECON (obowiązkowy, zanim odpalisz rój)
- `git -C /root/.openclaw/workspace/scripts/dispatch_v2 log --oneline -15` + bieżący stan flag **EFEKTYWNY w procesie** (który serwis/drop-in, nie env-default ani sam flags.json) + `systemctl` żywych serwisów + świeżość danych.
- **C1 multi-sesja:** `tmux ls` — sesja 4 (ja) może jeszcze żyć read-only; INNE sesje nie mogą edytować plików silnika gdy Ty audytujesz (Ty też nie edytujesz — read-only, więc kolizji brak). Nie deployuj, nie restartuj.
- **Numery linii DRYFUJĄ** (commity z dziś) — KAŻDY cytat plik:linia bierz ze świeżego grep, nie z seed-doców.
- Testy bazowe: odnotuj baseline (znane ~10 pre-existing FAIL — to też przedmiot klasy „integralność test-suite").

## 2. CO ZBUDOWAĆ I ODPALIĆ — workflow wg designu §2/§5
Zbuduj i odpal wieloagentowy Workflow realizujący Fazy A-F (design §2). Skala ~80-130 agentów (Adrian: „nie liczy się koszt, liczy się jakość/stabilność"). Fazy:
- **A Inwentarz+taksonomia:** mapa 10 warstw→pliki→serwisy/timery + rejestr reguł(warstwa) + rejestr flag(efektywny stan) + rejestr przyrządów (shadow/monitor/at-job).
- **B Sweep wzorców:** każdy z 15 anty-wzorców × pasma modułów, równolegle, z JAWNĄ deklaracją pokrycia (ledger MODUŁ×KLASA = 100%, luka jawna nie domyślna).
- **C Lane RUNTIME-ORACLE (OBOWIĄZKOWY, C9/C11):** ODPAL każdy przyrząd z rejestru A na realnej próbce, policz prawdę DRUGĄ metodą → validated/void/untested. (Poprzedni 86-agentowy audyt był read-only i PRZEOCZYŁ oba P0 właśnie tu — nie powtórz.)
- **D Mapa KONFLIKTÓW (oś I):** graf interakcji reguł+flag → inwersje HARD↔SOFT, sprzeczności, niezdefiniowana/niespójna precedencja między ścieżkami, sprzężenia flag.
- **E Dedup-do-źródła + ADWERSARYJNA weryfikacja:** scal instancje do distinct-rootów (inaczej zawyżysz „chaos"); każdy root → 2 niezależne refutery → CONFIRMED/PLAUSIBLE/REFUTED + is_really_source + is_really_open.
- **F SYNTEZA (DRAFT):** stan docelowy per klasa + dashboard entropii (liczby DZIŚ) + zbieżna roadmapa (każdy krok redukuje entropię, bramka „zero nowych kopii") + **PLAN 1 PoC** (opisowy — NIE wykonuj kodu).

## 3. ZAKRES (decyzja Adriana = cały okołosystem)
Rdzeń decyzyjny `dispatch_v2` (10 warstw) + czasówki + panel-watcher/recanon + most paczki + cross-repo `nadajesz_clone/panel` + `courier_api` + apka `courier-app`. **GRANICA: STOP na dyspozytorni — NIE wciągaj Mailek ani Papu** (inne domeny, osobny audyt). Klasa **J (cross-repo/multi-proces)** jest pierwszoplanowa.

## 4. TWARDE OGRANICZENIA (DoD)
- **ZERO kodu, ZERO edycji plików silnika, ZERO restartów/deployów.** Tylko czytanie + pliki-raporty w `eod_drafts/2026-06-30/`.
- **PoC = tylko PLAN opisowy** (szkielet docelowego modułu, lista call-site'ów, plan testu parytetu) — wykonanie kodu PoC = OSOBNY ACK Adriana, nie teraz.
- **Target + roadmapa = DRAFT do przeglądu Adriana**, nie decyzja. Oznacz jawnie „DRAFT".
- **Caveaty oracle** (button-truth vs fizyka GPS, realized-leg overhead) — oznacz wynik `proxy-certyfikowany` vs `ground-truth`.
- **Dedup PRZED liczeniem** — „N findingów" ≠ „N problemów".

## 5. DELIVERABLES (pliki do `eod_drafts/2026-06-30/`)
1. Mapa anty-wzorców (15 klas, instancje plik:linia, zdedup do rootów, werdykt CONFIRMED/PLAUSIBLE/REFUTED).
2. Mapa konfliktów (co się z czym bije + status precedencji).
3. Rejestr przyrządów (validated/void/untested — czemu ufać przy flipach).
4. DRAFT stanu docelowego (8 kontraktów) + DASHBOARD ENTROPII (copy-count, twin-divergence, void-instrument, dead-flag, layer-violation, unresolved-conflict — DZIŚ → cel).
5. DRAFT zbieżnej roadmapy + plan 1 PoC.
6. Ledger pokrycia MODUŁ×KLASA (dowód „sprawdziłem wszystko").
7. Krótki handoff/raport końcowy.

## 6. PO ZAKOŃCZENIU
Napisz raport końcowy + **powiadom Adriana w tej sesji** (tmux 2). **STOP przed jakąkolwiek naprawą** — naprawa = Faza 3, osobne sesje, protokół ETAP 0→7, ACK per fala. Wątpliwość/konflikt priorytetów → PYTAJ Adriana, nie zgaduj.
