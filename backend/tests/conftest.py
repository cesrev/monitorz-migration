"""
Pytest configuration and fixtures for billets-monitor tests.
"""

import pytest
from pathlib import Path


@pytest.fixture
def sample_dir():
    """Return path to sample HTML files."""
    return Path(__file__).parent / "samples"


@pytest.fixture
def load_sample():
    """Fixture to load sample HTML files."""
    def _load(filename: str) -> str:
        sample_path = Path(__file__).parent / "samples" / filename
        with open(sample_path, "r", encoding="utf-8") as f:
            return f.read()
    return _load
