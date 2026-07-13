# Dareczek — przygotowanie pilota source-only, 2026-07-13

`model_tier=sol`, `effort=ultra`: autoryzacja, dane klientów, współbieżność,
migracje i granice wysyłki mają wysoki koszt błędu.

## Wynik

ETAP 0 i ETAP 1 Dareczka są zaimplementowane lokalnie w panelu. Hardening jest w
commicie `f262c55` na branchu `codex/dareczek-pilot-prep-20260713`, worktree
`/root/dareczek_pilot_prep_20260713`. Bazą był `3328b50`. Commit nie został
pushnięty, zmergowany ani wdrożony.

Dodano bieżące role i sender authority, TOTP+HMAC dla ACK, trwały audit odmów,
wersjonowanie i revoke ofert, globalny hot kill, transakcyjne limity, świeże
blokady ORM/PostgreSQL, fail-closed migracje, offline politykę epaka/mailboxa,
dry-run retencji oraz test restore szyfrowanego artefaktu syntetycznego.

## Dowód

- Dareczek + migracje: 122 passed;
- izolowany PostgreSQL 16 bez sieci: 19 passed;
- pełny backend: 1212 passed, 1 skipped, 33 warnings;
- compileall, Alembic single head `dareczek02`, `git diff --check`: PASS;
- niezależny review: otwarte P0/P1 w kodzie Stage1/Level2 = 0/0.

## Stan produkcji i bramki

Nie wykonano migracji live, deployu, restartu, logowania do epaka, odczytu
skrzynki, importu realnych danych, zmiany DNS, flipa flag ani wysyłki. Dareczek
nie ma procesu, więc PID/NRestarts nie dotyczą tego sprintu.

Bezpieczne wartości pozostają: autonomia 1, `NO_PRODUCTION_SEND=true`, exact ACK
wymagany, kill switch true, transport produkcyjny nieskompilowany, retencja apply
false. Rollback jest code-only przez jawny revert/porzucenie brancha; addytywnych
ledgerów nie wolno cofać destrukcyjnie.

## HOLD przed realnym pilotem

P0 to wspólny outbox: wszystkie ścieżki Maileka muszą obowiązkowo czytać tę samą
permission/suppression/kill/quota. Ponadto potrzebne są: treść umowy i read-only
mapa UI epaka, atomowy importer ofert i dowodów zgód, mailbox oraz SPF/DKIM/DMARC,
KMS i restore drill, provisioning ról/TOTP/authority, audit pełnych odczytów i
alarm eksportu oraz potwierdzenie DPO/legal.

Proponowany następny sprint trwa 5–7 dni. Pilot dopiero po osobnych ACK na
migrację, deploy, restart, login i wysyłkę: 3–5 ręcznie wskazanych kontaktów,
każda wiadomość z `ACK SEND <message_id>`, początkowo maksymalnie 5/dzień i dwa
dni obserwacji. Zielone testy nie są opinią prawną.
