# Dane, store’y i writerzy

| Store | Writerzy | Czytelnicy | Ochrona | Otwarte ryzyko |
|---|---|---|---|---|
| orders state | state machine + kontrolowane adaptery | silnik, panel, API | flock/RMW, atomic replace, guardy | mieszane konwencje czasu |
| courier plans | plan manager, recheck, watcher, panel route | panel, API, silnik | lock + CAS w dispatcherze | cross-repo writer panelu wymaga osobnej kontroli |
| shadow decisions | shadow serializer | tools, panel, audyt | append + rotacja | copytruncate i duży payload |
| events DB | event bus | ręczny replay/cleanup | WAL, metadane retry | brak automatycznego workera/policy |
| geocode caches | geocoding/bootstrap | silnik/panel | stały lockfile, merge, fsync | jakość fallbacków/pinów |
| manual overrides | admin/panel tooling | fleet resolver | atomic replace | multi-writer locking do ponownej weryfikacji |
| world records | recorder | replay gate | append, retencja | niepełny zbiór wejść |
| API DB | courier API | API | WAL | konsolidacja i ownership poza tym audytem |

Najsilniejsza strona to atomowość większości writerów. Największa luka klasowa
pojawia się, gdy writer żyje w innym repo lub procesie niż mapa naprawy. Nie
wykonano migracji ani odczytu pełnych payloadów.
