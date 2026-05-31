#!/usr/bin/env python3
"""Reminder (Telegram) o merge brancha sprintu OBJ FRESH do master.

Odpalany przez `at` ~06.06 tuż PO werdykcie +7d (job werdyktu osobno). Czysty
przypomniacz — NIE mergeuje sam (merge wymaga ludzkiego ACK + sprawdzenia czy
werdykt zielony). Pokazuje aktualny stan brancha (ahead/behind, ostatni commit),
żeby Adrian zdecydował świadomie.
"""
import subprocess
import sys

REPO = "/root/.openclaw/workspace/scripts/dispatch_v2"
BRANCH = "obj-pickup-freshness-2026-05-30"
# Nazwa jest ambiguous (istnieje branch ORAZ tag o tej samej nazwie) — jawny
# refs/heads/ wymusza rozwiązanie do brancha, nie tagu.
BRANCH_REF = f"refs/heads/{BRANCH}"
SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _git(*args):
    try:
        return subprocess.run(
            ["git", "-C", REPO, *args],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"(git fail: {type(e).__name__})"


def build_message():
    cur = _git("branch", "--show-current")
    ahead = _git("rev-list", "--count", f"master..{BRANCH_REF}")
    last = _git("log", "-1", "--oneline", BRANCH_REF)
    tags = _git("tag", "--points-at", BRANCH_REF)
    parts = [
        "🔔 OBJ FRESH — przypomnienie o MERGE",
        f"branch `{BRANCH}` → master ({ahead} commitów ahead)",
        f"ostatni: {last}",
        f"tagi na branchu: {tags or '(brak na HEAD)'}",
        f"obecny checkout: {cur}",
        "",
        "Jeśli werdykt +7d był 🟢/🟡 (ogon odbioru spadł, koszt jazdy OK):",
        f"  git -C {REPO} checkout master && git -C {REPO} merge --no-ff {BRANCH_REF}",
        "  (refs/heads/ bo nazwa = i branch, i tag — bez tego git wybierze tag)",
        "Jeśli 🔴 (ogon nie spadł): NIE merge — flaga OFF zgodnie z werdyktem.",
    ]
    return "\n".join(parts)


def main():
    no_tg = "--no-telegram" in sys.argv
    msg = build_message()
    print(msg)
    if no_tg:
        print("\n[telegram] SKIP (--no-telegram)")
        return
    try:
        from dispatch_v2 import telegram_utils as T

        ok = T.send_admin_alert(msg)
        print(f"\n[telegram] send_admin_alert ok={ok}")
    except Exception as e:  # noqa: BLE001
        print(f"\n[telegram] FAIL {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
