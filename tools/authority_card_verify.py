#!/usr/bin/env python3
"""Read-only CLI weryfikacji karty ``auto.canary.v1``."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


try:
    from dispatch_v2 import authority_card as AC
    from dispatch_v2 import common as C
except ImportError:  # pragma: no cover - samodzielny odczyt z worktree
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "authority_card_cli_local", root / "authority_card.py")
    AC = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = AC
    spec.loader.exec_module(AC)
    C = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only weryfikator podpisanej karty execution authority")
    parser.add_argument("--card", default=AC.CARD_PATH)
    parser.add_argument("--audit", default=AC.AUDIT_PATH)
    parser.add_argument("--build-sha", default=AC.BUILD_SHA_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="body, skrócony SHA i wynik weryfikacji")
    sub.add_parser("verify", help="weryfikacja; exit 0/1")
    sub.add_parser("template", help="szkielet body do uzupełnienia")
    return parser


def _flag_fingerprint():
    if C is None:
        return None
    try:
        return C.flag_fingerprint()
    except Exception:
        return None


def _verdict(args):
    return AC.verify_card(
        card_path=args.card,
        audit_path=args.audit,
        now=datetime.now(timezone.utc),
        code_git_sha=AC.read_code_git_sha(args.build_sha),
        flag_fp=_flag_fingerprint(),
    )


def _read_body(path: str):
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as stream:
            body = json.load(stream)
        return body if isinstance(body, dict) else None
    except Exception:
        return None


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "template":
        print(json.dumps(
            AC.template_body(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    verdict = _verdict(args)
    if args.command == "verify":
        print(json.dumps({
            "class_id": AC.CLASS_ID,
            "valid": verdict.valid,
            "reason": verdict.reason,
            "card_sha256_12": (
                verdict.card_sha256[:12] if verdict.card_sha256 else None),
        }, ensure_ascii=False, sort_keys=True))
        return 0 if verdict.valid else 1

    body = verdict.body or _read_body(args.card)
    digest = verdict.card_sha256
    if digest is None and isinstance(body, dict):
        digest = AC.card_sha256(body)
    print(json.dumps({
        "body": body,
        "card_sha256_12": digest[:12] if digest else None,
        "verification": {
            "valid": verdict.valid,
            "reason": verdict.reason,
        },
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
