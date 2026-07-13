"""Wspólne parsowanie wartości liczbowych z panelu i Google Sheets."""


def parse_zl(raw: object) -> float:
    """Obsługuje polski i angielski zapis liczby; pustą wartość traktuje jako 0."""
    s = str(raw or "").strip().replace("\u00a0", "").replace(" ", "")
    if not s:
        return 0.0
    last_dot = s.rfind(".")
    last_comma = s.rfind(",")
    if last_dot == -1 and last_comma == -1:
        return float(s)
    decimal_pos = max(last_dot, last_comma)
    decimal_sep = s[decimal_pos]
    other_sep = "," if decimal_sep == "." else "."
    s = s.replace(other_sep, "")
    if decimal_sep == ",":
        s = s.replace(",", ".")
    return float(s)
