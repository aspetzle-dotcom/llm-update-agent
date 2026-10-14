import time

import pytest
from test_collectors_network import ScriptedFetcher
from test_research import ready

from llm_updates.locking import database_lock
from llm_updates.network import Fetcher, FetchError, Response
from llm_updates.research import Budget, LimitReached, totals


def test_database_lock_serializes_writers(config):
    with database_lock(config.database):
        with pytest.raises(ValueError, match="another process"), database_lock(config.database):
            pass
    with database_lock(config.database):
        pass


def test_monthly_cap_includes_another_runs_uncertain_reservation(repo, config, fixture_path):
    rid, _ = ready(repo, config, fixture_path)
    budget = Budget(repo, rid, config, time.monotonic() + 30)
    budget.reserve({"max_output_tokens": 100})
    config.limits.month_usd = totals(repo, rid)["accounted_usd"]
    other = repo.start(config, {"different": True})
    with pytest.raises(LimitReached, match="monthly_cost_limit"):
        Budget(repo, other["run_id"], config, time.monotonic() + 30).reserve(
            {"max_output_tokens": 100}
        )


def test_repeated_429_stops_after_three_attempts(repo, config):
    s = config.sources[2]
    f = ScriptedFetcher(config, repo, [Response(429, {"retry-after": "0"}, b"", s.endpoint)] * 3)
    with pytest.raises(FetchError, match="rate_limited"):
        f.get(s.endpoint, s)
    assert len(f.urls) == 3


def test_response_byte_limit(repo, config):
    config.limits.response_bytes = 1024

    class Body:
        status = 200

        def getheaders(self):
            return []

        def read1(self, n):
            return b"x" * n

    class Connection:
        sock = None

        def __init__(self, *args):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Body()

        def close(self):
            pass

    import socket

    f = Fetcher(
        config,
        repo,
        time.monotonic() + 30,
        resolver=lambda *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.5", 443))],
        connection=Connection,
    )
    with pytest.raises(FetchError, match="response_too_large"):
        f.get(config.sources[2].endpoint, config.sources[2])
