# A360-SEC0 — host boundary i rotacja poświadczenia

Status tego dokumentu: **SOURCE/PREP — NOT APPLIED**. To plan dla osobnego
FLIPMASTER-a. Sam dokument ani narzędzie audytowe nie zmieniają bindów, reguł,
routingu, kontenerów, unitów ani poświadczeń.

## 1. Twarde bramki GO/NO-GO

Przed operacją live muszą istnieć wszystkie poniższe zgody i dowody:

1. Jawny ACK Adriana na okno maintenance, zmianę provider firewall, reguł hosta,
   bindów, rotację poświadczenia, daemon-reload, pojedynczy restart API oraz
   kontrolowane odtworzenie kontenera.
2. Druga, działająca sesja administracyjna, utrzymana przez inną osobę przez
   całe okno. Nie zamykać pierwszej sesji przed końcem smoke.
3. Świeży eksport lub zrzut reguł providera z SHA-256 i czasem UTC. Bez niego
   provider ma status `UNKNOWN` i operacja jest HOLD.
4. Zatwierdzony test z niezależnej sieci dla allowed/denied, osobno IPv4 i IPv6.
   Ten runbook nie jest zgodą na wykonanie takiego testu.
5. Ustalony owner źródłowego manifestu kontenera. Sama nazwa kontenera i
   bieżący runtime nie są wystarczającym źródłem do recreate.
6. Lista wszystkich zatwierdzonych konsumentów pola `COURIER_ADMIN_PASS`, bez
   wartości, oraz owner odpowiedzialny za przełączenie każdego konsumenta.
7. Okno poza peakiem i brak równoległego deployu plików/usług objętych zmianą.

Jeśli choć jedna bramka nie jest spełniona: zakończyć na SOURCE/PREP, bez zmian
live.

## 2. Preimage i ownership gate

FLIPMASTER zaczyna od porównania dokładnie tych metadanych. Hash mismatch jest
STOP-em do ponownego audytu, nie powodem do nadpisania pliku.

| obiekt | rola | oczekiwany preimage SHA-256 / revision | owner i mode | stan źródła |
|---|---|---|---|---|
| `/root/.openclaw/workspace/scripts/courier_api/config.py` | pola `HOST`, `PORT`, `ADMIN_PASS`, `COURIER_ADMIN_PASS` | `16e0c8d6cbf05f8a0a618f994c05d8b2ae5a494b9548c5c3255ade0ca7c9d887`; repo `fa249e678aa3e15641e6440b10a972df830010f5` | `0:0`, `0644` | tracked, czysty repo |
| `/root/.openclaw/workspace/scripts/courier_api/main.py` | konsument bindu | `9bfe43878634414c9932e34b7a9afb1b2d1ade24f745b9be62263f542d392e16` | `0:0`, `0644` | tracked, czysty repo |
| `courier-api.service` live unit | uruchomienie API | hash niedopuszczony dla live fragmentu; existence/owner/mode/size/mtime only | `0:0`, `0644`, 553 B, mtime 2026-04-16T16:17:02Z | źródłowy generator/unit repo nieudowodniony |
| carrier administracyjny API | bieżący nośnik poświadczenia | hash niedopuszczony; existence/owner/mode/size/mtime only | `0:0`, `0644`, 221 B, mtime 2026-07-05T19:02:05Z | zawartość raz odczytana przez niedopuszczalny hash, lecz niewyświetlona i niezinterpretowana; hasha nie ponawiać; nośnik do wycofania |
| `openclaw-browser` | owner runtime portu 9222 | brak source hash | runtime Docker; manifest owner `UNKNOWN` | blocker recreate |

Oczekiwany owner/mode po patchu kodu API pozostaje `0:0`/`0644`. Dokładny
postimage SHA kodu musi wynikać z zatwierdzonego commita w repo `courier_api` i
zostać dopisany przed live. Nie wolno wymyślać post-hasha bez gotowego diffu.
Nowy nośnik poświadczenia ma być root-only (`0:0`, `0600`) i mieć nową rewizję.

Minimalne polecenia preflight (odczytowe):

```bash
test "$(git -C /root/.openclaw/workspace/scripts/courier_api rev-parse HEAD)" = "fa249e678aa3e15641e6440b10a972df830010f5"
test "$(sha256sum /root/.openclaw/workspace/scripts/courier_api/config.py | awk '{print $1}')" = "16e0c8d6cbf05f8a0a618f994c05d8b2ae5a494b9548c5c3255ade0ca7c9d887"
test "$(sha256sum /root/.openclaw/workspace/scripts/courier_api/main.py | awk '{print $1}')" = "9bfe43878634414c9932e34b7a9afb1b2d1ade24f745b9be62263f542d392e16"
git -C /root/.openclaw/workspace/scripts/courier_api status --porcelain
/root/.openclaw/venvs/dispatch/bin/python /root/a360_sec0_wt/dispatch_v2/tools/host_boundary_audit.py --live
```

Ostatnie polecenie ma przed live zwrócić `HOLD`/exit 2. Nie dodawać `|| true` —
operator ma świadomie zobaczyć i zatwierdzić wszystkie findings.

## 3. Dokładny plan patcha cross-repo

### 3.1 `courier_api` — bind i źródło poświadczenia

Patch należy wykonać w osobnym branchu repo `courier_api`, z powyższego HEAD:

1. Pole `HOST: str` ma domyślnie wybierać loopback IPv4. Wartość wildcard IPv4,
   wildcard IPv6 i dowolny non-loopback mają być odrzucane przy starcie, chyba
   że istnieje osobna, jawnie zatwierdzona architektura sieciowa. Nie dodawać
   cichego fallbacku do publicznego bindu.
2. Pole `PORT: int` pozostaje kompatybilne z aktualnym kontraktem; zmiana portu
   nie jest częścią tego sprintu.
3. `ADMIN_PASS: str` ma być ładowane przez małą funkcję czytającą wyłącznie nową
   rewizję z katalogu przekazanego przez systemd `CREDENTIALS_DIRECTORY`.
   Brak, pusty plik, zły owner/mode lub błąd odczytu mają zatrzymać start API.
   Nie zostawiać fallbacku do wartości procesu.
4. Wyjątki, repr, health i logi nie mogą zawierać odczytanej wartości.
5. Dodać testy: loopback default; wildcard v4/v6 odrzucony; brak rewizji fail
   closed; zły mode/owner fail closed; pusta rewizja fail closed; poprawna
   rewizja pozwala wystartować; złośliwa wartość nie trafia do outputu; nowe
   poświadczenie działa, poprzednie jest odrzucane.
6. Po zielonych testach zapisać commit, postimage SHA-256, owner i mode. Dopiero
   ten commit może zostać wskazany w live change record.

Kanon aplikacji mobilnej został potwierdzony bez pełnego odczytu configu w
`/root/courier-app`: branch `fix-tomtom-hero-crash-vc72`, HEAD
`740d2dd8a68d4199c8bef793711b4e697802fa36`, tracked manifest
`app/build.gradle.kts` owner `0:0`, mode `0644`, SHA-256
`60cae49d375965e200e20d68d4bcf1b63beca0e01f910c69e365b3487153c846`.
Zastany untracked katalog `docs/` należy do ownera repo i nie wolno go dotykać.

Unit systemd ma dostać `LoadCredential` wskazujące nową, root-only rewizję.
Usunięcie starego carriera następuje dopiero po udanym health nowej rewizji,
lecz jego treści nie wolno kopiować do backupu ani logu.

### 3.2 `openclaw-browser` — source przed recreate

Runtime potwierdza nazwę ownera i publiczny publish obu rodzin IP, lecz nie ma
compose labels ani udowodnionego manifestu. Bieżący tag obrazu jest mutowalny.
Dlatego nie wolno rekonstruować polecenia `docker run` z pamięci ani z ogólnego
dumpu configu.

Owner ma dostarczyć wersjonowany manifest wraz z immutable image digest,
wolumenami, siecią, capabilities, healthcheckiem i restart policy. Patch tego
manifestu ma:

1. przypiąć port 9222 wyłącznie do loopback IPv4;
2. dodać loopback IPv6 tylko gdy zatwierdzony konsument rzeczywiście go wymaga;
3. nie publikować wildcard v4/v6;
4. zachować pozostałe kontrakty z manifestu ownera;
5. mieć source commit i postimage hash przed recreate.

Do czasu spełnienia tej bramki jedyną bezpieczną ścieżką administracyjną jest
zatwierdzony tunel przez drugą sesję, a ochroną tymczasową — provider oraz
hostowe deny dla obu rodzin IP.

## 4. Kolejność przyszłej operacji live

### Krok A — backup, bez starej wartości

1. Utworzyć root-only katalog change record z czasem UTC.
2. Zapisać `iptables-save`, `ip6tables-save`, eksport reguł providera, hashe
   źródeł, `docker ps` w bezpiecznym formacie i allowlistę properties unitów.
3. Skopiować zatwierdzony kod i manifesty. Dla carriera zapisać tylko existence/owner/mode/size/mtime — nigdy treść ani hash.
4. Sprawdzić, że restore reguł jest syntaktycznie poprawny na kopii/offline.

### Krok B — add safe path

1. Potwierdzić działanie drugiej sesji administracyjnej.
2. Przygotować tunel do loopback 9222 i sprawdzić lokalny CDP status bez body.
3. Przygotować nową root-only rewizję poświadczenia bez stdout, argumentu
   procesu ani wartości procesu. Nie aktywować jej jeszcze.
4. Dodać najpierw jawny allow dla zatwierdzonego kanału administracyjnego w
   provider firewall i host firewall; nie zmieniać domyślnej polityki INPUT.

### Krok C — test safe path

1. Drugi administrator potwierdza, że sesja i tunel pozostają działające.
2. Na izolowanej instancji uruchomić patched API i testy nowej rewizji.
3. Potwierdzić kanoniczną ścieżkę aplikacji przez HTTPS i brak zależności od
   bezpośredniego 8767.
4. Zapisać start/end UTC oraz hash wyników. Każdy fail oznacza STOP przed
   restrict.

### Krok D — restrict i aktywacja

1. Provider: zablokować bezpośredni ruch do 8767 i 9222, osobno IPv4/IPv6,
   pozostawiając tylko zatwierdzony kanał zarządczy.
2. Host: dodać idempotentne, wersjonowane deny INPUT dla 8767/9222 i
   DOCKER-USER dla 9222, osobno IPv4/IPv6. Reguły muszą mieć trwałego ownera;
   ręczny skrypt w runtime state nie jest trwałością.
3. Wdrożyć zatwierdzony commit `courier_api`, unit z `LoadCredential`, wykonać
   daemon-reload i jeden kontrolowany restart `courier-api.service`.
4. Po udowodnieniu manifestu odtworzyć kontener z loopback-only publish.
5. Nigdy nie restartować `dispatch-telegram` w tym runbooku.

### Krok E — health i dowód allowed/denied

1. Sprawdzić PID, `NRestarts`, active/substate i status endpointu bez body.
2. Allowed: aplikacja przez HTTPS, lokalny API health i CDP przez tunel.
3. Denied z niezależnej sieci: direct 8767 v4, direct 8767 v6, direct 9222 v4,
   direct 9222 v6. Brak trasy IPv6 to osobny wynik, nie dowód reguły.
4. Uruchomić `tools/host_boundary_audit.py --live`; findings public bind mają
   zniknąć, a klasyfikacja reguł może zmienić się wyłącznie na `*_RULE_SEEN` lub
   `*_POLICY_SEEN`. Audytor nadal raportuje skuteczność host guard jako
   nieudowodnioną, ponieważ nie rozstrzyga kolejności, predykatów ani ścieżki
   pakietu. Dowodem są dopiero ordered ruleset review i niezależne testy v4/v6.
   Provider pozostaje w narzędziu `UNKNOWN`, a osobny proof musi zostać
   dołączony do change record.
5. Potwierdzić nowe poświadczenie pozytywnie i poprzednie negatywnie, wyłącznie
   kodem statusu. Nie logować ani nie porównywać ich treści.

## 5. Rollback bez przywracania ujawnionej wartości

Rollback ma zachować provider/host deny. Nie wolno wracać do publicznego bindu
ani usuwać ochrony tylko po to, aby odzyskać health.

- API/code failure: wrócić do poprzedniego kodu kompatybilnego z loopback, lecz
  utworzyć **kolejną nową rewizję** poświadczenia. Nigdy nie przywracać starej.
- Błąd nowej rewizji: unieważnić ją, wygenerować następną i ponowić test; stary
  carrier nie jest rollbackiem.
- Container failure: użyć zatwierdzonego poprzedniego image digest i manifestu,
  ale zachować loopback-only publish oraz tunel.
- Firewall error: z drugiej sesji naprawić wyłącznie wadliwą regułę/allowlistę.
  Pełny restore starego, otwartego rulesetu jest zabroniony.
- Po rollbacku powtórzyć health, allowed/denied v4/v6, PID/NRestarts i audytor.

## 6. Warunki zamknięcia

DONE wymaga: source commits i postimage hashes; provider proof; trwałych reguł
v4/v6; udowodnionego manifestu kontenera; testów allowed/denied z drugiej sieci;
pozytywnego health; negatywnego testu poprzedniej rewizji; gotowego rollbacku;
oraz zgodności raportu z runtime. Do tego czasu stan brzmi **NOT APPLIED**.
