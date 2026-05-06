"""Test that DEFAULT_CITY env override works.

Using subprocess isolation — module-level constant set raz przy import,
reload trick z sys.modules nie działa niezawodnie gdy sub-moduły trzymają
referencje. Subprocess gives clean import w fresh interpreter.
"""
import os
import subprocess
import sys


_PROBE = (
    "import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts'); "
    "from dispatch_v2 import dispatch_pipeline; "
    "print(dispatch_pipeline.DEFAULT_CITY)"
)


def _probe(env_value=None):
    env = os.environ.copy()
    env.pop("ZIOMEK_DEFAULT_CITY", None)
    if env_value is not None:
        env["ZIOMEK_DEFAULT_CITY"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"probe failed: stderr={result.stderr}"
    # Last non-empty line = our print (logging may emit before).
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    return lines[-1]


def test_default_city_default():
    """Without env var, should be 'Białystok'."""
    assert _probe(env_value=None) == "Białystok"


def test_default_city_env_override():
    """With env var set, should use that value."""
    assert _probe(env_value="Warszawa") == "Warszawa"
