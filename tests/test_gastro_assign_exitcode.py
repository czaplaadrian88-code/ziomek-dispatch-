"""AUDYT 2.0 Blocker-1 (lane auton-blockers) — strażnik „fałszywego sukcesu"
gastro_assign.py.

Bug: `if ... 'error' not in str(result).lower()` przepuszczał stronę logowania /
HTML (wygasła sesja) jako ASSIGN_OK, a gałąź „nieoczekiwana odpowiedź" NIE robiła
sys.exit(1) → exit 0 mimo niewykonanego przypisania. executor/auto_koord/telegram
ufają exit-code → cichy drop zlecenia bez człowieka w pętli.

Fix (STAGED kopia deploy_staging/scripts/gastro_assign.py — HTTP zamockowane,
ZERO realnych callów do panelu):
  - `_classify_assign_response`: PUSTE ciało + JSON-ok = SUKCES (kontrakt panelu:
    auto_koord 1057 parkowań + telegram realne przypisania), HTML/logowanie/błąd
    = PORAŻKA;
  - każda porażka → `sys.exit(1)` na stderr;
  - `--verify` = read-back edit-zamowienie (id_kurier) w torze autonomii.

Testy BEHAWIORALNE (C13): sterują odpowiedzią panelu i sprawdzają KOD WYJŚCIA
main(); mutacje ×2 odwracają fix i wymagają, by wynik się ZEPSUŁ (strażnik ma zęby).
PARYTET: żywy plik vs staged różnią się WYŁĄCZNIE fixem (funkcje nietknięte
bajt-identyczne; stale-mirror z mapy L8 = FAIL).

Izolacja (C12(e)): staged ładowany PO ŚCIEŻCE (importlib), sys.modules sprzątane
try/finally; ścieżki względne od pliku testu.
"""
import ast
import importlib.util
import os
import sys

import pytest

# ── ścieżki (C12(e)) ──────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STAGED = os.path.join(_REPO, "deploy_staging", "scripts", "gastro_assign.py")
_LIVE = "/root/.openclaw/workspace/scripts/gastro_assign.py"


def _load_by_path(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ga():
    assert os.path.exists(_STAGED), f"brak staged gastro_assign: {_STAGED}"
    mod = _load_by_path(_STAGED, "gastro_assign_staged_wt")
    try:
        yield mod
    finally:
        sys.modules.pop("gastro_assign_staged_wt", None)


# seams wstrzykiwane do main() — ZERO HTTP
def _login_ok():
    return "csrf-test"


def _kid_123(_name):
    return 123


def _assign_returning(body):
    def _a(order_id, kurier_id, time_minutes, csrf):
        return body
    return _a


def _fetch_kid(kid):
    def _f(order_id, csrf):
        return {"id_kurier": kid, "czas_odbioru": 12}
    return _f


def _run_courier(ga, *, assign_body, verify=False, fetch_fn=None, get_kid=_kid_123):
    argv = ["--id", "480300", "--kurier", "Bartek O.", "--time", "5"]
    if verify:
        argv.append("--verify")
    return ga.main(
        argv, login_fn=_login_ok, assign_fn=_assign_returning(assign_body),
        get_kid_fn=get_kid, fetch_fn=(fetch_fn or _fetch_kid(123)),
    )


# ══════════════════════════════════════════════════════════════════════════
# _classify_assign_response — jednostkowo (rdzeń kontraktu panelu)
# ══════════════════════════════════════════════════════════════════════════
def test_empty_body_is_success_protects_auto_koord_twin(ga):
    # PUSTE ciało = sukces panelu (1057 parkowań auto_koord + telegram). NIE regresować.
    assert ga._classify_assign_response({"raw": ""})[0] is True
    assert ga._classify_assign_response({"raw": "   \n"})[0] is True


def test_json_ok_is_success(ga):
    assert ga._classify_assign_response({"success": True})[0] is True
    assert ga._classify_assign_response({"status": "ok"})[0] is True
    assert ga._classify_assign_response({"przypisano": 1})[0] is True   # nieznany ok-kształt


def test_session_bounce_html_is_failure_THE_BUG(ga):
    # Odbicie na stronę logowania (wygasła sesja) — DZIŚ przechodziło (brak "error").
    ok, detail = ga._classify_assign_response(
        {"raw": '<!DOCTYPE html><html><form>zaloguj name="_token" value="x"</form></html>'})
    assert ok is False and "session_bounce" in detail


def test_explicit_json_error_is_failure(ga):
    assert ga._classify_assign_response({"success": False})[0] is False
    assert ga._classify_assign_response({"status": "error"})[0] is False
    assert ga._classify_assign_response({"raw": '{"error":"brak uprawnień"}'})[0] is False


def test_non_dict_is_failure(ga):
    assert ga._classify_assign_response("boom")[0] is False
    assert ga._classify_assign_response(None)[0] is False


# ══════════════════════════════════════════════════════════════════════════
# verify_assignment — read-back (fetch_fn wstrzyknięty, bez HTTP)
# ══════════════════════════════════════════════════════════════════════════
def test_verify_confirms_matching_kurier(ga):
    ok, d = ga.verify_assignment("480300", 123, "csrf", fetch_fn=lambda o, c: {"id_kurier": 123})
    assert ok is True and "verify_ok" in d


def test_verify_rejects_mismatch(ga):
    ok, d = ga.verify_assignment("480300", 123, "csrf", fetch_fn=lambda o, c: {"id_kurier": 99})
    assert ok is False and "verify_mismatch" in d


def test_verify_rejects_missing_order(ga):
    assert ga.verify_assignment("480300", 123, "csrf", fetch_fn=lambda o, c: None)[0] is False


def test_verify_failclosed_on_fetch_exception(ga):
    def _boom(o, c):
        raise RuntimeError("net")
    assert ga.verify_assignment("480300", 123, "csrf", fetch_fn=_boom)[0] is False


# ══════════════════════════════════════════════════════════════════════════
# main() — BEHAWIORALNE kody wyjścia (zero HTTP)
# ══════════════════════════════════════════════════════════════════════════
def test_main_session_bounce_exits_nonzero(ga, capsys):
    rc = _run_courier(ga, assign_body={"raw": "<html>zaloguj</html>"})
    err = capsys.readouterr().err
    assert rc == 1 and "NIE potwierdzone" in err


def test_main_empty_body_exits_zero_with_sentinel(ga, capsys):
    rc = _run_courier(ga, assign_body={"raw": ""})
    out = capsys.readouterr().out
    assert rc == 0 and ga.ASSIGN_OK_SENTINEL in out


def test_main_json_success_false_exits_nonzero(ga):
    assert _run_courier(ga, assign_body={"success": False}) == 1


def test_main_verify_mismatch_exits_nonzero(ga, capsys):
    # empty body (klasyfikacja OK) ALE read-back pokazuje innego kuriera → PORAŻKA.
    rc = _run_courier(ga, assign_body={"raw": ""}, verify=True, fetch_fn=_fetch_kid(999))
    assert rc == 1 and "verify_mismatch" in capsys.readouterr().err


def test_main_verify_confirms_exits_zero(ga, capsys):
    rc = _run_courier(ga, assign_body={"raw": ""}, verify=True, fetch_fn=_fetch_kid(123))
    out = capsys.readouterr().out
    assert rc == 0 and "verify_ok" in out and ga.ASSIGN_OK_SENTINEL in out


def test_main_kurier_not_found_exits_nonzero(ga, capsys):
    rc = _run_courier(ga, assign_body={"raw": ""}, get_kid=lambda n: None)
    assert rc == 1 and "nie znaleziono kuriera" in capsys.readouterr().err


def test_main_assign_exception_exits_nonzero(ga):
    def _boom(*a):
        raise RuntimeError("panel down")
    rc = ga.main(["--id", "1", "--kurier", "X", "--time", "5"],
                 login_fn=_login_ok, assign_fn=_boom, get_kid_fn=_kid_123, fetch_fn=_fetch_kid(123))
    assert rc == 1


# ══════════════════════════════════════════════════════════════════════════
# MUTATION ×2 (C13) — odwróć fix, wynik MUSI się zepsuć
# ══════════════════════════════════════════════════════════════════════════
def test_mutation_classifier_always_true_reintroduces_false_success(ga, monkeypatch):
    # Prawdziwy klasyfikator: session-bounce → exit 1.
    real = _run_courier(ga, assign_body={"raw": "<html>zaloguj</html>"})
    assert real == 1
    # MUTACJA: klasyfikator zawsze True (usunięty fix) → ten sam błąd → exit 0.
    monkeypatch.setattr(ga, "_classify_assign_response", lambda r: (True, "MUT"))
    mut = _run_courier(ga, assign_body={"raw": "<html>zaloguj</html>"})
    assert mut == 0 and mut != real   # strażnik ma zęby


def test_mutation_empty_body_polarity_flips_twin_contract(ga, monkeypatch):
    # Prawda: empty body = sukces (chroni auto_koord) → exit 0.
    real = _run_courier(ga, assign_body={"raw": ""})
    assert real == 0
    # MUTACJA: empty→False (zła inwersja kontraktu bliźniaka) → exit 1.
    monkeypatch.setattr(ga, "_classify_assign_response", lambda r: (False, "MUT"))
    mut = _run_courier(ga, assign_body={"raw": ""})
    assert mut == 1 and mut != real


# ══════════════════════════════════════════════════════════════════════════
# PARYTET mirrora żywy↔staged (klasa stale-mirror z mapy L8)
# ══════════════════════════════════════════════════════════════════════════
_UNTOUCHED_FUNCS = ("_first_existing", "login", "get_kurier_id", "assign")


def _func_src(path, names):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out[node.name] = ast.dump(node)
    return out


def test_untouched_functions_byte_identical_live_vs_staged():
    # Funkcje spoza fixu MUSZĄ być identyczne — łapie drift (ktoś edytuje żywy
    # login()/assign() bez staged albo odwrotnie). Post-deploy trywialnie spełnione.
    live = _func_src(_LIVE, _UNTOUCHED_FUNCS)
    staged = _func_src(_STAGED, _UNTOUCHED_FUNCS)
    for fn in _UNTOUCHED_FUNCS:
        assert fn in staged, f"staged zgubił {fn}"
        assert live.get(fn) == staged.get(fn), (
            f"funkcja {fn} rozjechała się żywy↔staged — edytuj OBA razem (stale-mirror L8)")


def test_fix_present_in_staged_and_mirror_convergence():
    # Staged MUSI zawierać fix; po deployu (żywy==staged) żywy też go ma.
    staged_src = open(_STAGED, encoding="utf-8").read()
    for sig in ("_classify_assign_response", "def verify_assignment",
                "ASSIGN_OK_SENTINEL", "--verify", "def main(argv=None"):
        assert sig in staged_src, f"staged zgubił sygnaturę fixu: {sig!r}"
    live_src = open(_LIVE, encoding="utf-8").read()
    if live_src == staged_src:
        assert "_classify_assign_response" in live_src   # deployed → żywy ma fix
    else:
        # pre-deploy: żywy to jeszcze stara wersja (bez klasyfikatora) — dozwolone.
        assert "_classify_assign_response" not in live_src, (
            "żywy różni się od staged ALE ma częściowy fix → stale-mirror / częściowa zmiana")
