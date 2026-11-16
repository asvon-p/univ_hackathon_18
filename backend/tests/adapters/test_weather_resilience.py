import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from backend.app.adapters import _timeout
from backend.tests.adapters.test_weather import engine, response_body


def wait_for(predicate):
    end = time.monotonic() + 2
    while not predicate() and time.monotonic() < end:
        time.sleep(0.005)
    assert predicate()


def test_weather_runs_while_all_ai_workers_are_busy():
    release = threading.Event()
    started = threading.Barrier(9)

    def busy():
        started.wait(timeout=3)
        release.wait(timeout=3)

    futures = [_timeout._POOL.submit(busy) for _ in range(8)]
    try:
        started.wait(timeout=3)
        weather = engine(lambda _: httpx.Response(200, json=response_body()))
        assert weather.current(37, 127).temperature_c == 8.5
    finally:
        release.set()
        for future in futures:
            future.result(timeout=3)


def test_twenty_simultaneous_users_share_one_lookup():
    calls = []
    ready = threading.Barrier(20)

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0.15)
        return httpx.Response(200, json=response_body())

    weather = engine(respond)

    def lookup(_):
        ready.wait(timeout=3)
        return weather.current(37.5665, 126.978)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lookup, range(20)))
    assert all(r.temperature_c == 8.5 for r in results)
    assert len(calls) == 1
    assert not weather._inflight


def test_network_is_cancelled_and_slots_released_after_deadline():
    cancelled = threading.Event()
    calls = []

    async def respond(request):
        calls.append(request)
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    weather = engine(respond, WEATHER_TIMEOUT_SEC=0.05)
    assert weather.current(37, 127) is None
    wait_for(cancelled.is_set)
    wait_for(lambda: not weather._inflight)
    assert not weather._cache
    assert weather.current(37, 127) is None
    assert len(calls) == 1


def test_capacity_is_bounded_without_an_unbounded_queue():
    calls = []
    release = threading.Event()

    async def respond(request):
        calls.append(request)
        while not release.is_set():
            await asyncio.sleep(0.005)
        return httpx.Response(200, json=response_body())

    weather = engine(respond)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(weather.current, 30 + i, 127) for i in range(4)]
        try:
            wait_for(lambda: len(calls) == 4)
            for i in range(20):
                assert weather.current(-i, 127) is None
            assert len(weather._inflight) == len(calls) == 4
        finally:
            release.set()
        assert all(f.result(timeout=2).temperature_c == 8.5 for f in futures)
    assert not weather._inflight


def test_stale_reading_is_labelled_same_location_bounded_and_retry_recovers():
    tick = [0.0]
    failing = [False]
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503) if failing[0] else httpx.Response(200, json=response_body())

    weather = engine(respond, WEATHER_CACHE_TTL_SEC=10, WEATHER_STALE_MAX_AGE_SEC=60)
    weather.clock = lambda: tick[0]
    fresh = weather.current(37, 127)
    failing[0], tick[0] = True, 11
    stale = weather.current(37, 127)
    assert stale.is_stale and stale.is_fallback and stale.age_seconds == 11
    assert stale.fetched_at == fresh.fetched_at and stale.weather_time == fresh.weather_time
    assert stale.notice and stale.retry_after_sec == 3
    assert weather.current(35, 129) is None
    tick[0] = 61
    assert weather.current(37, 127) is None
    failing[0], tick[0] = False, 65
    assert not weather.current(37, 127).is_stale


def test_late_blocking_transport_cannot_publish_after_deadline():
    def respond(request):
        time.sleep(0.08)
        return httpx.Response(200, json=response_body())

    weather = engine(respond, WEATHER_TIMEOUT_SEC=0.02)
    assert weather.current(37, 127) is None
    wait_for(lambda: not weather._inflight)
    assert not weather._cache
