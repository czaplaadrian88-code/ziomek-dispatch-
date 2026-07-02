# L12 — PAS BEZPIECZEŃSTWA (AUDYT 2.0 Ziomka)

**Data:** 2026-07-02 · **Tryb:** READ-ONLY inwentarz (nie pentest, bez eksploatacji) · **Serwer:** Hetzner 178.104.104.138
**Zakres:** (a) sekrety, (b) auth konsoli gps.nadajesz.pl/admin, (c) apka kuriera, (d) sesje gastro, (e) ekspozycja portów.

> **Uwaga metodyczna:** żaden endpoint zmieniający stan NIE był wywoływany. W szczególności **nie** dotknąłem `/stop` / `/start` (kill-switch dispatchu) ani przełączników autonomii — ocena z kodu + bezpiecznych GET-ów. Wartości sekretów zamaskowane.

---

## ⚠ USTALENIE PRZEKROJOWE (amplifikator wszystkiego): BRAK FIREWALLA HOSTA

- `ufw status` = **inactive**.
- `iptables -L INPUT` = **policy ACCEPT**, jedyny skok to `ts-input`; a `ts-input` zawiera regułę **`ACCEPT 0.0.0.0/0 → 0.0.0.0/0`** (dowód: `iptables -L ts-input -n`). Czyli łańcuch Tailscale **nie filtruje** ruchu spoza tailnetu — przepuszcza wszystko.
- `nft list ruleset` → `table ip filter` INPUT `policy accept`; `DOCKER-USER` **pusty** (brak restrykcji na porty publikowane przez Dockera).
- **Wniosek:** na poziomie OS nic nie ogranicza ruchu przychodzącego. Każdy port nasłuchujący na `0.0.0.0` jest osiągalny z internetu, **o ile** nie ma firewalla u dostawcy (Hetzner Cloud Firewall) — czego z poziomu hosta nie widać i **trzeba zweryfikować w panelu Hetznera**.
- **Poszlaka, że ekspozycja jest realna:** `/root/dispatch_control.py:60` i `/root/gps_server.py` ładują certyfikaty `*/letsencrypt/live/178.104.104.138.nip.io/*` — Let's Encrypt (HTTP-01) i domena `nip.io` wymagają publicznej osiągalności. To znaczy, że 8443/8765 były (są) wystawione do internetu świadomie.

---

## (a) SEKRETY

### Katalog `.secrets/` — uprawnienia (`ls -la /root/.openclaw/workspace/.secrets/`)
Większość plików jest poprawnie `-rw-------` (600, root). **Wyjątki world/– group-readable (644):**

| Plik | Perms | Zawartość (klucze) | Ryzyko |
|---|---|---|---|
| `panel.env` | `-rw-r--r--` (1000) | `PANEL_LOGIN`, `PANEL_PASSWORD` | **Master-login do gastro nadajesz.pl** czytelny dla każdego konta lokalnego |
| `panel_courier.env` | `-rw-r--r--` (root) | `PANEL_COURIER_LOGIN/PASSWORD/ID` | login kurierski do gastro |
| `nadajesz_parcel_admin.env` | `-rw-r--r--` (root) | `PARCEL_ADMIN_LOGIN/PASSWORD/BASE` | admin bramki paczek |
| `gmaps.env` | `-rw-r--r--` (1000) | `GMAPS_KEY` | klucz Google Maps (rozliczany $) |
| `traccar.env` | `-rw-r--r--` (1000) | `TRACCAR_TOKEN` | token GPS |

Reszta (`assistant_llm.env` = klucz LLM, `telegram.env`, `papu_internal.env`, `assistant_db.env`, `panel_courier_*.env`, `panel_session_cache.json`) = **600 root — OK**.
`ordering_app/.env` = `-rw-r----- root:papu-svc` (640, grupa serwisu) — OK.
`.env` (workspace root) = 600 root — OK.

> **Kontekst wzmacniający:** `/root` ma perms **`drwxr-xr-x` (755)** — czyli katalog jest przechodni dla wszystkich. W połączeniu z plikami 644 w drzewie oznacza to, że dowolne konto lokalne (uid 1000, `papu-svc`, itd.) realnie czyta powyższe sekrety oraz logi. Na maszynie w praktyce jednouserowej (root + uid1000) impakt jest mniejszy, ale to złamanie least-privilege.

### Git
- **`.secrets/` NIE jest w `.gitignore`** (`.gitignore` = `.aider*`, `.env`, `nadajesz_clone/`, `scripts/gastro_edit.py`). Obecnie katalog jest **untracked** (`git status` → `?? .secrets/`, `git ls-files .secrets/` = 0), więc *dziś* nie ma sekretów w repo — ale jeden `git add -A` wrzuci **cały** katalog produkcyjnych sekretów do historii. **Trzeba dodać `.secrets/` do `.gitignore`.**
- `git ls-files | grep -i secret|env|password|token` = pusto — brak sekretów zacommitowanych. Dobre.
- `/root` nie jest repo git — `dispatch_control.py`/`gps_server.py` (z hardcoded tokenami, niżej) nie trafiają do gita. Dobre.

### Hardcoded sekrety w kodzie
- **`/root/dispatch_control.py:10`** — `BOT_TOKEN = "<10cyfr>:AA…"` (żywy token bota Telegram sterującego dispatchem) **zaszyty w źródle**; `ALLOWED_USERS = ["8765130486"]` (l.11). Plik `-rw-r--r--` (644).
- **`scripts/gastro_koordynator.py:34`** — drugi `BOT_TOKEN = '<10cyfr>:AA…'` + `CHAT_ID` (l.35), również hardcoded.
- Token z `dispatch_control.py` jest **zduplikowany do dokumentu** `scripts/dispatch_v2/ZIOMEK_MASTER_KB.md` (grep `8770101598:AA` → trafienie). KB jest untracked, ale to sekret w pliku .md (ryzyko przy kopiowaniu/synchro).

### Sekrety w logach (grep `password|Bearer|api_key|csrf|token=` w `scripts/logs/`, `dispatch_state/`)
- **`scripts/logs/courier_api.log` — REALNY WYCIEK:** dump ciała żądania na błędzie 422 zawiera **hasło admina w cleartext**: `body="FormData([('user','admin'),('password','<słowo>2026')])"`. To hasło pasuje do `COURIER_ADMIN_PASS` (patrz (c)). Log jest **644 (world-readable)**, katalog `scripts/logs` = 755, 16 MB.
- **`scripts/logs/telegram_approver.log`** — linie `… token=<VALUE>` (realne wartości tokenów logowane przy starcie).
- **`scripts/logs/{cod_weekly,daily_accounting,cod_panel_ingest}.log`** — `login OK csrf=<8znaków>…` — częściowe/krótkotrwałe tokeny CSRF gastro (niska waga, sesyjne).
- `tomtom_poc/measure_rw.log` „api_key" = tylko nazwa zmiennej w tracebacku (NIE wartość) — false positive.
- **`Bearer ` w logach = 0 trafień** — tokeny sesyjne kurierów NIE lądują w logach. Dobre.

---

## (b) AUTH KONSOLI `gps.nadajesz.pl/admin` (nadajesz_clone/panel/backend) — **MOCNA (bez krytycznych luk)**

- **Bind:** `nadajesz-panel.service:15` → `uvicorn … --host 127.0.0.1 --port 8000`. Backend **tylko localhost**, wystawiony wyłącznie przez nginx (TLS). Nie ma go na liście portów `0.0.0.0`. Dobre.
- **JWT:** `app/core/security.py:72,77` — HS256, `jwt.decode(..., algorithms=[settings.jwt_algorithm])` (jawna allowlista algorytmu → brak podatności `alg=none`). Sekret = `settings.jwt_secret`.
- **Boundary produkcyjny DZIAŁA:** `app/core/config.py:411` — jeśli `jwt_secret == "dev-secret-change-me"` i `environment ∉ {dev,test,local}` → `raise RuntimeError` (nie wystartuje). Runtime: `PANEL_ENVIRONMENT=staging` (w `.env` i `flags.systemd.env`), a `PANEL_JWT_SECRET` ma **64 znaki, ≠ default**. `PANEL_FERNET_KEY` ustawiony (l.418 wymusza go poza dev). Czyli sekret jest silny i wymuszony.
- **Endpoint autonomii** `POST /api/coordinator/auto-assign` (`app/api/coordinator.py:795`) oraz cała reszta koordynatora — gate `Depends(_OperatorOnly)` = `require_roles("ziomek_admin")` (`coordinator.py:53`). Tożsamość z JWT (`CurrentUser`, `deps.py:16`). Killswitch odwracalny, z audytem `actor=user.email` (l.805).
- **Login:** `app/api/auth.py:63` — `@limiter.limit(settings.login_rate_limit)` + **lockout per-konto** (429 przed bcryptem, l.66-70). Hasła bcrypt (`security.py:13`), timing-equalizer dla nieistniejących kont (l.28-35), wsparcie **2FA TOTP** (l.38-47).
- **CSRF:** panel to API na **Bearer JWT w nagłówku `Authorization`** (nie cookie), więc klasyczny CSRF nie dotyczy (token nie jest auto-wysyłany przez przeglądarkę cross-site).
- **Drobne:** `access_token_ttl_min = 720` (12 h) — dość długi TTL sesji operatora; rozważyć krótszy + refresh. Klucz Fernet do szyfrowania haseł klientów jest derywowany z `jwt_secret` tylko w trybie legacy (`crypto.py:22`) — tu wyłączony, bo `fernet_key` ustawiony (dobrze; inaczej byłby „pojedynczy punkt totalnej kompromitacji", jak głosi komentarz `config.py`).

**Werdykt (b): brak podatności. To najlepiej zabezpieczony komponent.**

---

## (c) APKA KURIERA (courier_api, port 8767 na `0.0.0.0` — PUBLICZNY)

### Logowanie PIN (`main.py:244` `/api/auth/select`)
- **PIN = 4 cyfry, plaintext.** Źródło: `dispatch_state/kurier_piny.json` = mapa `{"PIN": "imię"}` (`auth.py:31-41` `resolve_pin`: `piny.get(pin) → name`). Analiza pliku: **52 wpisy, wszystkie 4-cyfrowe numeryczne, 0 kolizji, 0 trywialnych (1234/0000/sekwencje)**. Plik `-rw-------` (600 root) — perms OK, ale **wartości są jawne i tylko 4-cyfrowe** (przestrzeń 10 000).
- **Rate-limit:** `count_recent_failed` (`auth.py:127`) + `RATE_LIMIT_MAX_FAILED=5` / `RATE_LIMIT_WINDOW_SECONDS=15min` (`config.py:28-29`) — **ale per `courier_id`, brak limitu per-IP/globalnego.** Lista kurierów (`GET /api/couriers`) jest **bez auth** → atakujący pozna 52 `courier_id` i rozłoży brute po kontach: 52×(5/15min)=~25 k prób/dobę > 10 k przestrzeni → statystycznie pierwsze konto pęka w <1 dnia. Realny, choć wolny brute-force.
- Audyt prób: `pin_attempts` trzyma `pin_hash` (sha256+sól) **oraz `pin_last2` w cleartext** (`auth.py:120,147`) — przy wycieku tej tabeli brute redukuje się do 100 kombinacji. Drobne.
- **Impakt przejęcia PIN:** podszycie się pod kuriera → wysyłanie fałszywego GPS, podgląd przydzielonych zleceń + danych klientów. 

### Token sesji — **MOCNY**
- `auth.py:154` `generate_token = secrets.token_urlsafe(32)` (256 bit). `verify_token` (l.193): sprawdza revoke/expiry, **auto-logout po bezczynności** `ACTIVITY_TIMEOUT_SECONDS=90min` (`config.py:25`), twardy expiry `SESSION_TTL=30 dni` (l.24). Nowy login rewokuje poprzednie sesje kuriera (`revoke_courier_sessions`, l.158). Bearer w nagłówku, walidacja formatu (l.245). To „UUID/90 min" z pamięci projektu — **zaimplementowane dobrze.**

### Panel admina fleety NA PUBLICZNYM 8767 (`routes/admin.py`) — **słaby punkt**
- `main.py:60` montuje `admin_router`; bind `main.py:709` → `host=config.HOST` = `0.0.0.0:8767`.
- Login `routes/admin.py:70`: `hmac.compare_digest` dla usera i hasła (stały czas — dobrze), sesja `secrets.token_urlsafe(32)` w pamięci, cookie `httponly=True, secure=True, samesite` (l.87-94 — dobre flagi). Dashboard `/` gate `_is_authed` (l.111-116) — brak dostępu bez sesji. Dobre.
- **ALE:** hasło `COURIER_ADMIN_PASS` (drop-in `courier-api.service.d/admin-cred.conf`) = **słaby wzór „słowo+2026"** i **wyciekło do `courier_api.log` (644)**. `ADMIN_USER="admin"`. **Brak rate-limitu/lockoutu/2FA na tym loginie** (żadnego `@limiter`). Panel udostępnia `GET /api/positions/stream` (SSE — **live GPS całej floty**) i `/api/couriers/{id}/trail` (historia lokalizacji). → publiczny panel śledzenia floty za słabym, jawnym w logu hasłem, brute-forcowalny.
  - *Uwaga:* domyślna wartość w kodzie to pusty string (`config.py:173`), a `hmac.compare_digest(password, "")` przepuściłby puste hasło — tu jednak `COURIER_ADMIN_PASS` **jest** ustawione w dropinie, więc bypassu pustym hasłem NIE ma. Gdyby kiedyś dropin zniknął → natychmiastowy bypass. Warto dodać twardy fail gdy hasło puste.

---

## (d) SESJE GASTRO (klient dispatchu → panel nadajesz.pl)

- Dispatch loguje się do **zewnętrznego** panelu gastro creds z `panel.env` i utrzymuje `http.cookiejar` + token CSRF (`panel_client.py:14,139-140`, `gastro_koordynator.py:51-55` czyta `var TOKEN = '…'`).
- **Cache sesji na dysku:** `dispatch_state/panel_session_cache.json` + legacy `.secrets/panel_session_cache.json`, klucze `{saved_at, csrf, cookies}` — **oba `-rw-------` (600 root), OK.** To żywe ciasteczka sesji gastro; wyciek = dostęp do panelu gastro bez hasła, więc perms 600 są właściwe.
- CSRF: to CSRF **wychodzący** (nasz klient wysyła `_token` przy POST do gastro) — mechanizm gastro, nie nasz. Nasza konsola (b) jest Bearer-only, więc po naszej stronie CSRF nie występuje.
- Drobne: częściowe tokeny CSRF trafiają do logów cod_* (patrz (a)); są krótkotrwałe.

---

## (e) EKSPOZYCJA PORTÓW (`ss -tlnp`)

**Na `0.0.0.0`/`[::]`/`*` (potencjalnie z internetu — brak firewalla hosta):**

| Port | Proces | Auth? | Ocena |
|---|---|---|---|
| **8765** | `/root/gps_server.py` | **`/stop`,`/start` BEZ AUTH** (`gps_server.py:73-78`); `/gps` PIN 4-cyfry w URL | **P0** — nieuwierzytelniony kill-switch dispatchu |
| **9222** | `openclaw-browser` (Docker) | **CDP bez auth z definicji** (`/json/version` odpowiada) | **P0** — pełna zdalna kontrola przeglądarki agenta |
| 8767 | `courier_api/main.py` | PIN 4-cyfry + panel admina słabe hasło | **P1** (patrz c) |
| 18789/18790 | `openclaw-gateway` (Docker) | `GET /` → **HTTP 200 bez auth** | **P1** — brama runtime agenta wystawiona |
| 8443 | `/root/dispatch_control.py` | Telegram webhook, sprawdza `ALLOWED_users`; token hardcoded | P2 |
| 8766 | `dispatch_v2.gps_server` | tylko `/gps` (PIN) + `/ping`; **brak `/stop`** | P2 (czystszy niż 8765; PIN 4-cyfry) |
| 5001 | `osrm-server` (Docker) | brak | P3 — silnik tras publicznie |
| 631 | `cupsd` | — | P3 — drukowanie, zbędne na serwerze |
| 3001 / 3987 | `next-server` (x2) | `GET /`→200 | P3 — zweryfikować co serwują i zbindować do localhost |
| 80 / 443 | nginx | — | OK (front) |
| 22 | sshd | — | OK (potwierdzić klucze-only) |

**Na `127.0.0.1` (dobrze — tylko lokalnie):** panel nadajesz `:8000`, papu-backend `:8080/:8081`, papu-postgres `:5433`, redis `:6379`, prometheus `:9090`, grafana `:3000`, studio-renderer `:8800`, glitchtip `:8090`, adb `:5037`, `:8888`. Postgres/redis/grafana/prometheus poprawnie zamknięte na loopback.

---

## TOP RYZYKA (severity)

**P0 — krytyczne (internet + duży impakt + łatwe):**
1. **Brak firewalla hosta** (ufw off; iptables/nft INPUT ACCEPT; `ts-input` ACCEPT 0.0.0.0/0; DOCKER-USER pusty). Amplifikuje wszystko poniżej. *Do weryfikacji: czy istnieje Hetzner Cloud Firewall — jeśli nie, wszystkie porty 0.0.0.0 są publiczne.*
2. **Nieuwierzytelniony kill-switch dispatchu** — `https://…:8765/stop` (`/root/gps_server.py:73`) zatrzymuje przypisywanie zleceń całego biznesu; `/start` cofa. Zero auth.
3. **Chrome DevTools (CDP) na `0.0.0.0:9222`** (`openclaw-browser`) — protokół bez uwierzytelnienia; kto dosięgnie port, steruje przeglądarką agenta (odczyt zalogowanych sesji/cookies, exfiltracja, pivot). `DOCKER-USER` pusty = brak filtra.

**P1 — wysokie:**
4. **Brama runtime agenta `openclaw-gateway` `0.0.0.0:18789-18790`** — `GET /` zwraca 200 bez auth; wymaga weryfikacji zakresu sterowania, ale ekspozycja pewna.
5. **Słabe + wyciekłe hasło admina fleety** (courier_api :8767, panel `routes/admin.py`): wzór „słowo+2026", jawne w `courier_api.log` (644), **bez rate-limitu/lockoutu/2FA**, publicznie; daje live-GPS i trailы floty. Rotować + naprawić logowanie + dodać limiter + twardy fail na puste hasło.
6. **Hardcoded żywe tokeny botów Telegram** w `/root/dispatch_control.py:10` i `gastro_koordynator.py:34` (pliki czytelne przez 755 `/root`), token zduplikowany w `ZIOMEK_MASTER_KB.md`. Rotować + przenieść do `.secrets` (600) + usunąć z KB.

**P2 — średnie:**
7. **World-readable sekrety (644):** `panel.env` (master gastro), `panel_courier.env`, `nadajesz_parcel_admin.env`, `gmaps.env`, `traccar.env` + `/root` = 755. Ustawić 600 i `/root` = 700.
8. **Słabe 4-cyfrowe PIN-y kurierów** (plaintext, brak limitu per-IP, publiczne endpointy GPS 8765/8766/8767, `GET /api/couriers` bez auth ułatwia enumerację) — brute-force wykonalny w dniach. Rozważyć 6 cyfr + limit per-IP + auth na liście kurierów.
9. **`.secrets/` poza `.gitignore`** — dziś untracked, ale jeden `git add -A` = wyciek wszystkich sekretów do repo.
10. **Sekrety w logach** (`courier_api.log` hasło, `telegram_approver.log` token=) w plikach 644 — wyłączyć dump ciała żądań, zawęzić perms logów, logrotate + czyszczenie.

**P3 — niskie/higiena:**
11. `cupsd:631` i `osrm:5001` publiczne — zbindować do loopback/tailscale lub wyłączyć CUPS.
12. `next-server` `*:3001`/`*:3987` — ustalić rolę i zbindować do 127.0.0.1.
13. Panel JWT TTL 12 h — rozważyć krótszy + refresh. `pin_last2` w audycie — zaakceptowany trade-off, do świadomej decyzji. PIN w query stringu `/gps?pin=` (8765) — ląduje w logach dostępu (legacy).

---

## POKRYCIE

- **(a) Sekrety:** ✅ perms całego `.secrets/` + `.env`/`ordering_app/.env`; ✅ `.gitignore` + `git ls-files`/`git status` (repo workspace); ✅ grep hardcoded tokenów (2 boty Telegram, +KB); ✅ skan logów `password|Bearer|api_key|csrf|token=` z próbkami zamaskowanymi (potwierdzony 1 realny wyciek hasła + tokeny).
- **(b) Konsola:** ✅ bind, JWT (algo/secret/boundary), env runtime (`PANEL_ENVIRONMENT`/`PANEL_JWT_SECRET`/`FERNET`), gate `/auto-assign` i całego routera, rate-limit+lockout+2FA logowania, model CSRF.
- **(c) Apka:** ✅ PIN (długość/kolizje/plaintext/rate-limit), token sesji (entropia/TTL/idle-logout/revoke), panel admina 8767 (hasło/limit/cookie/gate/capabilities).
- **(d) Gastro:** ✅ cookiejar+CSRF klienta, perms cache sesji (oba 600), rozróżnienie CSRF in/out.
- **(e) Porty:** ✅ pełny `ss -tlnp`, mapowanie PID→proces→kontener, firewall (ufw/iptables/nft/DOCKER-USER), probe auth bezpiecznymi GET-ami.

## JAWNE LUKI (czego NIE potwierdziłem — do domknięcia)

1. **Zewnętrzna osiągalność vs Hetzner Cloud Firewall** — z hosta niewidoczna. Trzeba sprawdzić panel Hetznera / test z zewnątrz (poza tą sesją, poza read-only). Cała ocena P0/P1 dla portów 0.0.0.0 zakłada brak filtra u dostawcy (poszlaki: nip.io + Let's Encrypt sugerują realną ekspozycję).
2. **Zakres sterowania `openclaw-gateway` (18789/18790)** — potwierdzono 200 bez auth, nie reverse-owano API (byłoby to już zachowanie ofensywne). Wymaga oceny właścicielskiej.
3. **Realny brute-force PIN/hasła admina** — NIE wykonany (świadomie, read-only). Wykonalność oszacowana z limitów w kodzie.
4. **Rola `next-server :3001/:3987`** i pełny audyt konfiguracji nginx (server_name/locations/nagłówki bezpieczeństwa, TLS ciphers) — poza zakresem tej rundy.
5. **Hardening SSH** (klucze-only? PermitRootLogin? fail2ban?), rotacja/retencja logów, uprawnienia dropinów systemd z sekretami — nie audytowane w tej rundzie.
6. **Nie weryfikowano czy tokeny Telegram/hasła już są aktywnie nadużywane** (brak analizy logów dostępu bota/nginx pod kątem IoC).

---

### 5 NAJWAŻNIEJSZYCH RYZYK (zwięźle)
1. **Brak firewalla hosta** — ufw off + iptables/nft ACCEPT + `ts-input` przepuszcza 0.0.0.0/0; wszystkie porty `0.0.0.0` potencjalnie publiczne (zweryfikuj Hetzner Cloud FW). *(P0)*
2. **`https://…:8765/stop` bez auth** — dowolny w internecie zatrzymuje dispatch całego biznesu. *(P0)*
3. **Chrome DevTools na `0.0.0.0:9222`** — pełne zdalne przejęcie przeglądarki agenta, bez auth z definicji. *(P0)*
4. **Słabe, wyciekłe do logu (644) hasło admina fleety na publicznym :8767**, bez rate-limitu/2FA → live-GPS i trailе kurierów. *(P1)*
5. **Hardcoded żywe tokeny botów Telegram** w `/root/dispatch_control.py` i `gastro_koordynator.py` (czytelne przez `/root` 755, kopia w KB). *(P1)*

*Szybkie domknięcia low-effort/high-value:* włączyć firewall/zbindować porty do loopback+tailscale (leczy #1-#3 i większość (e)), dodać auth do `/stop`, dodać `.secrets/` do `.gitignore`, `chmod 600` na 5 plikach + `chmod 700 /root`, wyłączyć dump ciała żądań w courier_api i zrotować wyciekłe/hardcoded sekrety.
