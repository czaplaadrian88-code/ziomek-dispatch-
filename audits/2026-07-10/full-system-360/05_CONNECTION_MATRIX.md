# Connection matrix

| Źródło | Cel | Protokół / dane | Kierunek | Timeout/fallback | Owner / uwaga |
|---|---|---|---|---|---|
| panel gastro | panel watcher/client | HTTPS + HTML/CSRF | R/W | timeout, parser v2→fallback | krytyczny ingest |
| dispatcher | OSRM | HTTP route/table | R | CB/cache/haversine | strict health osobno |
| panel watcher | event bus | SQLite | W | ręczny DLQ | brak auto-retry |
| dispatcher | konsola panelu | pliki + import biblioteki | R/W | fail-soft | cross-repo kontrakt |
| dispatcher | courier API | pliki + import biblioteki | R/W | fallback view | auth boundary |
| Papu bridge | gastro/panel | HTTP | R/W | dedup/state | mapping biznesowy |
| DrTusz bridge | zewnętrzne źródło→panel | HTML/HTTP | R/W | retry/alert | crash-safe dedup do kontroli |
| epaka fetcher | zewnętrzny panel→CSV | HTTP/OCR | R | lokalny log | brak mocnego alertu |
| backup | off-site storage | SFTP/restic | W/R | sentinel | restore drill nieudowodniony |
| alerting | Telegram/router | HTTPS | W | route priority | bot dispatch OFF ≠ alerty HTTP OFF |

Macierz obejmuje połączenia znalezione w kodzie, unitach i mapach. Nie wykonano
zewnętrznego skanu sieci ani live requestów modyfikujących. Szczegółowe ryzyka
mostów: `09`, `11` i findings `INTE-*`.
