"""Shared pytest fixtures and a loader for the (hyphenated) target module."""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / 'rss-podcast-downloader.py'
MODULE_NAME = 'rpd'  # module has hyphens in its filename, so import by a stable alias


@pytest.fixture(scope='session')
def mod():
    """Return the target module, importing it exactly once per session."""
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[MODULE_NAME] = module
    return module
