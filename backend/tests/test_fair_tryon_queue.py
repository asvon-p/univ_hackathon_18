from queue import Empty, Full
from types import SimpleNamespace

import pytest

from tryon_server.fair_queue import FairJobQueue


def job(owner, number):
    return SimpleNamespace(owner_id=owner, id=f"{owner}-{number}")


def test_round_robin_and_positions_keep_each_users_fifo():
    queue = FairJobQueue(maxsize=8)
    for item in [job("a", 1), job("a", 2), job("a", 3), job("b", 1), job("b", 2), job("c", 1)]:
        queue.put_nowait(item)
    expected = ["a-1", "b-1", "c-1", "a-2", "b-2", "a-3"]
    assert [queue.position(key) for key in expected] == list(range(1, 7))
    assert [queue.get_nowait().id for _ in expected] == expected
    for _ in expected:
        queue.task_done()
    assert queue.unfinished_tasks == 0


def test_new_user_gets_turn_before_last_users_next_garment():
    queue = FairJobQueue(maxsize=8)
    queue.put_nowait(job("a", 1))
    queue.put_nowait(job("a", 2))
    assert queue.get_nowait().id == "a-1"
    queue.put_nowait(job("b", 1))
    assert queue.get_nowait().id == "b-1"
    queue.put_nowait(job("b", 2))
    assert queue.get_nowait().id == "a-2"
    assert queue.get_nowait().id == "b-2"


def test_capacity_cancellation_and_missing_job():
    queue = FairJobQueue(maxsize=2)
    queue.put_nowait(job("a", 1))
    queue.put_nowait(job("b", 1))
    with pytest.raises(Full):
        queue.put_nowait(job("c", 1))
    assert queue.discard("a-1") and not queue.discard("missing")
    assert queue.position("a-1") is None
    queue.put_nowait(job("c", 1))
    assert [queue.get_nowait().id for _ in range(2)] == ["b-1", "c-1"]
    queue.task_done()
    queue.task_done()
    assert queue.unfinished_tasks == 0
    with pytest.raises(Empty):
        queue.get_nowait()
