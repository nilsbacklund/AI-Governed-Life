import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from timer import WakeupTimer


@pytest.fixture
def timer(tz):
    return WakeupTimer(tz)


class TestParseTime:

    def test_parse_relative_minutes(self, timer, tz):
        before = datetime.now(tz)
        result = timer.parse_time("+45m")
        after = datetime.now(tz)
        assert before + timedelta(minutes=45) <= result <= after + timedelta(minutes=45)

    def test_parse_relative_hours(self, timer, tz):
        before = datetime.now(tz)
        result = timer.parse_time("+2h")
        after = datetime.now(tz)
        assert before + timedelta(hours=2) <= result <= after + timedelta(hours=2)

    def test_parse_iso_datetime(self, timer, tz):
        result = timer.parse_time("2026-02-22T07:00:00")
        assert result.hour == 7
        assert result.minute == 0
        assert result.tzinfo == tz

    def test_parse_time_only_past_rolls_tomorrow(self, timer, tz):
        now = datetime.now(tz)
        # Use a time 1 minute in the past
        past = now - timedelta(minutes=1)
        time_str = past.strftime("%H:%M")
        result = timer.parse_time(time_str)
        assert result > now
        assert result.day != now.day or result > now


class TestAsyncWait:

    async def test_wait_fires_returns_reason(self, timer, tz):
        timer.schedule(datetime.now(tz) + timedelta(milliseconds=50), "test reason")
        assert timer.is_active()
        reason = await timer.wait()
        assert reason == "test reason"
        assert not timer.is_active()

    async def test_wait_immediate_if_past(self, timer, tz):
        timer.schedule(datetime.now(tz) - timedelta(seconds=1), "past reason")
        reason = await timer.wait()
        assert reason == "past reason"

    async def test_reschedule_mid_wait(self, timer, tz):
        timer.schedule(datetime.now(tz) + timedelta(hours=1), "far future")

        async def reschedule():
            await asyncio.sleep(0.05)
            timer.schedule(datetime.now(tz) - timedelta(seconds=1), "now reason")

        asyncio.create_task(reschedule())
        reason = await timer.wait()
        assert reason == "now reason"

    async def test_schedule_does_not_clear_on_cancel(self, timer, tz):
        timer.schedule(datetime.now(tz) + timedelta(hours=1), "long wait")
        task = asyncio.create_task(timer.wait())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Timer state should still be active after cancellation
        assert timer.is_active()
        assert timer.reason == "long wait"
