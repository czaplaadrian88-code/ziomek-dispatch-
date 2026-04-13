# CLAUDE_WORKFLOW.md — Zasady pracy Claude Code w projekcie Ziomek

## Hard rules (NIE łam nigdy)

1. **Backup przed patchem produkcyjnym:** 
   cp file.py file.py.bak-$(date +%Y%m%d-%H%M%S)
   PRZED każdym str.replace na pliku w dispatch_v2/

2. **Walidacja 3-etapowa:**
   py_compile → import check → test/dry-run
   PRZED restart systemd

3. **Atomic writes dla produkcji:**
   temp file → fsync → rename
   NIGDY direct write do /root/.openclaw/workspace/dispatch_state/*.json

4. **Git flow:**
   - commit TAK, push NIGDY bez Adriana
   - commit msg: "[PATCH_ID]: [1-linia]\n\n- [punkty]\n\nFiles: [lista]\nTests: [wyniki]"
   - bez commitu: TODO/DEBUG/hardcoded credentials

5. **Systemd restart:**
   - NIGDY bez explicit Adrian approval (TAK w chacie)
   - Zawsze py_compile + import check first
   - Po restart: systemctl is-active + tail logi (verify alive)

## Forbidden patterns

- `jq` — brak w systemie, użyj Python json
- `sed -i` bez backupu
- Heredoki do produkcyjnych plików (tylko /tmp/ / docs/)
- `rm -rf` gdziekolwiek (tylko `rm -f` konkretnych plików)
- Multiple str.replace w jednej operacji (rozbij na osobne z weryfikacją)
- Chromedp / headless browsers (Python urllib zostaje)
- Modyfikacja .env files bez explicit Adrian approval
- pip install bez explicit approval (side effect na python env)

## Safe autonomous (zero pytań)

- Unit tests w /tmp/ lub tests/
- Dokumentacja docstring
- Code style (black, isort) — tylko jeśli już są instalowane
- Read-only queries: ls, cat, head, tail, grep, find, wc
- git log, git status, git diff, git show
- py_compile
- File analyses w /tmp/
- commits do git LOCAL (push NIE)
- tar backups do /root/backups/
- mkdir -p

## NEVER autonomous

- Restart systemd (tylko explicit Adrian approval)
- Write do dispatch_state/ directly (musi iść przez tool/script)
- Modyfikacja openclaw.json, *.env files
- DELETE operations poza /tmp/
- Install packages (pip/apt)
- git push
- Modyfikacja panelu Rutcoma bez Adrian approval
- Telegram messages (tylko Ziomek autonomous via kod, nie ad-hoc z CC)

## Response style

- Polski, konkretnie, bez lania wody
- Weryfikuj kod PRZED patchem (grep/cat/view aktualnego stanu)
- 1-linia status po każdym kroku batcha
- Pełne outputy tylko na checkpointach (STOP / CRITICAL / completion)
- Przy failurach — STOP + raport, nie kontynuuj
- Przy ambiguity — pytaj Adriana, NIE zgaduj (koszt zgadnięcia: 10-30 min debug)

## Batch workflow template

Gdy Adrian wysyła zadanie wieloetapowe:

1. Pokaż plan jako numbered list (co zrobisz, w jakiej kolejności)
2. Flag kroki CRITICAL (modify produkcji, restart, git commit)
3. Czekaj na akceptację planu
4. Wykonuj sekwencyjnie, 1-linia status per krok
5. STOP przed krokiem CRITICAL, wait for approval
6. Pełne outputy tylko przy STOP/completion
7. Jeśli krok failuje → STOP, raport, czekaj na instrukcje

## Session start ritual (wykonuj BEZ pytania)

```bash
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/CLAUDE.md | head -80
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/CLAUDE_WORKFLOW.md | head -40
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker
tail -3 /root/.openclaw/workspace/scripts/logs/watcher.log
cd /root/.openclaw/workspace/scripts/dispatch_v2 && git log --oneline | head -5
```

Potem 1-linia potwierdzenie: "Przeczytałem CLAUDE.md + CLAUDE_WORKFLOW.md, services active, last commit [SHA] [msg]. Gotów. Adrian, priorytet dnia?"

## Safe allow-list patterns (Adrian skonfiguruje w CC na stałe)

- ls:*
- cat:*
- head:*
- tail:*
- wc:*
- grep:*
- find:*
- python3 -m py_compile:*
- git log:*
- git status
- git diff:*
- git show:*
- *--dry-run*
- mkdir -p:*
- touch /tmp/*
- rm -f /tmp/test_* 
- rm -f /tmp/p0*_*
- cp -a:*.bak-*
- tar -czf:*

## Templates do użycia

### Template 1: Patch produkcyjnego pliku Python

Gdy Adrian zleca zmianę w pliku w dispatch_v2/:

1. cat [PLIK] | grep -n "wzorzec do zmiany" (verify exists)
2. cp [PLIK] [PLIK].bak-$(date +%Y%m%d-%H%M%S)
3. Python heredok z dokładnym str.replace (JEDNA operacja, assert old in s)
4. python3 -m py_compile [PLIK]
5. python3 -c "from dispatch_v2 import [MODUŁ]" (import check)
6. Jeśli dispatch-* service używa tego pliku → proponuje Adrianowi restart
7. git diff [PLIK] — pokaż zmiany
8. git add + commit (jeśli Adrian approved)

### Template 2: Nowy offline tool

Gdy Adrian zleca nowy tool w scripts/tools/:

1. Shebang #!/usr/bin/env python3
2. Docstring z Usage
3. argparse (--input, --output, --dry-run, required + help)
4. main() z exit codes 0/2/3
5. Atomic write dla output (shutil.copy2 backup + temp→fsync→rename)
6. py_compile + dry-run test + real run test + diff verify
7. POZA git repo dispatch_v2 (tools/ folder)

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

[Opcjonalne: TECH_DEBT follow-ups]
```
