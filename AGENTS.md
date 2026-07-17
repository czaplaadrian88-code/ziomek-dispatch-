# AGENTS.md — instrukcje repo dispatch_v2 dla sesji Codex

Lustro najwazniejszych dyrektyw z `CLAUDE.md` (sekcja 🧭 START TUTAJ) — Codex
czyta TEN plik, Claude Code czyta CLAUDE.md. Kanon globalny Codex:
`/root/.codex/AGENTS.md` (bootstrap, Przykazanie #0, sekrety, git/worktree).

## Start

1. Przykazanie #0 — KAZDA zmiana silnika idzie protokolem ETAP 0→7:
   `/root/.claude/projects/-root/memory/ziomek-change-protocol.md`.
2. Nawigacja: `docs/CODEMAP.md` → `docs/ARCHITECTURE.md` → `docs/decisions/`.
   NIE skanuj repo — wszystko ma mapy.
3. Srodowisko: testy WYLACZNIE `/root/.openclaw/venvs/dispatch/bin/python -m
   pytest tests/ -q`; zywy stan = `/root/.openclaw/workspace/dispatch_state/`
   (katalog `dispatch_v2/dispatch_state/` w repo to NIE stan live); flagi
   silnika = `../flags.json` (3 swiaty flag: ADR-004).

## Skille = domyslne narzedzia sesji (od 17.07)

Jezeli dla czynnosci istnieje skill — UZYWASZ skilla, nie recznych komend
(drivery maja bramki ACK, oracle i selftesty pod nocnym straznikiem; reczne
odtwarzanie = dryf i pominiete bezpieczniki). Katalog: `.claude/skills/README.md`.

| Robisz… | NAJPIERW odpal |
|---|---|
| start sesji / rozpoznanie stanu / wybor zadania | `python3 .claude/skills/ziomek-cto/driver.py brief` |
| planowanie KAZDEJ zmiany silnika (ETAP 3 #0) | `python3 .claude/skills/ziomek-cto/driver.py scope "<temat>"` |
| gotowy diff przed commitem (bramka DoD) | `python3 .claude/skills/ziomek-cto/driver.py dod <diff\|ref> --evidence <plik>` (exit 1 = STOP) |
| koniec sesji / wpis handoff do memory | `python3 .claude/skills/ziomek-cto/driver.py handoff` |
| diagnoza uslug / straznik / przecieki / flagi / suita | `.claude/skills/run-dispatch-v2/driver.sh health` (guard/litter/flags/collect) |
| kandydat przed promocja/merge | `python3 .claude/skills/ziomek-blind-review/driver.py blind <katalog>` → swiezy recenzent → `check` |

## REGULA DWOCH MIEJSC (Adrian 17.07)

Kazda dyrektywa sesyjna (routing, zasady pracy, bramki) MUSI byc zapisana
rownolegle w DWOCH miejscach: **CLAUDE.md (Claude Code) i AGENTS.md (Codex)**
— repo `CLAUDE.md` ↔ `AGENTS.md` (ten plik) oraz globalnie `/root/CLAUDE.md`
↔ `/root/.codex/AGENTS.md`. Zmiana jednego bez drugiego = zmiana czesciowa
(niezakonczona).

## Twarde granice (skrot; pelne w /root/.codex/AGENTS.md)

- Deploy / flip flagi / restart uslugi / telegram / peak — TYLKO za ACK
  wlasciciela, po protokole #0. ODR-002: zaden skill/dokument nie nadaje
  execution authority.
- Multi-sesja: wspolne repo — commit atomowo jawnym pathspec (C1-git), nigdy
  `git add -A`, nigdy nie cofaj cudzego WIP; przed commitem `git log -3` +
  `git status`.
- Baseline testow musi byc ZIELONY przed zmiana; pelna regresja vs baseline
  po zmianie; manifest straznika re-seed przy zmianie zbioru nodeidow
  (`night_guard --update-manifest`, fail-closed).
