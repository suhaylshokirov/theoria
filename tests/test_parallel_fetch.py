"""etl.parallel_fetch: the thread-pooled per-id Bronze fetch loop."""

import logging
import threading
import time

import config
from etl.parallel_fetch import fetch_each


def test_results_keep_the_input_order_whatever_order_threads_finish_in():
    def fetch_one(item_id):
        time.sleep(0.01 * (5 - item_id))  # the first ids finish last
        if item_id in (2, 4):
            raise RuntimeError("500")

    succeeded, failed = fetch_each([0, 1, 2, 3, 4], fetch_one, label="movie_id", max_workers=5)

    assert succeeded == [0, 1, 3]
    assert failed == [2, 4]


def test_ids_really_run_concurrently():
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def fetch_one(_item_id):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1

    fetch_each(range(8), fetch_one, label="movie_id", max_workers=4)

    assert peak == 4


def test_a_failure_is_logged_with_its_id_and_never_raised(caplog):
    def fetch_one(item_id):
        raise RuntimeError("TMDB 500")

    with caplog.at_level(logging.ERROR):
        assert fetch_each([550], fetch_one, label="movie_id", max_workers=4) == ([], [550])
    assert "movie_id=550 failed, skipping: TMDB 500" in caplog.text


def test_worker_count_defaults_to_config(monkeypatch):
    monkeypatch.setattr(config, "TMDB_FETCH_WORKERS", 3)
    seen = set()

    def fetch_one(_item_id):
        seen.add(threading.current_thread().name)
        time.sleep(0.02)

    fetch_each(range(9), fetch_one, label="movie_id")

    assert len(seen) == 3


def test_one_worker_runs_inline_on_the_calling_thread():
    caller = threading.current_thread().name
    seen = set()

    fetch_each(range(3), lambda _i: seen.add(threading.current_thread().name),
               label="movie_id", max_workers=1)

    assert seen == {caller}
