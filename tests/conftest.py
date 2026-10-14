from pathlib import Path

import pytest

from llm_updates.config import load
from llm_updates.storage import Repository

ROOT = Path(__file__).resolve().parents[1]
SINCE = "2026-10-07T00:00:00+00:00"
UNTIL = "2026-10-14T00:00:00+00:00"


@pytest.fixture
def config(tmp_path):
    result = load(ROOT / "config.example.toml")
    result.database = str(tmp_path / "updates.sqlite3")
    result.output_dir = str(tmp_path / "digests")
    return result


@pytest.fixture
def repo(config):
    result = Repository(config.database)
    result.configure(config)
    yield result
    result.close()


@pytest.fixture
def fixture_path():
    return ROOT / "fixtures/demo.jsonl"
