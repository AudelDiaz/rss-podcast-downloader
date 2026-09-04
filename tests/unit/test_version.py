"""Unit tests for versioning."""

import pathlib
import re


def test_version_exists_and_semver(mod):
    assert hasattr(mod, '__version__')
    assert re.match(r'^\d+\.\d+\.\d+', mod.__version__)


def test_version_matches_pyproject(mod):
    pyproject = pathlib.Path('pyproject.toml').read_text(encoding='utf-8')
    m = re.search(r'version\s*=\s*"([^"]+)"', pyproject)
    assert m, 'version not found in pyproject.toml'
    assert m.group(1) == mod.__version__


def test_version_flag(mod, capsys):
    import sys

    # Simulate --version via argparse SystemExit
    orig = sys.argv[:]
    try:
        sys.argv = ['rss-podcast-downloader.py', '--version']
        try:
            mod.main()
        except SystemExit as e:
            assert e.code == 0
        out = capsys.readouterr().out
        assert mod.__version__ in out
    finally:
        sys.argv = orig


def test_shim_version_matches(mod):
    import importlib

    shim = importlib.import_module('rss_podcast_downloader')
    assert shim.__version__ == mod.__version__
