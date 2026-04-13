# CLAUDE_WORKFLOW.md V3.4 — Zasady pracy Claude Code w projekcie Ziomek

**Zmiana vs V3.3:** dodane HARD EXCLUSIONS dla allow-list, MAX 5-8 kroków per batch, sed-only-read, batch checkpoint co 5 kroków.

## ⚠️ HARD EXCLUSIONS dla allow-list (NIGDY nie czytaj/wklejaj)

**To są pliki/ścieżki które CC NIGDY nie powinien czytać, listować ani wklejać zawartości w odpowiedziach** — nawet jeśli są w "safe" katalogu lub pasują do allow-list pattern.

```
/root/.openclaw/workspace/.secrets/**     # panel.env, gmaps.env, traccar.env
/root/.ssh/**                              # private SSH keys
/root/.bash_history                        # historia komend (może mieć hasła)
/var/log/auth.log                          # SSH auth logs
**/*.env                                   # wszystkie .env (główne i kopie)
**/*.env.*                                 # .env.local, .env.production etc.
**/*.pem                                   # certyfikaty private
**/*.key                                   # klucze
**/*.secret                                # custom secret files
**/credentials*                            # AWS/GCP credentials
**/.git/config                             # może mieć remote URL z tokenem
```

**Procedura przy przypadkowym otwarciu:**
1. STOP natychmiast
2. NIE wklejaj zawartości w odpowiedzi
3. Powiedz "Próbowałem otworzyć [path] który jest na HARD EXCLUSION list. Skip."
4. Zaproponuj alternatywę (np. masking, sysadmin command zamiast file read)

**Powód:** allow-list `cat /**` w UI Claude Code może wysłać sekrety do logów Anthropic. Bez exclusions = security bug.

## Hard rules (NIE łam nigdy)

1. **Backup przed patchem produkcyjnym:** 
   ```
   cp file.py file.py.bak-$(date +%Y%m%d-%H%M%S)
   ```
   PRZED każdym str.replace na pliku w dispatch_v2/

2. **Walidacja 3-etapowa:**
   ```
   py_compile → import check → test/dry-run
   ```
   PRZED restart systemd

3. **Atomic writes dla produkcji:**
   ```
   temp file → fsync → rename
   ```
   NIGDY direct write do /root/.openclaw/workspace/dispatch_state/*.json

4. **Git flow:**
   - commit TAK, push NIGDY bez Adriana
   - commit msg: `[PATCH_ID]: [1-linia]\n\n- [punkty]\n\nFiles: [lista]\nTests: [wyniki]\nReview ref: [Gemini #X | DeepSeek #Y]`
   - bez commitu: TODO/DEBUG/hardcoded credentials

5. **Systemd restart:**
   - NIGDY bez explicit Adrian approval (TAK w chacie)
   - Zawsze py_compile + import check first
   - Po restart: systemctl is-active + tail logi (verify alive)

## Forbidden patterns

- `jq` — brak w systemie, użyj Python json
- `sed -i` bez backupu (sed do edycji = ryzyko, LLM bywają złe w liczeniu linii)
- **Sed do EDYCJI plików** — sed wyłącznie do odczytu (`sed -n '/start/,/end/p file`)
- Heredoki do produkcyjnych plików (tylko /tmp/ / docs/)
- `rm -rf` gdziekolwiek (tylko `rm -f` konkretnych plików)
- Multiple str.replace w jednej operacji (rozbij na osobne z weryfikacją)
- Chromedp / headless browsers (Python urllib zostaje)
- Modyfikacja .env files bez explicit Adrian approval
- pip install bez explicit approval (side effect na python env)
- Cat dużych plików (>100 linii) — używaj sed range albo head/tail
- **Batche dłuższe niż 8 kroków** — CC traci kontekst po 5-7 (DeepSeek finding)

## Safe autonomous (zero pytań)

- Unit tests w /tmp/ lub tests/
- Dokumentacja docstring
- Code style (black, isort) — tylko jeśli już są instalowane
- Read-only queries (z wyłączeniem HARD EXCLUSIONS): ls, cat, head, tail, grep, find, wc
- git log, git status, git diff, git show, git branch
- py_compile
- File analyses w /tmp/
- commits do git LOCAL (push NIE)
- tar backups do /root/backups/
- mkdir -p w /tmp/ lub /root/.openclaw/workspace/

## NEVER autonomous

- Restart systemd (tylko explicit Adrian approval)
- Write do dispatch_state/ directly (musi iść przez tool/script)
- Modyfikacja openclaw.json, *.env files
- DELETE operations poza /tmp/
- Install packages (pip/apt)
- git push
- Modyfikacja panelu Rutcoma bez Adrian approval
- Telegram messages (tylko Ziomek autonomous via kod, nie ad-hoc z CC)
- **Czytanie HARD EXCLUSIONS paths**

## Response style

- Polski, konkretnie, bez lania wody
- Weryfikuj kod PRZED patchem (grep/cat/sed range aktualnego stanu)
- 1-linia status po każdym kroku batcha
- Pełne outputy tylko na checkpointach (STOP / CRITICAL / completion)
- Przy failurach — STOP + raport, nie kontynuuj
- Przy ambiguity — pytaj Adriana, NIE zgaduj (koszt zgadnięcia: 10-30 min debug)

## Batch workflow template (zaktualizowany V3.4)

Gdy Adrian wysyła zadanie wieloetapowe:

```
BATCH PLAN: <co robimy>
Cel: <konkretny output>

Kroki A-X (allow-list ON):
A. <krok>
B. <krok>
...
X. <krok>

CHECKPOINT po E (jeśli więcej niż 5 kroków): raport mid-way, czekaj na "kontynuuj"
STOP po X. Raport: co zrobione/co do review/następny krok.
Nie wykonuj nic poza planem bez mojej akceptacji raportu.
```

**Reguły batcha:**
1. **MAX 5-8 kroków** per batch (DeepSeek: ponad 8 = CC traci kontekst)
2. Pokaż plan jako numbered list ZANIM zaczniesz wykonywać
3. Flag kroki CRITICAL (modify produkcji, restart, git commit)
4. Po długim batchu: przekrocznia MUSI być explicite STOP — CC pędzi dalej bez tego
5. 1-linia status per krok
6. Pełne outputy tylko przy STOP/completion
7. Jeśli krok failuje → STOP, raport, czekaj na instrukcje

## Session start ritual (wykonuj BEZ pytania, sed-only-read)

```bash
# Workflow rules
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/CLAUDE_WORKFLOW.md | head -50

# Stan Fazy (sed range, nie cat całego)
sed -n '/## Stan Fazy 0/,/## Analiza spadku/p' /root/.openclaw/workspace/scripts/dispatch_v2/CLAUDE.md

# Production health
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker
tail -3 /root/.openclaw/workspace/scripts/logs/watcher.log

# Git state
cd /root/.openclaw/workspace/scripts/dispatch_v2 && git log --oneline | head -5
```

Potem 1-linia potwierdzenie:
> "Przeczytałem CLAUDE_WORKFLOW.md (V3.4) + stan Fazy z CLAUDE.md. Services [active/failed]. Last commit [SHA] [msg]. HARD EXCLUSIONS noted. Gotów. Adrian, priorytet dnia?"

## Safe allow-list patterns (Adrian skonfiguruje w UI CC)

**Allow (z dokładnymi ścieżkami, NIE globalne *):**
- `ls /root/.openclaw/workspace/**`
- `cat /root/.openclaw/workspace/**` (z HARD EXCLUSIONS)
- `head /root/.openclaw/workspace/**`
- `tail /root/.openclaw/workspace/**`
- `wc /root/.openclaw/workspace/**`
- `grep /root/.openclaw/workspace/**`
- `find /root/.openclaw/workspace/**`
- `python3 -m py_compile /root/.openclaw/workspace/**`
- `git log *`
- `git status`
- `git diff *`
- `git show *`
- `git branch *`
- `mkdir -p /tmp/*`
- `cp -a * *.bak-*` (tylko backup pattern)
- `tar -czf /root/backups/*`
- `* --dry-run*`
- `* --help`

**NIE allow-list (zawsze pytaj):**
- `git add/commit/push/reset`
- `rm` / `mv` / `cp` (poza .bak pattern)
- `systemctl restart/stop/start`
- `apt install`
- `python3` bez `--dry-run`
- `*.env`, `*.pem`, `*.key`, `.secrets/**`, `.ssh/**`

## Templates do użycia

### Template 1: Patch produkcyjnego pliku Python

Gdy Adrian zleca zmianę w pliku w dispatch_v2/:

```
1. cat [PLIK] | grep -n "wzorzec do zmiany" (verify exists)
2. cp [PLIK] [PLIK].bak-$(date +%Y%m%d-%H%M%S)
3. Python heredok z dokładnym str.replace (JEDNA operacja, assert old in s)
4. python3 -m py_compile [PLIK]
5. python3 -c "from dispatch_v2 import [MODUŁ]" (import check)
6. Jeśli dispatch-* service używa tego pliku → proponuje Adrianowi restart
7. git diff [PLIK] — pokaż zmiany
8. git add + commit (jeśli Adrian approved, z review reference)
```

### Template 2: Nowy offline tool

Gdy Adrian zleca nowy tool w scripts/tools/:

```
1. Shebang #!/usr/bin/env python3
2. Docstring z Usage
3. argparse (--input, --output, --dry-run, required + help)
4. main() z exit codes 0/2/3
5. Atomic write dla output (shutil.copy2 backup + temp→fsync→rename)
6. py_compile + dry-run test + real run test + diff verify
7. POZA git repo dispatch_v2 (tools/ folder)
```

### Template 3: Git commit message

```
[PATCH_ID lub CATEGORY]: [1-linia cel]

[Kontekst/motivation jeśli nieoczywisty]

Files changed:
- [plik]: [co zmienione]

Tests:
- [test 1]: PASS/FAIL
- [test 2]: PASS/FAIL

Production: [NO RESTART | RESTART required] 
Backup: [bak-TS name | N/A]
Review ref: [Gemini #X | DeepSeek #Y | none]

[Opcjonalne: TECH_DEBT follow-ups]
```

### Template 4: Recon / read-only analiza

```
1. cat (tylko head/tail/sed range jeśli >100 linii)
2. grep dla pattern
3. python3 -c "import json; d=json.load(open(...)); print(...)"
4. Zero side effects, zero modyfikacji
5. Output: krótki raport z findings + rekomendacja
```

## Quick diagnostics commands (uruchom bez pytania)

```bash
# Status systemd
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker dispatch-shadow

# Git state
cd /root/.openclaw/workspace/scripts/dispatch_v2 && git log --oneline | head -5

# Last events
tail -3 /root/.openclaw/workspace/scripts/logs/watcher.log

# Disk space
df -h /root | tail -1

# Memory
free -h | grep Mem

# Backups age
ls -la /root/backups/ | tail -5
```

## Patrz: wersja master brief

Pełny kontekst projektu, struktura plików, decyzje D1-D19, reguły R1-R29, plan tygodni 1-4 — w `/root/.openclaw/workspace/scripts/dispatch_v2/CLAUDE.md` (V3.4).
