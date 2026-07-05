# Diagnoza: „czemu godzinny cron git push nie pchał (master ahead 25)" + staged fix

> tmux18, 2026-07-05 ~19:00 UTC. Zadanie ad-hoc od Adriana po ręcznym pushu `4be5f2d` przez sesję koordynacyjną.

## 1. WERDYKT: cron 05.07 DZIAŁAŁ — „ahead 25" to burst commitów MIĘDZY godzinnymi tikami, nie awaria

Twarda oś czasu (reflog `origin/master` + syslog + `/root/backups/git_push.log`):
- Wpis crontab istnieje i odpala się co godzinę (syslog: 14:00, 15:00, 16:00, 17:00, 18:00 UTC — bez przerw).
- Cron pchał, gdy było co: **17:00:03 push `d8328b2..9e6174d`**, **18:00:03 push `9e6174d..1fc385a`** (reflog „update by push").
- „Ahead 25" = **dokładnie 25 commitów** zrobionych w oknie **18:00→18:41 UTC** przez 3 równoległe sesje
  (zweryfikowane: `git log 1fc385a..4be5f2d | wc -l` = 25). Ręczny push 18:44 po prostu uprzedził tik 19:00.
- Wcześniejszy odstęp 04.07 11:00 → 05.07 17:00 z samymi „Everything up-to-date" = w repo NIE było nowych
  commitów do 05.07 16:33 (`9e6174d`) — zgodne z KONSOLIDACJĄ („ahead 2" o 17:15).

## 2. ALE: dwie REALNE wady wpisu znalezione przy okazji

1. **Tagi nigdy nie pchane.** `git push origin master` nie rusza tagów; tagi w repo są lightweight
   (`git cat-file -t` = commit), więc nawet `--follow-tags` by ich nie wzięło. Stąd tag `l6c-…` wymagał
   ręcznego pusha. Tagi = punkty rollbacku → powinny lecieć z cronem (`--tags`).
2. **Fail jest CICHY.** W historii loga: **70 kolejnych godzin `! [rejected] non-fast-forward`** (origin był
   przed lokalem po cudzym pushu) — nikt nie wiedział, aż ktoś ręcznie zrekonsyliował. Plus 1× transient
   `kex_exchange_identification: Connection reset` (szum, nie problem). Log nie ma timestampów — korelacja
   wymagała syslog+reflog.

## 3. STAGED FIX (wykonanie za ACK): wrapper zamiast gołego wpisu

**Nowy skrypt (GOTOWY, przetestowany DRY_RUN=1 → rc=0, auth/refspec OK):** `/root/.openclaw/workspace/scripts/git_push_hourly.sh`
- `git push origin master --tags` (master + wszystkie tagi),
- nagłówek `== <UTC> rc=<n>` per bieg w tym samym logu `/root/backups/git_push.log`,
- alert Telegram (`telegram_utils.send_admin_alert`, wzorzec backup_sentinel) przy **≥2 kolejnych failach**,
  re-alert max co 6 h (anty-szum: pojedynczy reset SSH nie pageuje; 70-godzinny non-fast-forward pageuje po 2 h),
- stan failów: `/root/backups/git_push_fail.state` (kasowany przy sukcesie).

**Zmiana w crontab (za ACK) — podmiana JEDNEJ linii:**
```
# BYŁO:
0 * * * * cd /root/.openclaw/workspace/scripts/dispatch_v2 && git push origin master >> /root/backups/git_push.log 2>&1
# MA BYĆ:
0 * * * * /root/.openclaw/workspace/scripts/git_push_hourly.sh
```
Instalacja: `crontab -e` (albo `crontab -l | sed …` — wolę ręcznie/za zgodą, bo crontab ma 53 linie wielu projektów).
Rollback: przywrócenie starej linii; skrypt loguje do tego samego pliku, format wsteczne-zgodny (dopisany nagłówek).

## 4. Rezydualne (poza zakresem fixa, do świadomości)
- Okno „ahead do 59 min" między tikami jest WBUDOWANE w godzinną kadencję — jeśli ma być krócej, to zmiana
  kadencji (np. */15) albo push w post-commit hooku (odradzam: sesje commitują seriami).
- Migracja cron→systemd timer z OnFailure=telegram (wzorzec 11 serwisów) = czystsza opcja długoterminowa;
  nie robiona teraz (większy blast, wrapper załatwia obserwowalność).
