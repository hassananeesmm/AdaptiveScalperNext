import sys


def test_python_version_matches_canonical_project_interpreter():
    # Canonical project interpreter is 3.13 (pyproject.toml:
    # requires-python = ">=3.13,<3.14"). Deliberately excludes the
    # machine's global Python 3.14 — running this suite under it should
    # fail loudly here rather than silently exercise an untested
    # interpreter.
    assert (3, 13) <= sys.version_info < (3, 14), (
        f"running under Python {sys.version_info[0]}.{sys.version_info[1]}, "
        f"expected 3.13.x — use .venv\\Scripts\\python.exe, not the global interpreter"
    )
