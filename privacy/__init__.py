"""Privacy boundary for versioned, access-restricted operational ledgers."""

from .private_ledger import (
    AppendOutcome,
    FileKeyProvider,
    LedgerConfig,
    PrivateLedgerError,
    SecureJsonlWriter,
    append_ledger_record,
    decode_ledger_record,
    iter_decoded_jsonl,
    iter_ledger_records,
    redact_record,
    rotate_secure_jsonl,
)

__all__ = [
    "AppendOutcome",
    "FileKeyProvider",
    "LedgerConfig",
    "PrivateLedgerError",
    "SecureJsonlWriter",
    "append_ledger_record",
    "decode_ledger_record",
    "iter_decoded_jsonl",
    "iter_ledger_records",
    "redact_record",
    "rotate_secure_jsonl",
]
