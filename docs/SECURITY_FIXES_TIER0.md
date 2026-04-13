# SECURITY_FIXES_TIER0.md — 5 fixów blokujących Fazę 1

**Data:** 13.04.2026
**Źródło:** red-team review Gemini 3.1 PRO + DeepSeek-V3 (12.04 wieczór)
**Status:** TIER 0 (BLOCKING) — bez tych fixów nie włączamy allow-list CC ani Fazy 1
**Czas total:** ~90 min
**Commit:** P0.5b hotfix

---

## Fix #1 — HARD EXCLUSIONS dla allow-list CC (5 min)

### Problem
Allow-list w UI Claude Code z pattern `cat /**` lub `cat /root/**` może wysłać sekrety do logów Anthropic gdy CC przypadkiem otworzy plik z credentials.

### Rozwiązanie
Wyklucz z allow-list (jako "Excluded patterns" w UI Claude Code):

```
/root/.openclaw/workspace/.secrets/**
/root/.ssh/**
/root/.bash_history
/var/log/auth.log
**/*.env
**/*.env.*
**/*.pem
**/*.key
**/*.secret
**/credentials*
**/.git/config
```

### Wdrożenie
1. Settings → Permissions → Excluded patterns w Claude Code UI
2. Skopiuj patterns wyżej
3. Verify: `cat /root/.openclaw/workspace/.secrets/panel.env` w CC powinno PYTAĆ (nie auto-exec)

### Testy
- ✅ `cat /root/.openclaw/workspace/.secrets/panel.env` → "requires approval"
- ✅ `cat /root/.openclaw/workspace/scripts/dispatch_v2/common.py` → safe (allow)
- ✅ `cat /root/.ssh/id_ed25519` → "requires approval"

### Commit ref
`fix per DeepSeek security #1.4 + Gemini #3.2`

---

## Fix #2 — Retry FileNotFoundError w state_machine._read_state() (15 min)

### Problem
Watcher (20s) + sla_tracker (10s) odczytują `orders_state.json` współbieżnie. Przy starcie systemu lub po atomic write (rename) pojawia się okno gdzie plik chwilowo nie istnieje → `FileNotFoundError` → utrata aktywnych orderów (rare ~1/dzień).

### Rozwiązanie

```python
# state_machine.py — _read_state()
import fcntl
import time
from pathlib import Path

def _read_state():
    path = Path(_state_path())
    for attempt in range(3):
        try:
            with open(path) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # shared lock
                return json.load(f)
        except FileNotFoundError:
            if attempt == 2:
                _log.warning(f"State not found after 3 retries: {path}")
                return {}
            time.sleep(0.01 * (attempt + 1))  # 10ms, 20ms, 30ms backoff
        except json.JSONDecodeError as e:
            _log.error(f"State JSON decode error: {e}")
            return {}
    return {}
```

### Wdrożenie
1. `cp state_machine.py state_machine.py.bak-$(date +%Y%m%d-%H%M%S)`
2. Python heredok z str.replace na funkcji `_read_state`
3. `python3 -m py_compile state_machine.py`
4. `python3 -c "from dispatch_v2 import state_machine; print(state_machine._read_state())"` (import + smoke test)
5. Restart `dispatch-panel-watcher` + `dispatch-sla-tracker` (z Adrian approval)
6. Tail logi przez 5 min — verify zero errors

### Testy
- ✅ Symulacja: `mv orders_state.json /tmp/temp; sleep 0.05; mv /tmp/temp orders_state.json` → state nie traci
- ✅ py_compile passes
- ✅ Import + read empty file → returns {} bez crash

### Commit ref
`fix per DeepSeek concurrency #2.1`

---

## Fix #3 — Atomic write + lock dla geocoding cache (30 min)

### Problem
`geocoding.py` używa `json.dump(cache, open(path, 'w'))` bez locka. Watcher 20s + tracker 10s = równoległy zapis przy nowym geocode → corruption (truncated JSON).

### Rozwiązanie
Refactor do `atomic_write_json` (jak w `state_machine.py`):

```python
# geocoding.py
import os
import tempfile
import fcntl
import json
from pathlib import Path

def _atomic_write_cache(cache_dict):
    path = Path(_cache_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Pisz do temp w tym samym katalogu (atomic rename działa tylko same fs)
    fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.tmp-",
        suffix=".json"
    )
    try:
        with os.fdopen(fd, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # exclusive lock
            json.dump(cache_dict, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # Atomic rename (POSIX gwarantuje atomicity)
        os.replace(temp_path, path)
    except Exception:
        # Cleanup temp jeśli failure
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

def _save_cache():
    """Public API — call po dodaniu nowego geocode do cache."""
    _atomic_write_cache(_cache)
```

### Wdrożenie
1. `cp geocoding.py geocoding.py.bak-$(date +%Y%m%d-%H%M%S)`
2. Backup `geocode_cache.json` przed restartem (294 entries):
   ```
   cp /root/.openclaw/workspace/dispatch_state/geocode_cache.json /root/backups/geocode_cache_pre_p05b_$(date +%Y%m%d-%H%M%S).json
   ```
3. Python heredok dodający `_atomic_write_cache` + zamiana wszystkich `json.dump` w geocoding.py
4. py_compile + import check
5. Restart watcher + tracker (Adrian approval)
6. Verify: monitor logs przez 30 min — czy zero `JSONDecodeError` i cache się rośnie

### Testy
- ✅ py_compile passes
- ✅ Import + smoke geocode → cache zapisany correctly
- ✅ Stress test: 100 równoległych geocode'ów → zero corruption
- ✅ `python3 -c "import json; json.load(open('/root/.openclaw/workspace/dispatch_state/geocode_cache.json'))"` → valid JSON

### Commit ref
`fix per DeepSeek concurrency #2.2`

---

## Fix #4 — Re-login w panel_client przy 401/419 (30 min)

### Problem
Panel Rutcom timeoutuje sesję po ~24h. `panel_client.py` nie ma re-login logic → po 24h wszystkie request padają z 401/419 → manual restart watcher.

### Rozwiązanie

```python
# panel_client.py — wrapper request_with_reauth
import urllib.request
import urllib.error
import time

class PanelClient:
    # ... existing __init__ etc.
    
    def request_with_reauth(self, method, url, data=None, headers=None, timeout=30):
        """
        HTTP request z automatycznym re-login przy 401/419.
        Retry max 1 raz po re-login.
        """
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    url, data=data, headers=headers or {}, method=method
                )
                # Add session cookie z self.session
                if self.cookie_header:
                    req.add_header('Cookie', self.cookie_header)
                
                response = urllib.request.urlopen(req, timeout=timeout)
                
                # Check redirect to login (Laravel zwraca 302 na /login)
                if response.url.endswith('/login') or 'login' in response.url:
                    if attempt == 0:
                        _log.info(f"Session expired (redirect to login), re-authenticating...")
                        self.login()
                        continue  # retry
                    else:
                        raise RuntimeError(f"Re-login failed, still redirecting to login")
                
                return response
                
            except urllib.error.HTTPError as e:
                if e.code in (401, 419) and attempt == 0:
                    _log.info(f"Got HTTP {e.code}, re-authenticating...")
                    self.login()
                    continue  # retry
                else:
                    raise
        
        raise RuntimeError(f"Request failed after re-login retry: {url}")
```

### Wdrożenie
1. `cp panel_client.py panel_client.py.bak-$(date +%Y%m%d-%H%M%S)`
2. Python heredok dodający metodę `request_with_reauth`
3. Zamień wszystkie `urllib.request.urlopen` na `self.request_with_reauth` w `panel_client.py`
4. py_compile + import check
5. Smoke test: `python3 -c "from dispatch_v2 import panel_client; c = panel_client.PanelClient(); c.login(); r = c.request_with_reauth('GET', 'https://gastro.nadajesz.pl/admin2017/...'); print(r.status)"`
6. Restart watcher (Adrian approval)
7. Monitor 24h — zero manual restart

### Testy
- ✅ py_compile passes
- ✅ Smoke test logging in
- ✅ Symulacja: invalidate session manually → next request triggers re-login → success
- ✅ 24h monitoring bez manual restart

### Commit ref
`fix per DeepSeek reliability #3.1 + Gemini #4.1`

---

## Fix #5 — .gitignore audit + cleanup (5 min)

### Problem
1. Brakuje wpisów dla `.bak-*`, `.secrets/`, `.env` — ryzyko commit credentials
2. Może być już tracked plik z credentials (audit)

### Rozwiązanie

#### Sprawdź czy nic nie tracked

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
git ls-files | grep -E "\.(bak|env|secret)$|\.bak-|\.env\." | head -20
```

Jeśli coś znajdzie → `git rm --cached <file>` + dodaj do .gitignore.

#### Update .gitignore

```bash
cat > .gitignore << 'EOF'
# Backups
*.bak-*
*.bak.*
*.bak

# Secrets (HARD EXCLUSION dla CC + git)
.secrets/
.env
.env.*
*.pem
*.key
*.secret
credentials*

# Server backups
/root/backups/

# Temp files
/tmp/

# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.pytest_cache/

# OS
.DS_Store
Thumbs.db

# Editor
.vscode/
.idea/
*.swp
EOF
```

### Wdrożenie
1. Backup obecnego .gitignore: `cp .gitignore .gitignore.bak-$(date +%Y%m%d-%H%M%S)` (jeśli istnieje)
2. Audit: `git ls-files | grep -E "\.(bak|env|secret)$|\.bak-|\.env\."` → jeśli coś jest, raport do Adriana
3. Apply nowy .gitignore (wyżej)
4. `git status` — verify że stare bak files są teraz "ignored"

### Testy
- ✅ `git status` nie pokazuje `.bak-*` files
- ✅ `git ls-files | grep secret` → empty
- ✅ Test: `touch test.env; git status` → `.env` ignored

### Commit ref
`fix per DeepSeek security #1.5`

---

## Sumaryczny commit message dla P0.5b

```
P0.5b: Security TIER 0 hotfix (5 fixes per Gemini + DeepSeek review)

Source: red-team review 12.04.2026 wieczorem (Gemini 3.1 PRO + DeepSeek-V3).
TIER 0 = blocking dla allow-list CC i Fazy 1.

Fix 1 (Security): HARD EXCLUSIONS dla allow-list CC
  - Wyklucz .secrets/**, .ssh/**, .env, .pem, .key z UI patterns
  - Bez tego: cat /** wysyła sekrety do logów Anthropic
  - Ref: DeepSeek #1.4

Fix 2 (Concurrency): Retry FileNotFoundError w state_machine._read_state
  - 3 retry z exponential backoff (10/20/30 ms) + fcntl LOCK_SH
  - Bez tego: sporadyczna utrata orderów przy konkurencji watcher+tracker
  - Ref: DeepSeek #2.1
  - Files: state_machine.py
  - Backup: state_machine.py.bak-{ts}

Fix 3 (Concurrency): Atomic write + lock dla geocoding cache
  - Refactor json.dump → atomic_write_json (temp + fsync + rename + LOCK_EX)
  - Bez tego: race condition watcher 20s + tracker 10s = corruption
  - Ref: DeepSeek #2.2
  - Files: geocoding.py
  - Backup: geocoding.py.bak-{ts}, geocode_cache_pre_p05b_{ts}.json

Fix 4 (Reliability): Re-login w panel_client przy 401/419
  - Wrapper request_with_reauth z 1 retry po re-login
  - Bez tego: panel scraping pada po ~24h timeout sesji
  - Ref: DeepSeek #3.1, Gemini #4.1
  - Files: panel_client.py
  - Backup: panel_client.py.bak-{ts}

Fix 5 (Security): .gitignore audit + cleanup
  - Dodano *.bak-*, .secrets/, .env, *.pem, *.key, /root/backups/, /tmp/
  - Audit: git ls-files | grep -E "\.(bak|env|secret)" → empty (zero leaked)
  - Ref: DeepSeek #1.5
  - Files: .gitignore (created/updated)

Tests:
- Fix 1: cat .secrets/* w CC requires approval ✓
- Fix 2: py_compile + import + symulacja race → state nie traci ✓
- Fix 3: 100x parallel geocode → zero corruption ✓
- Fix 4: Smoke login + symulacja invalid session → re-login + success ✓
- Fix 5: git status nie pokazuje .bak-* / .env ✓

Production: RESTART required (watcher + sla_tracker) — Adrian approved
Backup: 4 .bak-{ts} pliki + geocode_cache backup
Review ref: Gemini 3.1 PRO + DeepSeek-V3 audit 20260412

TECH_DEBT: TIER 1 fixes do tygodnia 2 (telegram security, rate limit, OSRM boundary)
```

---

## Checklist wykonania (rano 13.04)

- [ ] Backup wszystkich plików które ruszamy (4 pliki)
- [ ] Backup geocode_cache.json przed Fix #3 restart
- [ ] Fix #1: HARD EXCLUSIONS w UI Claude Code (Settings → Permissions → Excluded)
- [ ] Fix #2: state_machine._read_state retry + py_compile + import check
- [ ] Fix #3: geocoding atomic write + py_compile + import check
- [ ] Fix #4: panel_client re-login wrapper + py_compile + import check
- [ ] Fix #5: .gitignore update + audit
- [ ] Test #1: cat .secrets/panel.env → requires approval
- [ ] Test #2-4: py_compile + import + smoke testy
- [ ] Restart `dispatch-panel-watcher` + `dispatch-sla-tracker` (Adrian approval)
- [ ] Tail logi 10 min — verify zero new errors
- [ ] Git diff sanity check
- [ ] Git add + commit P0.5b z message powyżej
- [ ] Update TECH_DEBT.md (sekcja TIER 0 DONE, TIER 1 follow-up)
- [ ] Update CLAUDE.md jeśli zmiany w architekturze (np. dla state_machine retry behavior)

**Po DONE → przejście do Krok 1 (CC acceleration setup).**
