# GRF-02 — domknięcie WIP z tmux50 (2026-07-12)

## Zakres i stan wejściowy

Adrian polecił sprawdzić `tmux50` i dokończyć pracę. Pane był zatrzymany limitem
narzędzia po rozpoczęciu ostatniej poprawki edytora grafiku. Zastany problem był
potwierdzony w kodzie: ikona przy godzinie wywoływała `DELETE /builder/shift`, więc
usuwała cały rekord `(day, cid)`. Niedokończony helper nie był podpięty, a jego
algorytm „do bliższej krawędzi” mógł po kliknięciu w środku skasować kilka godzin.

Repo panelu: `/root/.openclaw/workspace/nadajesz_clone`, branch
`coordinator-console`, base `dc757e9`, commit domknięcia `5924e19`; push parity
`origin/coordinator-console...HEAD = 0/0`.
WIP obejmował również wcześniejsze, udokumentowane i już wdrożone zmiany GRF-02
z 09.07: kalendarz dyspozycji, trwałe commity routera, picker z dyspozycji,
czytelniejszy layout oraz `Grafik aktualny` z panelu.

## Root cause i kontrakt

`VehicleAssignment` ma unikalność `(day, courier_cid)` i przechowuje jeden ciągły
przedział `shift_start..shift_end`. Bez migracji na wiele odcinków nie da się usunąć
godziny ze środka bez utraty jednej części zmiany. Dlatego kanoniczna operacja:

- skraca dokładnie o 60 minut od początku albo końca;
- odrzuca godzinę środkową HTTP 409 zamiast zgadywać;
- blokuje aktualny rekord `SELECT ... FOR UPDATE`;
- zachowuje auto, nazwę i pozostałe dane przypisania;
- dla zmiany trwającej najwyżej godzinę usuwa rekord (nie zostawia zera);
- obsługuje granicę `24:00` w piątek/sobotę.

Frontend renderuje wpisy z `day.shifts` po CID, nie przez mapę nazwa→CID. Ikona
pojawia się wyłącznie na krawędziach, a jej opis mówi, czy skraca początek, koniec,
czy usuwa jedyną godzinę. React Query odświeża edytor, dyspozycje i `Grafik aktualny`.

## Mapa kompletności

| Miejsce | Rola | Status | Dowód |
|---|---|---|---|
| `ScheduleBuilder.tsx` | render CID i akcja krawędzi | TAK | build + focused tests backend contract |
| `schedule/api.ts` | `PATCH /builder/shift/hour` | TAK | bundle zawiera endpoint |
| `backend/app/api/schedule.py` | auth, walidacja, commit | TAK | HTTP round-trip |
| `schedule_builder.py` | atomowa semantyka skrócenia | TAK | unit + mutation-probe |
| `VehicleAssignment` | jeden ciągły writer | N-D | bez migracji; użyty istniejący kontrakt |
| `builder_week` | obsada godzinowa | TAK | asercje 09/10/16/17 |
| `current_grid` | `Grafik aktualny` | TAK | wspólne invalidation + istniejący test DB source |
| silnik dispatch | inny kanon grafiku | N-D | poprawka nie przełącza źródła silnika ani HARD/SOFT |
| flaga / state migration | sterowanie | N-D | brak nowej flagi i migracji |

## Testy i dowody

- baseline panel focused: `16 passed`;
- po zmianie focused: `20 passed`;
- `py_compile` dwóch modułów: PASS;
- Vite production build `--base=/admin/`: PASS, chunk GRF-02 zawiera endpoint i opisy;
- pełny backend panelu: `1089 passed, 1 failed`; jedyny fail to zastany
  `test_alembic_baseline` (brak migracji trzech tabel kontrolingu), identyczna klasa
  opisana w handoffie 09.07, poza zakresem;
- TypeScript: błąd GRF-02 `trimAtHour unused` usunięty; pozostaje pięć zastanych
  błędów w nietkniętym `ControllingPanel.tsx`;
- ESLint zakresu: nowy kod bez własnego trafienia; pełny plik ma zastany
  `react-hooks/set-state-in-effect` w wyborze dnia, obecny na HEAD;
- mutation-probe: wyłączenie blokady środka dało `2 failed`; po przywróceniu
  `2 passed`, diff względem commitu zero;
- Ziomek baseline i final: oba `5152 passed / 27 skipped / 8 xfailed / 0 failed`;
- flag lifecycle: `505/505`, zero błędów;
- entropy dashboard: uruchomiony bez wzrostu od tej zmiany. Jego wartości
  `[AUDIT-BASELINE]` są jawnie stare i rozjeżdżają się z nowszym kanonem VOID=0;
  nie użyto ich do decyzji.

Ciężkie interwały host-load do sensitivity at-214:

- dispatch baseline `[2026-07-12T13:37:53Z,13:42:40Z]`;
- panel full `[2026-07-12T13:49:34Z,13:50:44Z]`;
- dispatch final `[2026-07-12T13:53:34Z,13:58:19Z]` — 5152/27/8/0.

## Live, wydanie i rollback

Pierwsza faza tej samej sesji zakończyła się świadomie na TECH COMPLETE/NOT LIVE.
Następnie Adrian przekazał jawny ACK: `ack deploy i restart nadajesz-panel.service`.
Kanon D2 został rozstrzygnięty bez zgadywania: 17–20 w niedzielę jest peak scoringowy,
ale operacyjny `blackout-ops` obowiązuje w sobotę 16–21, więc nie była potrzebna
osobna sobotnia bramka peak.

Preflight live:

- `tmux58` był bieżącym FLIPMASTEREM; `tmux77` pracował w osobnym worktree audytu,
  bez zmian w repo panelu;
- panel `coordinator-console=origin/coordinator-console=5924e19` (0/0), a dziewięć
  plików zakresu miało zero diff do HEAD;
- chroniony `flags.systemd.env` miał mtime `2026-07-09 11:01:46 UTC`, wcześniejszy
  niż start starego procesu `12:05:49 UTC`; restart nie ładował oczekującej nowszej
  zmiany. Treści chronionego pliku nie odczytano;
- dwa stare nieśledzone watchery nie miały żadnego importera w `app/` ani testach;
- stary runtime: PID `2028171`, `NRestarts=0`, health 200, asset
  `index-DjGmO1bc.js`, zero warningów w journalu.

Release preflight po ACK: py_compile/import PASS (`routes=15`, helper callable),
focused `20 passed / 4 warnings`. Pierwsze wywołanie Vite z błędnego katalogu
`frontend-shared` failnęło `Cannot resolve entry module index.html` i niczego nie
wdrożyło; kanoniczne `panel/frontend` z `vite build --base=/admin/` przeszło PASS.
Bundle zawiera endpoint oraz oba jawne opisy skracania, a `dist/index.html` wskazuje
`/admin/assets/index-CB3bgZBR.js`.

Backup 1:1 utworzono przed nadpisaniem:
`/var/www/html/admin-panel.bak-grf02-hourtrim-20260712T151212Z` (root:root,
katalog 0755, index 0644, `diff -qr` zero). O `2026-07-12 15:14:24 UTC`
wykonano `rsync -a --delete dist/ /var/www/html/admin-panel/` i dokładnie jeden
`systemctl restart nadajesz-panel.service`.

Postimage LIVE:

- PID `683706`, `NRestarts=0`, `SubState=running`, listener `127.0.0.1:8000`;
- backend `/api/health` 200 i publiczne `/admin/` 200;
- publiczny asset `index-CB3bgZBR.js`, live directory ma zero diff do `dist`;
- `PATCH /api/schedule/builder/shift/hour` bez tokenu = 401 bezpośrednio i przez
  `/admin/api/`, więc endpoint istnieje i auth gate działa; nie mutowano syntetycznie
  produkcyjnego grafiku;
- journal od restartu: zero warningów; efektywne `GRF02_CURRENT_FROM_PANEL`,
  `DRIVERS_ADMIN`, `GRF01_SCHEDULE` pozostały `<unset>` przed i po, czyli bez flipa;
- tag release `grf02-hour-trim-live-verified-20260712` wypchnięty na origin.

Rollback runtime jest gotowy bez restartu backendu:
`rsync -a --delete /var/www/html/admin-panel.bak-grf02-hourtrim-20260712T151212Z/ /var/www/html/admin-panel/`.
Przywraca poprzedni asset `index-DjGmO1bc.js`, przez co addytywny backendowy endpoint
pozostaje nieużywany. Wcześniejsze zalecenie `git revert 5924e19` zostało odwołane:
commit obejmuje także wcześniejszy, już działający WIP GRF-02 z 09.07, więc wholesale
revert cofnąłby za szeroki zakres. Ewentualny source rollback musi selektywnie usunąć
tylko endpoint/helper/hook tej operacji, przejść testy i osobną bramkę restartu.

Okno obserwacji trwa do `2026-07-14 15:15 UTC`. Odczyt: health i publiczny asset,
401 gate, warning/error journal od deployu oraz potwierdzenie operatora przy pierwszym
naturalnym użyciu. Nie utworzono timera ani at-joba i nie wykonano testowej mutacji
danych live. Telegram i procesy dispatch pozostały nietknięte.

Chronione/cudze pliki pozostawione bez zmian: `panel/backend/flags.systemd.env`, dwa
watchery pickup, backup SQL, dispatch `CLAIM_LEDGER_HARD_GATE_CARD.md`, Papu oraz
`daily_accounting/kurier_full_names.json`.

## Zamknięcie tmux50 i near-miss operacyjny

Po commitach/pushach panelu, dispatch i memory oraz snapshotcie 0600 wykonano
sprzątanie starej sesji. Pierwszy odczyt pane pokazywał zatrzymanego limitem
Claude, ale bezpośredni pre-close `display-message` pokazał już zmianę tożsamości:
`session=50 command=codex dead=0 attached=1`. Mimo tego sesja została zamknięta.
To był błąd operacyjny: zmiana komendy i aktywne podpięcie powinny anulować kill.

Po zdarzeniu potwierdzono, że `tmux50` nie istnieje, a wszystkie dziewięć plików
GRF-02 jest bajtowo zgodnych z commitem `5924e19`; obce dirty mają ten sam zestaw,
więc nie utracono niezapisanego WIP na filesystemie. Utracony został jednak proces
i scrollback tej sesji. Bieżący Codex działa w nowej sesji tmux, a `tmux58`
pozostał nietknięty.

Do żywego protokołu dopisano C54: przed każdym `kill-session` trzeba ponownie
zebrać pane tail/title/current_command/cwd/attached i porównać z audytem. Każda
zmiana tożsamości, `attached=1` albo aktywna komenda oznacza STOP; po sprzątaniu
obowiązkowa jest także kontrola unikalnego WIP na filesystemie.
