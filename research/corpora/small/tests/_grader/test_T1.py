"""
Grader for Task T1 (code-fix: remove hardcoded JWT secret default).

Objective binary check via source inspection + minimal runtime check:
  1. settings.py source no longer contains the hardcoded default value
  2. load_settings() raises when JWT_SECRET is unset
  3. load_settings() succeeds when JWT_SECRET is set

Run with: cd <project_root> && python -m pytest tests/_grader/test_T1.py -v
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
SETTINGS_PATH = PROJECT_ROOT / "src" / "settings.py"


def _read_settings_source() -> str:
    return SETTINGS_PATH.read_text()


def _run_load_settings_subprocess(env):
    """Run load_settings() in a fresh subprocess and capture the result.
    Returns (returncode, stdout, stderr).
    """
    code = textwrap.dedent('''
        import sys
        sys.path.insert(0, ROOT + "/src")
        try:
            import settings
            s = settings.load_settings()
            print("RESULT_OK:", s.jwt_secret)
        except Exception as e:
            print("RESULT_RAISED:", type(e).__name__, str(e))
    ''').replace("ROOT", repr(str(PROJECT_ROOT)))
    full_env = {k: v for k, v in os.environ.items() if k != "JWT_SECRET"}
    full_env.update(env)
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=full_env,
        cwd=str(PROJECT_ROOT), timeout=15,
    )
    return r.returncode, r.stdout, r.stderr


def test_hardcoded_default_removed_from_source():
    """The 'dev-secret-do-not-use-in-prod' string must not be a default value."""
    src = _read_settings_source()
    bad_patterns = [
        re.compile(r'os\.environ\.get\s*\(\s*["\']JWT_SECRET["\']\s*,\s*["\']dev-secret-do-not-use-in-prod["\']\s*\)'),
    ]
    for pat in bad_patterns:
        m = pat.search(src)
        assert m is None, (
            f"settings.py still has the hardcoded JWT secret default.\n"
            f"Match: {m.group() if m else None}\n\n"
            f"Fix: remove the second argument to os.environ.get('JWT_SECRET', ...), "
            f"and raise an exception if the result is None or empty."
        )


def test_load_settings_raises_when_jwt_secret_unset():
    """load_settings() must raise if JWT_SECRET env var is unset."""
    rc, out, err = _run_load_settings_subprocess({})
    assert "RESULT_RAISED" in out, (
        f"load_settings() did not raise when JWT_SECRET was unset.\n"
        f"rc={rc}, stdout={out!r}, stderr={err!r}\n\n"
        f"It must raise a RuntimeError (or similar) so the application fails "
        f"fast at startup if the operator forgot to set JWT_SECRET."
    )


def test_load_settings_raises_when_jwt_secret_empty():
    """load_settings() must raise if JWT_SECRET is an empty string."""
    rc, out, err = _run_load_settings_subprocess({"JWT_SECRET": ""})
    assert "RESULT_RAISED" in out, (
        f"load_settings() did not raise when JWT_SECRET was empty string.\n"
        f"rc={rc}, stdout={out!r}, stderr={err!r}"
    )


def test_load_settings_succeeds_with_proper_secret():
    """load_settings() should return a Settings when JWT_SECRET is set."""
    rc, out, err = _run_load_settings_subprocess(
        {"JWT_SECRET": "a-proper-test-secret-32-chars-long-x"}
    )
    assert "RESULT_OK" in out, (
        f"load_settings() failed when JWT_SECRET was properly set.\n"
        f"rc={rc}, stdout={out!r}, stderr={err!r}"
    )
