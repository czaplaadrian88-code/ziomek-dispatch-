# 🔴 SECURITY-P0 RUNBOOK (P1 dyrektywy 03.07) — gotowa checklista wykonania

**Źródło findingów:** AUDYT2 pas L12 (`eod_drafts/2026-07-02/AUDYT2/L12-bezpieczenstwo.md`) — wszystkie fakty **RE-ZWERYFIKOWANE na żywo 03.07 ~15:50 UTC+2** (ss/ufw/perms/grep/git log): stan NIEZMIENIONY od audytu.
**Zasada:** działania serwerowe = ZA ACK Adriana (per pozycja); działania konsolowe/BotFather = Adrian osobiście. Kolejność poniżej = kolejność wykonania (FW najpierw — leczy najwięcej naraz). Restart `dispatch-telegram`/botów NIGDY bez jawnego ACK.

---

## KROK 0 (Adrian, 10 min) — czy porty są REALNIE publiczne?

**✅ WYKONANE 05.07 ~19:25 UTC (tmux15, probe z 2 zewnętrznych węzłów check-host.net — zastępuje test z telefonu):**
**P0 POTWIERDZONE — brak filtra u dostawcy.** Z zewnątrz OPEN: 22, 443, **8766, 8767, 5001 (OSRM), 631 (CUPS), 3001, 18789 (gateway/next)**. Jedyny FILTERED: **9222** (CDP) — dowód, że host-iptables B1 z 04.07 DZIAŁA nawet z internetu, ale to jedyna zamknięta dziura. `ss` publicznych listenerów potwierdza. **8767 WOLNO objąć FW** — apka bije przez nginx 443 (9906 wywołań /api/* w access.log; 0 bezpośrednich połączeń spoza localhost na 8767), nie wprost w port. ⚠ 8766 (nowy gps_server) też publiczny — sprawdzić czy PWA/coś bije wprost przed DROP. **Sekcja A (Hetzner Cloud FW) PILNA — 5001/631/3001/18789 otwarte dla całego internetu bez powodu.**


Ocena P0 zakłada brak filtra u dostawcy. Z wnętrza VM tego nie widać.
- [ ] **Konsola Hetzner Cloud** → serwer 178.104.104.138 → zakładka **Firewalls**: czy jest przypięty firewall i jakie reguły?
- [ ] **Test z zewnątrz** (telefon na LTE / inna maszyna, NIE z serwera):
  `nc -vz -w3 178.104.104.138 9222` oraz `nc -vz -w3 178.104.104.138 8765`
  - Oba `refused/timeout` → jest filtr u dostawcy → P0 spada do P2 (hardening dalej wart zrobienia, bez pośpiechu).
  - Którykolwiek `succeeded` → **P0 potwierdzone, wykonać sekcję A natychmiast.**
  - Poszlaka, że ekspozycja realna: certy Let's Encrypt na `178.104.104.138.nip.io` (8443/8765) wymagały publicznej osiągalności.

## SEKCJA A (Adrian, konsola Hetzner, ~20 min) — Cloud Firewall = 1 ruch leczy #1/#2/#3

Utworzyć/przypiąć firewall z regułami INBOUND (reszta DROP):
| Port | Zostawić otwarty? | Powód |
|---|---|---|
| 22 (SSH) | TAK (opcjonalnie tylko z IP Adriana) | administracja |
| 80, 443 (nginx) | TAK | fronty (gps.nadajesz.pl, lokalka.pl, apka kuriera przez /api) |
| 8443 | TAK, najlepiej tylko z zakresów Telegrama: `149.154.160.0/20`, `91.108.4.0/22` | webhook bota kontrolnego |
| 8765, 8766, 8767 | **DO USTALENIA — patrz ⚠ niżej** | GPS legacy + courier-api |
| 9222, 5001, 631, 18789-90, 3001, 3987 | **NIE (zablokować)** | CDP/OSRM/CUPS/gateway — nic z zewnątrz ich nie potrzebuje |

⚠ **Przed zablokowaniem 8765/8766/8767 zweryfikować, czy apka kuriera bije w port bezpośrednio, czy przez nginx 443** (nginx routuje `/api/*`→:8767; ale LE-cert na nip.io:8765 sugeruje, że COŚ używało bezpośrednio). Weryfikacja serwerowa (CC za ACK): `grep -c ":876" /var/log/nginx/access.log` vs `tcpdump -c 20 -i any port 8767 and not src 127.0.0.1` w godzinach pracy floty — jeśli telefony biją wprost w 8767, port musi zostać otwarty do czasu przepięcia apki na 443.

## SEKCJA-B MOST host-iptables WYKONANY 05.07 ~19:45 UTC (tmux15, ACK Adriana - do czasu Sekcji A)

Zamkniete z internetu (weryfikacja z 2-3 zewn. wezlow check-host.net = blocked, lokalnie dalej 200):
| Port | Usluga | Lancuch (wg sciezki pakietu) |
|---|---|---|
| 631 | CUPS | INPUT (host) |
| 3001 | next-server/gateway UI | INPUT (host) |
| 18789 | openclaw-gateway | DOCKER-USER (docker same-port) |
| 5001 | OSRM | raw/PREROUTING (docker DNAT :5001->:5000 - DOCKER-USER/INPUT NIE lapia po DNAT; kluczowa lekcja) |
| 9222 | CDP | DOCKER-USER - B1 skonsolidowany (B1 z 04.07 nigdy nie mial @reboot) |

Nietkniete (potwierdzone OPEN z zewnatrz): 22 SSH, 443/80 nginx, 8767 courier-api (apka bije przez nginx /api -> 443, 9906 wywolan w access.log; 0 bezposrednich), 8766 (zostaje do weryfikacji PWA - patrz Sekcji A). Wszystkie reguly v4+v6, tylko -i eth0 (localhost/docker-internal nietkniete - OSRM lokalnie 200, dispatch-shadow 0 bledow OSRM).

Trwalosc: dispatch_state/host_fw_drop_reboot.sh (idempotentny, wzorzec per-port) + wpis crontab @reboot sleep 20. Rollback: iptables -D / ip6tables -D odpowiedniej reguly (lub -t raw -D PREROUTING ... 5001) + usuniecie wpisu @reboot. To MOST - Sekcja A (Hetzner Cloud FW u dostawcy) go zastepuje docelowo; po jej zalozeniu skrypt+@reboot mozna usunac.

## SEKCJA B (CC za ACK per pozycja, serwerowe, łącznie ~1-2 h)

**B1 — CDP :9222 poza internet (bez restartu przeglądarki agenta):**
```bash
iptables -I DOCKER-USER -p tcp --dport 9222 ! -s 127.0.0.1 -j DROP
# trwałość po reboot: netfilter-persistent save  (albo reguła w unit-file/skrypcie @reboot)
```
Docelowo (przy najbliższym świadomym restarcie kontenera): publish `127.0.0.1:9222:9222` w compose openclaw-browser. Rollback: `iptables -D DOCKER-USER ...`.
To samo dla OSRM: `--dport 5001` (konsumenci = lokalne serwisy; sprawdzić przed: `ss -tnp | grep :5001`).

**B2 — kill-switch `/stop` :8765 (P0):**
Stan faktyczny: :8765 = **legacy** `/root/gps_server.py` (PID 1010, @reboot — to jest duplikat GPS z P5!), obok żyje kanoniczny `dispatch-gps.service` (:8766, bez `/stop`).
- Krok minimalny (od ręki, za ACK): token-auth w `/root/gps_server.py` — `/stop`/`/start` wymaga `?auth=<sekret z .secrets>`; plus reguła FW z sekcji A.
- Krok właściwy (u źródła, = P5): **wygasić legacy ścieżkę 8765/@reboot w całości** po potwierdzeniu, że nic z niej nie czyta (`tail -f` log legacy przez 1 dzień + grep konsumentów `:8765` w kodzie). Nie robić obu na raz bez okna obserwacji.

**B3 — sekrety na dysku (15 min, niski risk):**
```bash
cd /root/.openclaw/workspace
chmod 600 .secrets/panel.env .secrets/panel_courier.env .secrets/nadajesz_parcel_admin.env .secrets/gmaps.env .secrets/traccar.env
printf '.secrets/\n' >> .gitignore   # dziś untracked, ale 1× git add -A = wyciek do GitHuba (remote: mailek.git)
```
⚠ **NIE robić ślepo `chmod 700 /root`** (rekomendacja audytu): workspace pod `/root/.openclaw` jest własnością uid 1000, a `ordering_app/.env` czyta grupa `papu-svc` — odcięcie traversalu wywali te serwisy. Jeśli domykać: `chmod 750 /root` + `setfacl -m g:papu-svc:x,u:1000:x /root` i test WSZYSTKICH serwisów non-root. Osobny mini-sprint, nie quick-win.

**B4 — courier-api :8767 (kod, mini-sprint protokołem #0):** 🟢 **LIVE 03.07 ~17:50** (restart za ACK, regresja 141 passed, backupy `.bak-pre-*-2026-07-03`):
- (a) ✅ dump ciała 422 zredagowany — `main.py:_redact_body` maskuje hasło/pin/token w logu I w odpowiedzi (zweryfikowane na żywo: `SEKRET_TEST_123` 0× w odpowiedzi i logu).
- (b) ✅ `courier_api.log` → `640` (inode-preserving); 1 historyczna linia z hasłem = unieszkodliwiona rotacją hasła w C2.
- (c) ✅ per-IP lockout logowania admina (5 prób/15 min → 429, in-memory).
- (d) ✅ twardy fail (503) gdy `COURIER_ADMIN_PASS` pusty — koniec bypassu pustym hasłem.
- (e) ⏸ auth na `GET /api/couriers` = **ODŁOŻONE (decyzja produktowa)** — apka woła ten endpoint PRZED logowaniem (ekran wyboru kuriera); naiwne auth urywa logowanie w apce. Alternatywy: rate-limit endpointu / minimalizacja danych / token-gate z osobnym flow. Osobny sprint z Adrianem.

**B5 — higiena:** `systemctl disable --now cups` (drukarka na serwerze produkcyjnym zbędna); ustalić rolę `next-server :3001/:3987` i zbindować do 127.0.0.1.

## SEKCJA C — ROTACJE (Adrian + CC za ACK; sekrety skompromitowane przez historię git/logi)

**C1 — tokeny botów Telegram (2 szt.) — priorytet, bo żyją w historii git wypchniętej na GitHub** (redakcja 03.07 objęła TYLKO treść plików .md — commit `0b01e46`; historia `85f3185`/`bb46da6`… trzyma pełne tokeny; wciąż też hardcoded w żywych `/root/dispatch_control.py:10` i `scripts/gastro_koordynator.py:34`).
> ✅ **CZĘŚĆ KODOWA GOTOWA (staged, 03.07):** `dispatch_state/staged_token_rotation/` (załatane kopie obu plików = env-load z `.secrets/telegram_bots.env`, ZERO tokenów w kopiach, py_compile OK, fail-loud gdy brak tokenu) + szablon `telegram_bots.env.template` + `dispatch_state/apply_token_rotation.sh` (backup→podmiana→sanity-load; NIE restartuje botów). Zbudowane bez wyświetlania tokenów, diff = tylko blok BOT_TOKEN. Klucze env: `DISPATCH_CONTROL_BOT_TOKEN`, `GASTRO_KOORDYNATOR_BOT_TOKEN` (jeden plik obsługuje oba — gastro_koordynator czyta ten sam bind-mount przez `/home/node/...`).

**✅ C1 WYKONANE 05.07 ~18:58 UTC (tmux15):** revoke @Nadajesz2Bot zrobiony przez Adriana (stary token getMe=401), nowy token w `.secrets/telegram_bots.env` (600; GASTRO_KOORDYNATOR_BOT_TOKEN tymczasowo = ten sam @Nadajesz2Bot — stary bot gastro_koordynatora nieznany/martwy; podmiana 1 linią env gdy Adrian wskaże inny). `apply_token_rotation.sh` przeszedł: backupy `.bak-pre-token-rotation-20260705T185843Z`, oba pliki na env-load, sanity OK, **0 hardcoded tokenów w żywych plikach** (grep). Restart pkt 6 ŚWIADOMIE pominięty dla dispatch_control (proces WYGASZONY 05.07 za ACK — nie wskrzeszamy); gastro_koordynator weźmie env przy następnym wywołaniu. Historia gita ze STARYM tokenem = nieszkodliwa (revoked) → bez rewrite. ✅ **PRIVATE na obu repach POTWIERDZONE przez Adriana 05.07 ~19:20 UTC** — C1 domknięte w 100% (martwy token w historii gita = bez znaczenia, zero rewrite).

**⭐ KOREKTA 05.07 (tmux15, weryfikacja getMe):** (a) prawdziwy username control-bota = **@Nadajesz2Bot** (display „GastroBot", id=8770101598) — NIE „NadajeszControlBot" (nazwa umowna z docs; Adrian go nie znalazł w /mybots). (b) **token gastro_koordynatora JUŻ MARTWY (getMe 401)** → C1 = 1 żywy token, nie 2; UWAGA side-finding: sendMessage w gastro_koordynator.py:166 tym samym nie działa — jeśli skrypt wciąż używany, powiadomienia cicho padają. (c) Ryzyko rezydualne żywego tokena NISKIE: @Nadajesz2Bot NIE jest w grupie Ziomka (getChat 400), webhook pusty, proces wygaszony 05.07. (d) Jeśli @Nadajesz2Bot nie ma w /mybots Adriana → bot założony z INNEGO konta (Bartek? stary numer?) — revoke wymaga właściciela.

Sekwencja wykonania:
1. **Adrian** @BotFather → `/mybots` → **@Nadajesz2Bot („GastroBot")** → **Revoke current token** → nowy. To samo dla bota z `gastro_koordynator.py`. (Revoke = moment zamknięcia wycieku.)
2. **Adrian:** `cp dispatch_state/staged_token_rotation/telegram_bots.env.template /root/.openclaw/workspace/.secrets/telegram_bots.env` → wpisz oba nowe tokeny → `chmod 600`.
3. **Adrian:** `! bash /root/.openclaw/workspace/dispatch_state/apply_token_rotation.sh` (pre-flight sprawdza że nie ma placeholdera, backup `.bak-pre-token-rotation-*`, podmiana, sanity import-test).
4. **Restart za ACK** (bot = kanał Telegram): `dispatch_control` = kill PID + `nohup python3 … &` (komendy wypisuje apply-skrypt pkt 6); `gastro_koordynator` bierze nowy kod przy następnym wywołaniu (brak trwałego daemona).
5. Stary token po revoke = martwy ⇒ **historia git przestaje być groźna → NIE przepisujemy historii** (rotacja > rewrite; C1: nie ruszamy cudzych commitów). Sprawdź, czy repo `ziomek-dispatch-` i `mailek` są PRIVATE (Adrian, Settings → Danger Zone).
6. Weryfikacja: `curl -s https://api.telegram.org/bot<STARY>/getMe` → 401; bot odpowiada na `/status` (nowy token żywy).

**C2 — hasło fleet-admina (:8767): ✅ WYKONANE 05.07 ~19:03 UTC (tmux15, ACK off-peak).** Nowe silne hasło (26 alnum, generator `secrets`) w drop-inie `admin-cred.conf` + **kopia dla Adriana: `.secrets/courier_admin_fleet.txt` (600)** — odczyt: `! cat /root/.openclaw/workspace/.secrets/courier_admin_fleet.txt`. Restart courier-api 19:02 czysty (0 błędów, /arrival sanity 401, sesje kurierów przeżyły — sqlite; cid=492 aktywny 19:03). Dowód rotacji: stare hasło login→401, nowe→200. **B4b też domknięte:** wyciekła linia 422 z hasłem zredagowana z `courier_api.log` (0 wystąpień w logs/, backup z wyciekiem skasowany po weryfikacji). Konsumenci zmapowani przed rotacją: tylko courier-api drop-in (panelsync ADMIN_* = martwe legacy w config, unit bez credów). ⚠ Rezyduum niskie: stare (MARTWE) hasło wciąż w treści docs/memory (np. CLAUDE.md „admin/nadajesz2026") — nieszkodliwe po rotacji, sprzątanie przy okazji. Oryginalny opis:  nowe silne hasło (nie wzór „słowo+rok") do drop-inu `courier-api.service.d/admin-cred.conf` → `systemctl daemon-reload && systemctl restart courier-api` (za ACK, poza peakiem) → wyczyścić wyciekłą linię z `courier_api.log` (B4b).

**C3 — ✅ WYKONANE 05.07 ~19:15 UTC** (restrykcja wklikana przez Adriana [IP v4+v6 + API-lista], weryfikacja CC z serwera: **Geocoding=OK, DistanceMatrix=OK** po propagacji — nic nie pękło; probe negatywny Places=REQUEST_DENIED [komunikat wskazuje api-not-enabled w projekcie, nie kluczową restrykcję — bite restrykcji IP nieweryfikowalny z serwera, przyjęty na podstawie konfiguracji w konsoli]. Klucz Papu ...OKiGmE = osobny, do tej samej procedury przy pracach nad Lokalką.) Wartości użyte: dokładne wartości do kliknięcia w Google Cloud Console** (programistycznie NIE DA SIĘ: API Keys API 403 dla SA sheets — konsola = Adrian). Klucz Ziomka (`.secrets/gmaps.env`, końcówka **...rbNszw**; klucz Papu ...OKiGmE = OSOBNY, ta sama procedura osobno): Console → APIs & Services → Credentials → klucz ...rbNszw → (1) **Application restrictions = IP addresses**: **178.104.104.138** ORAZ **2a01:4f8:1c19:a25d::/64** (⚠ serwer MA publiczne IPv6 — zweryfikowane icanhazip; restrykcja tylko-IPv4 = ryzyko odcięcia wywołań idących po v6, ta sama klasa miny co IPv6 przy CDP B1); (2) **API restrictions = Restrict key**: TYLKO `Distance Matrix API` + `Geocoding API` (pełna mapa użyć: distancematrix ×3 w gastro_koordynator/gastro_scoring, geocode w dispatch geocoding.py + gastro_koordynator:394). Po zapisaniu (propagacja do ~5 min) → CC weryfikuje z serwera żywym geocode-testem. Oryginalny opis:  `GMAPS_KEY` — restrykcja klucza w Google Console (IP serwera) zamiast rotacji; `panel.env` (master gastro) — rotacja przy okazji, bo wyciek tylko lokalny (644), nie publiczny; hasła floty/PIN-y 4-cyfrowe → osobny temat B4e + ewent. 6 cyfr.

## KOLEJNOŚĆ SUGEROWANA (dźwignia/koszt)
1. KROK 0 (Adrian) → 2. SEKCJA A FW (Adrian) → 3. C1 rotacja tokenów (Adrian+ACK) → 4. B1+B3 (CC za ACK, 20 min) → 5. C2 (ACK, off-peak) → 6. B2/B5 → 7. B4 (mini-sprint) → 8. B3-bis `/root` perms (osobno, z analizą).

**Po całości:** re-run weryfikacji z KROKU 0 + `ss -tlnp` + wpis do trackera `ZIOMEK_STAN_AUDYTY_1i2.md` (finding „Security P0" → status) + odhaczyć P1 w `memory/priorytety-stabilnosc-jakosc-skala-2026-07-03.md`.
