import sys


def test_python_version_is_311_or_newer():
    assert sys.version_info >= (3, 11)
