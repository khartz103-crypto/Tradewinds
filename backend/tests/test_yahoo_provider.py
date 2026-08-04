"""Tests for Yahoo Finance throttling and retry behavior."""

import asyncio

import pytest

from app.providers.yahoo import YahooProvider, YahooRateLimitError


@pytest.mark.asyncio
async def test_request_retries_rate_limit_with_exponential_backoff(monkeypatch):
    provider = YahooProvider()
    calls = 0
    delays = []

    def request():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("Too Many Requests")
        return "ok"

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    assert await provider._request(request) == "ok"
    assert calls == 3
    assert delays == [2.0, 4.0]


@pytest.mark.asyncio
async def test_request_raises_clear_error_after_rate_limit_retries(monkeypatch):
    provider = YahooProvider()
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(YahooRateLimitError, match="persisted after 3 retries"):
        await provider._request(lambda: (_ for _ in ()).throw(RuntimeError("HTTP 429")))
    assert delays == [2.0, 4.0, 8.0]
