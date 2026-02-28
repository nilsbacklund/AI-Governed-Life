import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class WakeupTimer:
    def __init__(self, tz: ZoneInfo):
        self._tz = tz
        self._event = asyncio.Event()
        self._wakeup_time: datetime | None = None
        self._reason: str = ""

    def is_active(self) -> bool:
        return self._wakeup_time is not None

    @property
    def wakeup_time(self) -> datetime | None:
        return self._wakeup_time

    @property
    def reason(self) -> str:
        return self._reason

    def schedule(self, time: datetime, reason: str):
        self._wakeup_time = time
        self._reason = reason
        self._event.set()

    def parse_time(self, time_str: str) -> datetime:
        now = datetime.now(self._tz)

        # Relative: "+45m", "+2h"
        rel = re.match(r"^\+(\d+)([mh])$", time_str)
        if rel:
            amount, unit = int(rel.group(1)), rel.group(2)
            delta = timedelta(minutes=amount) if unit == "m" else timedelta(hours=amount)
            return now + delta

        # Full ISO datetime: "2026-02-22T07:00:00"
        if "T" in time_str and len(time_str) > 8:
            dt = datetime.fromisoformat(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self._tz)
            return dt

        # Time-only: "14:30"
        parts = time_str.split(":")
        target = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    async def wait(self) -> str:
        while True:
            self._event.clear()
            if self._wakeup_time is None:
                await self._event.wait()
                continue

            delay = (self._wakeup_time - datetime.now(self._tz)).total_seconds()
            if delay <= 0:
                reason = self._reason
                self._wakeup_time = None
                return reason

            try:
                await asyncio.wait_for(self._event.wait(), timeout=delay)
                # event was set → timer was rescheduled, loop again
            except asyncio.TimeoutError:
                reason = self._reason
                self._wakeup_time = None
                return reason
