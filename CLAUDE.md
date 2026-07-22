# 🧭 START TUTAJ — dispatch_v2 (CLAUDE.md, dla sesji Claude Code)

> **Parytet:** ten plik i `AGENTS.md` (obok, dla Codex) mają IDENTYCZNY zestaw dyrektyw
> — różnią się tylko adresatem. Zmiana jednej dyrektywy tu = ta sama zmiana w `AGENTS.md`
> (REGUŁA DWÓCH MIEJSC, niżej).
>
> **Ten plik NIE zawiera już stanu bieżącego ani historii wersji.** Snapshoty sprintów
> V3.7–V3.28, stare tabele flag, TASK B, firmowe konto, Faza 7 itd. zostały wycięte
> (były zamrożonym snapshotem 2026-05-10). Bieżący stan weryfikuj w kodzie, runtime i handoffie —
> NIGDY w żadnym dokumencie-snapshotcie. Gdzie szukać wyciętej treści:
> - **Historia wersji / commity / tagi / incydenty** → `/root/.claude/projects/-root/memory/dispatch_history.md` + `git log`, `git tag`.
> - **Co robić teraz / co zrobione** → `/root/.claude/projects/-root/memory/todo_master.md` → `/root/.claude/projects/-root/memory/sprint_timeline.md` (CURRENT HANDOFF).
> - **Backlog / tech-debt** → `/root/.claude/projects/-root/memory/tech_debt_backlog.md` + `ZIOMEK_BACKLOG.md`.
> - **Reguły biznesowe** → `/root/.claude/projects/-root/memory/project_overview.md` + `/root/.claude/projects/-root/memory/ZIOMEK_REGULY_KANON.md`.
> - **Panel API / infra / porty / kurierzy** → `ZIOMEK_MASTER_KB.md` Część III (evergreen).
> - **Live wartości flag** → `flags.json` + efektywne środowisko procesu (FLAG_FINGERPRINT) — NIGDY stare tabele.

## 🧱 NAPRAWA U ŹRÓDŁA — ZAKAZ ŁAT I KOSMETYKI (Adrian 2026-07-22)
- Objaw, UI, etykieta albo raport są WYŁĄCZNIE punktem startowym trace; nie są domyślnym miejscem fixu.
  Przed zmianą prześledź przyczynę do warstwy, która tworzy błędny stan lub decyzję.
- Przed implementacją MUSI powstać pełna mapa wszystkich writerów i konsumentów dotkniętego kontraktu,
  także ścieżek bliźniaczych, recovery, cache, serializerów, monitorów i narzędzi operatorskich.
- Naprawa MUSI ustanawiać jednego kanonicznego ownera/source kontraktu. Zakazane jest dokładanie kolejnego
  fallbacku, warunkowego `if`, etykiety, render-only override albo duplikatu polityki zamiast usunięcia źródła.
- Konkurencyjni writerzy tej samej prawdy MUSZĄ zostać usunięci albo jawnie wygaszeni; pozostawienie starej
  ścieżki jako cichego fallbacku oznacza zmianę częściową i `HOLD`.
- Bramka testowa MUSI zawierać: negatywny oracle reprodukujący defekt, mutation test który po usunięciu lub
  odwróceniu fixu ponownie czerwienieje, oraz ratchet blokujący powrót duplikatu/writera/obejścia.
- Tymczasowa łata jest dopuszczalna WYŁĄCZNIE jako jawny kill-switch: z osobnym owner ACK, terminem usunięcia,
  rollbackiem i otwartym ledger gate. Bez tych czterech elementów łata jest zabroniona.
- `CLEAN` reviewera nie obala reprodukowalnego defektu. Sprzeczne werdykty oznaczają `HOLD`, zachowanie repro
  jako prawdy roboczej i obowiązkową niezależną weryfikację przed zmianą gate'a lub promocją.

## 🚦 OTWARTE BRAMKI / DŁUG — LEDGER MECHANICZNY (od 21.07, OBOWIĄZKOWY RYTUAŁ)
Kanoniczna PRAWDA o otwartym długu = baza `/var/lib/ziomek-process-gates/gates.sqlite3` (0600).
JEDYNY interfejs: `dispatch_v2/tools/process_debt_gate.py` (add/transition/list/show/export; FSM
BUILT_OFF→WAIT_DATA→READY_FOR_REVIEW→READY_FOR_OWNER→OWNER_ACKED→APPLIED→VERIFIED→CLOSED
+ REJECTED/SUPERSEDED; CAS + audyt). Widok: `dispatch_v2/OPEN_GATES.md` (GENERATED; odśwież
`export --format open-gates`).
- START SESJI: przeczytaj `dispatch_v2/OPEN_GATES.md` ZANIM weźmiesz nowe zadanie.
- KONIEC ZADANIA/BRAMKI: `transition` w ledgerze z dowodem (SHA+hash) — notatka w memory NIE wystarcza.
- AT-JOBY: planuj WYŁĄCZNIE przez `dispatch_v2/tools/at_gate.py` (rejestracja+reconcile; job zniknął bez werdyktu = ALARM w widoku).
- Kolektor propozycji: `tools/process_debt_collect.py` (`--apply` tylko świadomie). `todo_master.md` = kontekst; ledger = prawda.

## Kolejność czytania — NIE skanuj repo, wszystko ma mapę
1. **Przykazanie #0** (niżej) — JAK bezpiecznie zmieniać Ziomka; bez wyjątków.
2. **`docs/CODEMAP.md`** — spis treści repo + „gdzie szukać czego" + pułapki nawigacyjne.
3. **`docs/ARCHITECTURE.md`** — 10 warstw, przepływ danych, punkty wejścia. Kanon kontraktów:
   `ZIOMEK_ARCHITECTURE.md` + `ZIOMEK_INVARIANTS.md` + `ZIOMEK_DEFINITION_OF_DONE.md`.
4. **Bieżący stan** → `/root/.claude/projects/-root/memory/` (`todo_master.md` → `sprint_timeline.md` CURRENT HANDOFF).
5. **Decyzje projektowe** („dlaczego tak jest") → `docs/decisions/` (ADR-001..008 + ODR-001/ODR-002 —
   decyzje właścicielskie 12.07: owner-decisions + autonomy authority).
6. Dalej TYLKO pliki potrzebne do zadania — CODEMAP wskaże które.

## 🧰 SKILLE = DOMYŚLNE NARZĘDZIA KAŻDEJ SESJI (od 17.07)
Jeżeli dla czynności istnieje skill — **UŻYWASZ skilla, nie ręcznych komend** (drivery mają bramki ACK,
oracle i selftesty pod nocnym strażnikiem; ręczne odtwarzanie = dryf i pominięte bezpieczniki).
Katalog + zasady: `.claude/skills/README.md`.

| Robisz… | NAJPIERW odpal |
|---|---|
| start sesji / „co się dzieje" / wybór zadania | `python3 .claude/skills/ziomek-cto/driver.py brief` |
| planujesz JAKĄKOLWIEK zmianę silnika (ETAP 3 #0 — mapa kompletności/bliźniaki) | `python3 .claude/skills/ziomek-cto/driver.py scope "<temat>"` |
| diff gotowy, przed commitem (bramka DoD) | `python3 .claude/skills/ziomek-cto/driver.py dod <diff\|ref> --evidence <plik>` (exit 1 = STOP) |
| koniec sesji / wpis handoff do memory | `python3 .claude/skills/ziomek-cto/driver.py handoff` |
| diagnoza usług / werdykt strażnika / przecieki / flagi / suita | `.claude/skills/run-dispatch-v2/driver.sh health` (subkomendy: `guard`/`litter`/`flags`/`collect`/`test`) |
| kandydat (skill/patch/brama) przed promocją/merge | `python3 .claude/skills/ziomek-blind-review/driver.py blind <katalog>` → świeży recenzent → `check <verdict.json>` |

## 📜 REGUŁA DWÓCH MIEJSC (Adrian 17.07)
Każda dyrektywa sesyjna (routing skilli, zasady pracy, bramki, bootstrap) MUSI być zapisana równolegle
w DWÓCH miejscach, bo Claude czyta CLAUDE.md a **Codex czyta AGENTS.md**: repo `CLAUDE.md` ↔ `AGENTS.md`
(obok) oraz globalnie `/root/CLAUDE.md` ↔ `/root/.codex/AGENTS.md`. Zmiana jednego bez drugiego =
zmiana częściowa (niezakończona).

## ⚙️ Twarde minimum środowiskowe
- **Testy WYŁĄCZNIE** `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` (systemowy python3
  nie ma ortools → fałszywe faile).
- **Żywy stan** = `/root/.openclaw/workspace/dispatch_state/`. Katalog `dispatch_state/` w TYM repo = TYLKO dane epaki, NIE stan live.
- **Log decyzji silnika** = `../logs/shadow_decisions.jsonl`.
- **Flagi = 3 światy** (ADR-004): silnik `../flags.json` — część czytana hot-reload przez `C.flag()`/`decision_flag`,
  ale **flagi module-level ładują się dopiero przy starcie procesu → po ich zmianie WYMAGANY restart** (KANON v1.3).
  Panel `flags.systemd.env` + drop-iny (⚠ `systemctl show -p Environment` ich NIE pokazuje); apka drop-iny
  systemd + `courier_api/config.py`. W wątpliwości sprawdź efektywne środowisko procesu i FLAG_FINGERPRINT, nie default kodu.
- **NIGDY nie restartuj `dispatch-telegram` ani nie wdrażaj w peaku bez jawnego ACK.** Re-enable = pełen deploy.

## 🛡️ TWARDE BEZPIECZNIKI OPERACYJNE (nie do pominięcia, żaden skill ich nie znosi)
- **Workflow per-step:** draft → ACK → `cp .bak` → edit → `py_compile` → import check → test → commit → tag → (restart) → verify → stop na ACK. Granularne tagi jako punkty rollbacku.
- **NIGDY nie restartuj żadnego systemd bez `py_compile` + import check** (dispatch-telegram dodatkowo wymaga explicit ACK w czacie).
- **Zero `jq`** na serwerze (JSON manipuluj Pythonem). **`sed` tylko do ODCZYTU**, nigdy do edycji.
- **Zakaz heredoców z cudzysłowami.**
- **Atomic writes:** temp → `fsync` → `rename` (nigdy zapis w miejscu na żywym stanie).

---

# ⛔ PRZYKAZANIE #0 — ZANIM TKNIESZ ZIOMKA (czytaj PIERWSZE)
**Każda zmiana / naprawa / upgrade dispatchu (silnik, feasibility, scoring, selekcja, kanon/plan, flagi,
metryki, konsola/apka, config, integracja) idzie PROTOKOŁEM — bez wyjątków:**
➡ **`/root/.claude/projects/-root/memory/ziomek-change-protocol.md`** — **wklej z niego PROMPT na początku
zadania** i przejdź ETAP 0→7. Skrót nieprzeskakiwalny:
- **(0)** stan na żywo + testy bazowe ZIELONE; ustal efektywne flagi procesu (3 światy), nie default.
- **(1)** fix U ŹRÓDŁA we właściwej z 10 warstw — nie łatka na renderze.
- **(2)** SOFT nie osłabia HARD; nie cofaj świadomych inwersji P‑1..P‑7 bez ACK.
- **(3) MAPA KOMPLETNOŚCI** — wszystkie miejsca danej klasy, **bliźniacze ścieżki RAZEM** (best_effort↔objm_lexr6,
  feasibility↔greedy↔plan_recheck, serializer A+B, 4 handlery recanon, każdy importer/konsument).
- **(4)** dowody nie deklaracje: flaga ON≠OFF (test), metryka w `shadow_decisions.jsonl`, parytet bliźniaków,
  checkery flag + inwarianty, **PEŁNA regresja Ziomka vs baseline + e2e przez WSZYSTKIE dotknięte warstwy**.
- **(5)** replay → dowód **POZYTYWNEGO wpływu** (metryka docelowa lepsza ON↔OFF, nie tylko brak regresji) + okno 2 dni.
- **(6)** backup→py_compile→test(kanoniczna ścieżka)→git log -3→ACK→1 restart (NIGDY telegram/peak bez OK).
- **(7)** rollback gotowy.

**Zmiana częściowa = NIEZAKOŃCZONA. Wątpliwość co do priorytetów/inwersji → PYTAJ Adriana, nie zgaduj.**

➡ **Kanon architektury** (zatwierdzony 01.07): `ZIOMEK_ARCHITECTURE.md` (10 warstw + 6 filarów + 8 kontraktów
+ rejestr bliźniaków) + `ZIOMEK_INVARIANTS.md` (co MUSI być prawdą + strażnicy) + `ZIOMEK_DEFINITION_OF_DONE.md`
(DoD 1 ekran) + `tools/entropy_dashboard.py` (8 metryk — **re-run po KAŻDEJ fali, metryki mają MALEĆ**).

---

# ⚖️ NIEZALEŻNA WERYFIKACJA CTO (zasada globalna, Adrian 19.07)
**Nigdy nie ufaj CTO na słowo.** Opinia / rekomendacja / werdykt `ACCEPT`/`REJECT` CTO = materiał do
niezależnej analizy, a NIE dowód ani zgoda ownera. Sprawdź tezy CTO we własnym zakresie (kod, diff, runtime,
testy, logi, dane), porównaj realną alternatywę (poprawność, skutki uboczne, bezpieczeństwo, utrzymanie,
koszt, rollback), a przy rozbieżności lub sporze o zmianę nieodwracalną/produkcyjną — **zatrzymaj się do
decyzji Adriana**. Werdykt CTO nie omija authority, Przykazania #0 ani bramek ownera.

---

# 🔒 TWARDE GRANICE / BRAMKI ACK
Jeśli Adrian jawnie zlecił zakres — to GO dla analizy i implementacji; nie pytaj ponownie o techniczne kroki
w tym zakresie. Osobny **biznesowy ACK** jest nadal wymagany przed:
- flipem flagi zmieniającej decyzje; restartem/deployem procesu produkcyjnego; migracją/modyfikacją danych runtime;
- pracą w peaku; re-enable lub restartem `dispatch-telegram`;
- zmianą relacji HARD/SOFT lub precedencji P‑1..P‑7 / W‑1..W‑6; operacją nieodwracalną.

ODR-002: żaden skill ani dokument NIE nadaje execution authority — tylko właściciel podnosi poziom autonomii.
Jawne polecenie w AKTUALNEJ sesji = ten ACK; nie przenoś zgody ze starego sprintu/czatu.

**Multi-sesja (wspólne repo):** jedna sesja = FLIPMASTER (tylko ona rusza `flags.json`/deploy/restart w oknie);
commituj jawnym pathspec (nigdy `git add -A`); nigdy nie cofaj/nadpisuj cudzego WIP; przed commitem `git log -3`
+ `git status`; baseline ZIELONY przed zmianą, pełna regresja vs baseline po; manifest strażnika re-seed przy
zmianie zbioru nodeidów (`night_guard --update-manifest`, fail-closed).

---

# 📌 Kontrakty Sprintu 4 (LIVE od 2026-07-10, master 70af4fa) — obowiązują każdą sesję
- **HERMETIC-GUARD:** root `dispatch_v2/conftest.py` sandboxuje DISPATCH_STATE_DIR + blokuje zapis/kasowanie
  żywych `dispatch_state`/`scripts/logs`/`flags.json` (też w subprocesach). `RuntimeError "HERMETIC-GUARD"`
  w teście = TEST nieizolowany — napraw TEST (tmp_path/monkeypatch), NIGDY nie osłabiaj guarda. Dowód:
  `HERMETIC_STRICT=1 pytest tests/` = 0 failed; kwarantanna live tylko w `tests/hermetic_quarantine.json`.
- **FLAGI:** każda nowa/zmigrowana flaga MUSI trafić do `tools/flag_lifecycle_registry.json`. **Re-seed ZAWSZE
  `tools/flag_lifecycle_seed.py --merge` — bez `--merge` zabijesz kurację** (seeder ostrzega). Checker
  `tools/flag_lifecycle_check.py [--live]` = exit 0.
- **TOŻSAMOŚĆ KURIERA:** kanon = pakiet `dispatch_v2/identity/`. Onboarding TYLKO przez `courier_admin.add_new_courier`
  (transakcja 5 plików, w tym `courier_names`). Pełne nazwiska = grafik. NIE dodawaj inline kopii norm/resolverów.
- Artefakty: `eod_drafts/2026-07-10/SPRINT4_*.md`.

---

# 🎯 3 ZASADY KARDYNALNE (NIENEGOCJOWALNE)
- **Z1** — Autonomia = cel nadrzędny, niezależnie od dnia/pory.
- **Z2** — Jakość ponad szybkość ZAWSZE, root cause przed fix.
- **Z3** — Buduj na lata, fix u źródła, bez łat i skrótów tworzących nowy dług.

Kod i stan runtime = dowód. Dokumentacja = wskazówka do weryfikacji. Zmiana częściowa = niezakończona.
Wiedza domenowa Adriana ma pierwszeństwo przed intuicją modelu.

**Owner:** Adrian Czapla <ac@nadajesz.pl>
