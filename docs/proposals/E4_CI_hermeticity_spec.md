# E4 — Spec hermetyczności CI dla `validate.py` (ziomek-change-gate)

**To jest SPEC, nie patch.** Nic tu nie stosuję. Cel: żeby walidator skilla
`validate.py` wychodził `exit 0` na świeżym klonie repo `dispatch_v2`, bez
katalogów hosta `/root/handover` i `/root/.claude/projects/-root/memory` (oraz
pozostałych ścieżek spoza repo). Dziś na czystym klonie wychodzi `1`.

Plik: `dispatch_v2/docs/codex-skills/evals/ziomek-change-gate/validate.py` (3185 linii).

---

## 1. Mechanizm porażki (dlaczego exit 1)

Pętla w `validate_navigation` sprawdza istnienie KAŻDego celu prelude+bootstrap na dysku:

- **l. 827-831** (rdzeń):
  ```python
  for _, entry_class, _, target in prelude + bootstrap:
      if entry_class == "DYNAMIC":
          continue
      path = Path(target) if target.startswith("/") else (NAVIGATION_FILE.parent / target)
      require(path.resolve().exists(), f"navigation target does not exist: {target}")
  ```
  ⚠ Klasa `MANDATORY`/`CONDITIONAL` NIE zwalnia z tej bramy — `CONDITIONAL`
  (`/root/handover/*`) też jest sprawdzane na istnienie. Klasa wpływa tylko na
  tekstowe dopasowanie do `EXPECTED_*` (l. 826), nie na existence-gate.
- **l. 404-406** `require` podnosi wyjątek: `if not condition: raise ValidationError(message)`
  (`class ValidationError(RuntimeError)` — l. 375).
- **l. 3044** `validate_navigation(navigation_text)` wołane bezwarunkowo w `main()`.
- **l. 3092-3094** łapie i zwraca 1:
  ```python
  except (OSError, UnicodeError, ValidationError) as exc:
      print(json.dumps({"status": "validated_static_scope_error", "error": str(exc)}, ...))
      return 1
  ```
- **l. 3185** `sys.exit(main())` → kod wyjścia 1.
- **Brak env-hatcha:** `grep -c 'getenv|os.environ' = 0`. Nie ma dziś żadnego przełącznika.

Pozostałe wejścia walidatora (registry/cases/schematy/pliki skilla) kotwiczą do
`ROOT`/`EVAL_DIR` (l. 20-33: `ROOT = Path(__file__).resolve().parents[4]`), więc
istnieją w świeżym klonie. Jedyna brama zależna od hosta = existence-check tych 11 ścieżek.

---

## 2. Zewnętrzne zależności ścieżkowe (dokładnie 11, file:linia + cytat)

Wszystkie to elementy `EXPECTED_PRELUDE` (l. 160-163) i `EXPECTED_BOOTSTRAP`
(l. 164-182), pole nr 4 tupli = ścieżka absolutna spoza drzewa repo:

| # | linia | grupa | cytat (pole ścieżki) |
|---|-------|-------|----------------------|
| 1 | 161 | agent-host | `(1, "MANDATORY", "ROOT_AGENTS", "/root/AGENTS.md")` |
| 2 | 162 | agent-host | `(2, "MANDATORY", "CODEX_AGENTS", "/root/.codex/AGENTS.md")` |
| 3 | 165 | live-workspace | `(1, "MANDATORY", "CLAUDE_86", "/root/.openclaw/workspace/scripts/dispatch_v2/CLAUDE.md")` |
| 4 | 171 | memory | `(7, "MANDATORY", "MEMORY_INDEX", "/root/.claude/projects/-root/memory/MEMORY.md")` |
| 5 | 172 | memory | `(8, "MANDATORY", "TODO_MASTER", "/root/.claude/projects/-root/memory/todo_master.md")` |
| 6 | 173 | memory | `(9, "MANDATORY", "SPRINT_TIMELINE", "/root/.claude/projects/-root/memory/sprint_timeline.md")` |
| 7 | 174 | memory | `(10, "MANDATORY", "SHADOW_JOBS", "/root/.claude/projects/-root/memory/shadow-jobs-registry.md")` |
| 8 | 175 | memory | `(11, "MANDATORY", "BUSINESS_CANON", "/root/.claude/projects/-root/memory/ZIOMEK_REGULY_KANON.md")` |
| 9 | 176 | memory | `(12, "MANDATORY", "CHANGE_PROTOCOL", "/root/.claude/projects/-root/memory/ziomek-change-protocol.md")` |
| 10 | 178 | handover | `(14, "CONDITIONAL", "HANDOVER_MAP", "/root/handover/MAPA_WIEDZY.md")` |
| 11 | 179 | handover | `(15, "CONDITIONAL", "HANDOVER_TODO", "/root/handover/CO_TRZEBA_ZROBIC.md")` |

Uwagi:
- #3 (`CLAUDE_86`) ma bliźniaka w repo pod ścieżką względną, ale bootstrap pinuje
  **absolutną** ścieżkę żywego workspace'u → w klonie pod innym katalogiem nie istnieje.
- 6 pozostałych wpisów bootstrap (CODEMAP, ARCHITECTURE, ZIOMEK_ARCHITECTURE,
  ZIOMEK_INVARIANTS, ZIOMEK_DEFINITION_OF_DONE, BACKLOG l. 166-170, 177) używa
  `../../../../../` względem repo i istnieje w klonie — **nie** są zależnościami hosta.
- l. 110 `"/root/\.openclaw/workspace/\.secrets/"` to regex w `FORBIDDEN_ACTIVE_PAYLOAD`
  (skan treści), NIE existence-check — nie liczy się do tych 11.

---

## 3. Proponowana zmiana — minimalna, addytywna (NIE zastosowana)

**Wariant bazowy (env-gated skip existence dla absolutnych, host-zewnętrznych celów):**
- Wprowadź env `HERMETIC_SKILL_CI` (domyślnie nieustawiony = zachowanie dzisiejsze).
- W pętli l. 827-831: gdy `HERMETIC_SKILL_CI == "1"` **oraz** `target.startswith("/")`,
  pomiń `require(path.resolve().exists(), ...)` (l. 831). Cele względne wobec repo
  (`../../../../../...`) sprawdzaj DALEJ bez zmian — są commitowane.
- **Zachowaj bez zmian** tekstową bramę tożsamości l. 825-826
  (`require(prelude == EXPECTED_PRELUDE, ...)`, `require(bootstrap == EXPECTED_BOOTSTRAP, ...)`)
  — kontrakt „które pliki operator ma przeczytać" zostaje egzekwowany; rozluźniamy
  wyłącznie fizyczny existence-check na hoście CI.

**Wariant bezpieczniejszy (opcjonalny, węższy):** zamiast „każdy absolutny", pomiń
tylko nazwany zbiór `EXTERNAL_HOST_TARGETS` = dokładnie 11 ścieżek z §2. Wtedy literówka
w innej absolutnej ścieżce nadal zawiedzie (brak cichego rozluźnienia poza znany zbiór).

Obie wersje są addytywne: bez env i na żywym hoście (11 plików obecnych) zachowanie
jest identyczne jak dziś. Nie ruszają `EXPECTED_*`, schematów, mutation matrix ani logiki blockerów.

---

## 4. Kryterium akceptacji

1. **Cel:** świeży klon repo `dispatch_v2` (samo drzewo repo), BEZ: `/root/AGENTS.md`,
   `/root/.codex/AGENTS.md`, żywej kopii `/root/.openclaw/.../CLAUDE.md`,
   `/root/.claude/projects/-root/memory/`, `/root/handover/`. Uruchom:
   `HERMETIC_SKILL_CI=1 python docs/codex-skills/evals/ziomek-change-gate/validate.py`
   → **exit 0**.
2. **Regresja zachowania (domyślnie strict):** ten sam klon BEZ env → **exit 1**,
   komunikat `navigation target does not exist: /root/AGENTS.md` (pierwszy brakujący).
3. **No-op na hoście:** na żywym serwerze (11 plików obecnych) → **exit 0** i z env, i bez env.
4. **Zakres nietknięty:** 6 celów względnych repo + `docs/decisions/` + pliki
   skilla/registry/schema/cases dalej sprawdzane i muszą istnieć w klonie (są commitowane);
   spec ich NIE rozluźnia.

⚠ Uczciwa granica: „exit 0 na czystym klonie" można **udowodnić tylko uruchomieniem**
w takim kontenerze. Statyczny odczyt potwierdza, że te 11 existence-checków to jedyna
brama zależna od hosta (reszta wejść kotwiczy do `ROOT`, l. 20-33) — ale nie wykonałem
biegu w czystym kontenerze. Akceptacja = powyższy bieg, do wykonania po zmianie.
