# Diagnoza adopcji GPS-5b (dlaczego flota nie ma vc60+) — 2026-07-05 ~21:40 UTC

Agent diagnostyczny READ-ONLY. Nic nie zmieniano w systemie. Raport przygotowany na poniedziałek rano.

## TL;DR (werdykt jednym zdaniem)
**To NIE jest problem dystrybucji APK — kanał działa idealnie (vc70 leży na serwerze i endpoint go ogłasza).
Prawdziwa przyczyna: apka v2 ma znikomą penetrację floty — realnie GPS przez apkę śle DZIŚ jeden kurier (cid=492), a on jest już na vc70.** Licznik „1/553" to liczba REKORDÓW, nie kurierów, i odzwierciedla, że istnieje dokładnie jeden kurier vc60+ z aktywnymi dostawami i realnym GPS. Werdykt pokrycia nie ruszy sam z siebie — trzeba ręcznie onboardować garstkę kurierów.

---

## USTALENIA

### 1. Dystrybucja APK — SPRAWNA, na najnowszej wersji
- `/var/www/html/apk/courier-v2.apk` — mtime **05.07 20:44 UTC**, 19,79 MB (świeży build z dzisiejszego sprintu modernizacji).
- Manifest `dispatch_state/courier_app_version.json` (mtime 20:44) oraz endpoint `https://gps.nadajesz.pl/api/app/version` zwracają **zgodnie: latest=70 / 0.9.56 / min=1 / apk_url=…/courier-v2.apk**.
- Nginx `location /apk/` serwuje plik poprawnie (`gps-nadajesz`, MIME apk, no-cache, backupy .bak zablokowane regexem F2).
- **Pułapki 52/53 (APK bez bumpu manifestu) BRAK** — oba kroki zrobione, endpoint ogłasza vc70. To nie jest „stary APK na nginx".

### 2. `min_version_code=1` → aktualizacja jest MIĘKKA (do odłożenia w nieskończoność)
Logika updatera (`app/.../update/AppUpdater.kt` + `MainActivity.kt`):
- `available = latestCode > zainstalowany` → pokazuje **overlay do odrzucenia** („aktualizacja dostępna", przycisk 1-klik).
- `forced = zainstalowany < minCode`. Przy min=1 **forced jest NIGDY prawdą** → monit zawsze da się zamknąć (`onDismiss = if (forced) null else {...}`).
- Ścieżka aktualizacji jest w 100% PULL / opt-in: kurier musi (a) OTWORZYĆ apkę [check odpala się przy starcie MainActivity], (b) TAPNĄĆ update, (c) zezwolić „Instaluj nieznane aplikacje" (`canRequestPackageInstalls` → zrzuca do ustawień systemowych), (d) domknąć instalator. Zero pusha, zero auto-instalacji.

### 3. Faktyczny rozkład wersji na flocie — apkę v2 realnie używa ~1 kurier
Źródła: `courier_api.db` (tabele `sessions` z `app_version` z nagłówka `X-App-Version`, oraz `gps_history` z pingami GPS).

- **GPS przez apkę ost. 3 dni: TYLKO cid=492** (Jakub W) — 2995 pingów, ostatni 21:39 UTC. Nikt inny.
- **GPS przez apkę ost. 14 dni: 5 kurierów** — 492 (aktywny), 123 Bartek (do 29.06), 484 (do 26.06), 393 (do 26.06), 370 (do 23.06). Czterech odpadło / siedzi na starych wersjach.
- **Sesje v2 ost. 7 dni: 3 kurierzy** — cid=492 na **0.9.56 (vc70)**, cid=400 Adrian R (wersja NULL, 02.07 — testowo), cid=123 Bartek na 0.9.39 (stara, 29.06).
- Sesje w bazie łącznie: 277 / 16 różnych kurierów kiedykolwiek. Flota (`courier_tiers.json`) = **62 kurierów**.
- **cid=492 sam trzyma tempo wydań:** 0.9.41 (29.06–01.07) → 0.9.50 (03–05.07) → 0.9.52 → **0.9.56/vc70** — jedyny zaangażowany kurier zaktualizował się przez wszystkie dzisiejsze buildy. To dowód, że mechanizm DZIAŁA, gdy kurier się angażuje.
- Endpoint `/api/app/version` odpytuje w 14 dni ~25 różnych IP (mobilne, zawyżone), DZIŚ tylko 3 IP. Czyli urządzeń z zainstalowaną apką jest garstka, a aktywnie pracujących z GPS — jedno.

### 4. Czynnik obciążający: do vc70 serwis GPS umierał po KAŻDEJ aktualizacji
Z memory modernizacji (`courier-app-audyt-modernizacja-2026-07-05.md`): incydent „zlecenia się nie aktualizują" — serwis trackingu padał po każdej aktualizacji APK (`isTracking` kłamał), naprawione dopiero w **vc70** (`MY_PACKAGE_REPLACED` + idempotentny start). Efekt: kurierzy, którzy DAWNIEJ się zaktualizowali, tracili tracking i musieli się przelogowywać → aktualizowanie się apki było wręcz KARANE. To dodatkowo tłumaczy odpływ 4 kurierów, którzy używali GPS jeszcze w 22–26.06.

---

## DOWODY (komendy odtwarzalne)
```
# manifest + endpoint (oba = vc70):
cat dispatch_state/courier_app_version.json ; curl -s https://gps.nadajesz.pl/api/app/version

# adopcja 5b (rekordy, nie kurierzy) = 1, tylko cid=492:
python3 -c "import json;d=json.load(open('dispatch_state/courier_ground_truth.json'));print(sum(1 for e in d.values() if e.get('gps_arrived_at')))"  # -> 1

# GPS przez apke ost. 3 dni (DB courier_api.db):
sqlite3 courier_api.db "SELECT courier_id,COUNT(*),datetime(MAX(received_at),'unixepoch') FROM gps_history WHERE received_at>strftime('%s','now')-3*86400 GROUP BY courier_id"  # -> tylko 492

# rozklad wersji sesji ost. 7 dni: 492=0.9.56, 400=NULL, 123=0.9.39
```
APK dystrybucyjny: 20:44 UTC, backupy pokazują ~7 wydań w ciągu 05.07 (v0.9.50 → vc70) — vc60 (16:34) w kilka godzin zastąpione przez vc61..vc70; 5b jest we WSZYSTKICH (vc60 = przodek HEAD), więc „doprowadzić flotę do vc60" = w praktyce „doprowadzić do vc70".

---

## HIPOTEZA (główna przyczyna)
**Metryka „1/553" nie mierzy dystrybucji — mierzy PENETRACJĘ apki v2, która jest bliska zeru.**
Aby `gps_arrived_at` wzrósł, potrzeba jednocześnie: (1) apka vc60+, (2) aktywne zlecenia dostawy, (3) realny GPS docierający do apki. Ten iloczyn spełnia dziś **dokładnie jeden kurier — cid=492 — i on jest już na vc70.** Reszta floty albo nie używa apki v2 w ogóle (statusy/GPS idą panelem/reconcile), albo używała jej okazjonalnie i odpadła (dobity dodatkowo bugiem „serwis pada po update", naprawionym w vc70). Aktualizacja jest miękka i wyłącznie pull-owa, więc bez ręcznego onboardingu nikt nowy się nie pojawi. **Nie ma „kogoś, kto by rozesłał link" — ale nawet rozesłanie nie ruszy metryki bez wyboru konkretnych kurierów i dopilnowania GPS + dostaw.**

Odrzucone hipotezy: „stary APK na nginx" (FAŁSZ — vc70 leży i jest ogłaszany), „nie zbumpowano manifestu" (FAŁSZ — endpoint = vc70), „kill-switch ingestu" (FAŁSZ — ENABLE_GPS_ARRIVAL_INGEST default ON, rekord z 17:19 dowodzi że pisze).

---

## PLAN NA PONIEDZIAŁEK (dla Adriana / sesji)

**Krok 0 — przeramuj cel.** Werdykt pokrycia 5b NIE wymaga całej floty na vc60+. Wymaga KILKU kurierów z realnym GPS + dostawami przez apkę. Metryka do werdyktu powinna liczyć **distinct kurierów produkujących `gps_arrived_at`**, a nie surowe rekordy. Czekanie na „adopcję flotową" jest bezcelowe — ona nie przyjdzie organicznie.

**Krok 1 — wskaż 3–5 kurierów-kotwic i osobiście onboarduj.** Kandydaci = ci, co już kiedykolwiek słali GPS z apki: **cid=492 (już vc70, działa), 123 Bartek, 393, 484, 370.** Dla każdego: rozesłać link `https://gps.nadajesz.pl/apk/courier-v2.apk` (Telegram/telefon) z instrukcją: *otwórz apkę → „Aktualizuj" → zezwól „Instaluj nieznane aplikacje" → zaloguj się i pracuj z włączonym GPS*. vc70 naprawia padanie serwisu po update, więc doświadczenie będzie już bez „tracking gaśnie".

**Krok 2 — zweryfikuj adopcję po każdym kurierze (dwie liczby):**
```
# wersja per kurier (ma byc 0.9.56 / vc70):
sqlite3 courier_api.db "SELECT courier_id,app_version,datetime(last_activity_at,'unixepoch') FROM sessions WHERE last_activity_at>strftime('%s','now')-86400 GROUP BY courier_id"
# czy leci GPS z apki (pingi rosna):
sqlite3 courier_api.db "SELECT courier_id,COUNT(*) FROM gps_history WHERE received_at>strftime('%s','now')-6*3600 GROUP BY courier_id"
# licznik pokrycia 5b — distinct kurierzy, nie rekordy:
python3 -c "import json,collections;d=json.load(open('dispatch_state/courier_ground_truth.json'));c=collections.Counter(e.get('courier_id') for e in d.values() if e.get('gps_arrived_at'));print('rekordy',sum(c.values()),'kurierzy',len(c),dict(c))"
```

**Krok 3 — (opcja do decyzji Adriana, NIE robić bez ACK) twarde okno wymuszenia.** Gdyby zależało na szybszym pokryciu: jednorazowy bump `min_version_code` w manifeście do bieżącego vc na czas okna pomiarowego zamienia monit w wymuszony (forced, bez „później”) dla WSZYSTKICH używających apki. Ryzyko: przerywa pracę w trakcie zmiany, dotyka wszystkich userów apki. To zmiana konfiguracji (poza tą sesją read-only) — wymaga świadomej zgody i wpisuje się w protokół wydań apki (krok manifestu). Rekomendacja: raczej NIE — przy 5 kandydatach ręczny onboarding wystarczy i nie ryzykuje.

**Szacowany czas do sensownego pokrycia:** jeśli w pon. rano 3–5 kotwic zaktualizuje się i będzie pracować z GPS → kilka–kilkanaście `gps_arrived_at` na kilku kurierach w ciągu **2–3 dni roboczych** → realistyczny werdykt **~08–09.07** — ale WYŁĄCZNIE przy aktywnym onboardingu. Bez niego licznik zostanie na 1 i werdykt (a za nim flip O2, pomiar feas_carry #483000, dowód autonomii) będzie się przesuwał w nieskończoność.

---

## Uwagi dodatkowe
- Kill-switch `ENABLE_GPS_ARRIVAL_INGEST` = default ON; endpoint `/arrival` żywy (rekord cid=492 z 17:19 UTC to potwierdza). Backend nie jest wąskim gardłem.
- `[arrival]` nie loguje się do journala (serwis nie pisze stdout) — dowód adopcji bierz z DANYCH (ground_truth + gps_history), nie z logu.
- Werdykt pokrycia liczyć OD 05.07 17:19 UTC (pierwszy rekord), ale realnie zegar startuje dopiero od momentu, gdy zacznie pracować >1 kurier.
