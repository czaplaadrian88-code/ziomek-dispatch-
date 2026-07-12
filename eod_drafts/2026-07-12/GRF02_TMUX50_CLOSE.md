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

## Live, bramka i rollback

W tej sesji nie wykonano deployu, restartu, flipa, migracji ani zmiany danych live.
`nadajesz-panel.service` pozostał na PID 2028171, `NRestarts=0`, start 09.07.
Żywy frontend przed tym wydaniem wskazywał asset `index-DjGmO1bc.js`; nowy build
nie został skopiowany do `/var/www/html/admin-panel`.

Nowa funkcja jest więc TECH COMPLETE, ale NOT LIVE. Wydanie wymaga osobnego ACK:
backup `/var/www/html/admin-panel`, rsync builda z `--base=/admin/`, restart wyłącznie
`nadajesz-panel.service` (backend ma nowy endpoint), a potem health/admin/HTTP 401 gate,
PID/NRestarts i journal. Telegram i procesy dispatch pozostają nietknięte.

Rollback przed deployem: brak operacji live; kod `git revert 5924e19`. Rollback po
deployu: przywrócić backup frontu, revert commitu, jeden kontrolowany restart panelu,
smoke i potwierdzenie poprzedniego assetu.

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
