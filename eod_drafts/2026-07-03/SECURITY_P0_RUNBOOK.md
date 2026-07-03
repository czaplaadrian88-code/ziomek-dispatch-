# 🔴 SECURITY-P0 RUNBOOK (P1 dyrektywy 03.07) — gotowa checklista wykonania

**Źródło findingów:** AUDYT2 pas L12 (`eod_drafts/2026-07-02/AUDYT2/L12-bezpieczenstwo.md`) — wszystkie fakty **RE-ZWERYFIKOWANE na żywo 03.07 ~15:50 UTC+2** (ss/ufw/perms/grep/git log): stan NIEZMIENIONY od audytu.
**Zasada:** działania serwerowe = ZA ACK Adriana (per pozycja); działania konsolowe/BotFather = Adrian osobiście. Kolejność poniżej = kolejność wykonania (FW najpierw — leczy najwięcej naraz). Restart `dispatch-telegram`/botów NIGDY bez jawnego ACK.

---

## KROK 0 (Adrian, 10 min) — czy porty są REALNIE publiczne?

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

**B4 — courier-api :8767 (kod, mini-sprint protokołem #0):**
(a) wyłączyć dump ciała żądań przy 422 (wyciek hasła do loga); (b) `chmod 640 scripts/logs/courier_api.log` + wyczyścić linię z hasłem (backup przed edycją loga); (c) rate-limit/lockout na login admina; (d) twardy fail przy pustym `COURIER_ADMIN_PASS`; (e) auth na `GET /api/couriers` (enumeracja do brute PIN).

**B5 — higiena:** `systemctl disable --now cups` (drukarka na serwerze produkcyjnym zbędna); ustalić rolę `next-server :3001/:3987` i zbindować do 127.0.0.1.

## SEKCJA C — ROTACJE (Adrian + CC za ACK; sekrety skompromitowane przez historię git/logi)

**C1 — tokeny botów Telegram (2 szt.) — priorytet, bo żyją w historii git wypchniętej na GitHub** (redakcja 03.07 objęła TYLKO treść plików .md — commit `0b01e46`; historia `85f3185`/`bb46da6`… trzyma pełne tokeny; wciąż też hardcoded w żywych `/root/dispatch_control.py:10` i `scripts/gastro_koordynator.py:34`):
1. Adrian: @BotFather → `/mybots` → bot kontrolny (NadajeszControlBot) → **Revoke current token** → nowy token. To samo dla bota z `gastro_koordynator.py`.
2. CC za ACK: nowe tokeny do `.secrets/telegram_control.env` (600, wzór `telegram.env`); w obu plikach `BOT_TOKEN = os.environ[...]` / load z env-file zamiast literału.
3. Restart dotkniętych procesów — ⚠ `dispatch_control.py` to legacy PID 1006 (@reboot) — restart = kill+ręczny start albo domknięcie razem z B2/P5. **Bot = kanał Telegram → jawny ACK przed dotknięciem.**
4. Stary token po revoke = martwy ⇒ **historia git przestaje być groźna → NIE przepisujemy historii na GitHubie** (rotacja > rewrite; C1: nie ruszamy cudzych commitów). Sprawdzić czy repo `ziomek-dispatch-` i `mailek` są PRIVATE (Adrian, 1 min, Settings → Danger Zone).
5. Weryfikacja: `curl -s https://api.telegram.org/bot<STARY>/getMe` → 401; bot działa na nowym (testowy `/status`).

**C2 — hasło fleet-admina (:8767):** nowe silne hasło (nie wzór „słowo+rok") do drop-inu `courier-api.service.d/admin-cred.conf` → `systemctl daemon-reload && systemctl restart courier-api` (za ACK, poza peakiem) → wyczyścić wyciekłą linię z `courier_api.log` (B4b).

**C3 — niższy priorytet (po C1/C2):** `GMAPS_KEY` — restrykcja klucza w Google Console (IP serwera) zamiast rotacji; `panel.env` (master gastro) — rotacja przy okazji, bo wyciek tylko lokalny (644), nie publiczny; hasła floty/PIN-y 4-cyfrowe → osobny temat B4e + ewent. 6 cyfr.

## KOLEJNOŚĆ SUGEROWANA (dźwignia/koszt)
1. KROK 0 (Adrian) → 2. SEKCJA A FW (Adrian) → 3. C1 rotacja tokenów (Adrian+ACK) → 4. B1+B3 (CC za ACK, 20 min) → 5. C2 (ACK, off-peak) → 6. B2/B5 → 7. B4 (mini-sprint) → 8. B3-bis `/root` perms (osobno, z analizą).

**Po całości:** re-run weryfikacji z KROKU 0 + `ss -tlnp` + wpis do trackera `ZIOMEK_STAN_AUDYTY_1i2.md` (finding „Security P0" → status) + odhaczyć P1 w `memory/priorytety-stabilnosc-jakosc-skala-2026-07-03.md`.
