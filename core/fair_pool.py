import asyncio
import math
from contextlib import asynccontextmanager


class FairPool:
    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._total = 0
        self._inflight: dict = {}
        self._waiting: dict = {}
        self._cond = asyncio.Condition()

    def _demanding(self) -> int:
        runs = set(self._inflight) | set(self._waiting)
        return sum(
            1 for r in runs if self._inflight.get(r, 0) + self._waiting.get(r, 0) > 0
        )

    def _fair_share(self) -> int:
        return max(1, math.ceil(self.capacity / (self._demanding() or 1)))

    def _others_waiting(self, run_id) -> bool:
        return any(cnt > 0 for r, cnt in self._waiting.items() if r != run_id)

    def _can_grant(self, run_id) -> bool:
        if self._total >= self.capacity:
            return False
        return self._inflight.get(
            run_id, 0
        ) < self._fair_share() or not self._others_waiting(run_id)

    @asynccontextmanager
    async def slot(self, run_id):
        async with self._cond:
            self._waiting[run_id] = self._waiting.get(run_id, 0) + 1
            self._cond.notify_all()  # спрос изменился — другие пересчитают долю
            try:
                while not self._can_grant(run_id):
                    await self._cond.wait()
            finally:
                self._waiting[run_id] -= 1
                if self._waiting[run_id] <= 0:
                    self._waiting.pop(run_id, None)
                self._cond.notify_all()  # на случай отмены во время ожидания
            self._total += 1
            self._inflight[run_id] = self._inflight.get(run_id, 0) + 1
        try:
            yield
        finally:
            async with self._cond:
                self._inflight[run_id] -= 1
                if self._inflight[run_id] <= 0:
                    self._inflight.pop(run_id, None)
                self._total -= 1
                self._cond.notify_all()

    def snapshot(self) -> dict:
        return {
            "total": self._total,
            "capacity": self.capacity,
            "inflight": dict(self._inflight),
            "fair_share": self._fair_share(),
        }
