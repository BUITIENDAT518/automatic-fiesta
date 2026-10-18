"""Account pool: health tracking, cooldown/backoff and round-robin selection for platform accounts.

Accounts live in the `shops` table (credentials encrypted); their runtime health lives in Redis so every
collector/tracker replica sees the same state.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis

BASE_COOLDOWN_S = 60
MAX_COOLDOWN_S = 3600
ERROR_AFTER = 5


def health_key(account_key: str) -> str:
    return f"pool:health:{account_key}"


class AccountPool:
    def __init__(self, r: aioredis.Redis):
        self.r = r

    async def health(self, account_key: str) -> dict:
        h = await self.r.hgetall(health_key(account_key))
        fails = int(h.get("fails", 0))
        cooldown_until = float(h.get("cooldown_until", 0))
        if fails == 0:
            state = "ok" if h.get("last_ok") else "new"
        elif fails >= ERROR_AFTER:
            state = "error"
        elif cooldown_until > time.time():
            state = "cooldown"
        else:
            state = "retry"
        return {"state": state, "fails": fails, "cooldown_until": cooldown_until or None,
                "last_ok": float(h["last_ok"]) if h.get("last_ok") else None, "last_error": h.get("last_error"),
                "last_count": int(h["last_count"]) if h.get("last_count") else None, "worker": h.get("worker")}

    async def available(self, account_key: str) -> bool:
        return float(await self.r.hget(health_key(account_key), "cooldown_until") or 0) <= time.time()

    async def report_ok(self, account_key: str, count: int | None = None, worker: str | None = None) -> None:
        fields = {"fails": 0, "cooldown_until": 0, "last_ok": time.time(), "last_error": ""}
        if count is not None:
            fields["last_count"] = count
        if worker:
            fields["worker"] = worker
        await self.r.hset(health_key(account_key), mapping=fields)

    async def report_failure(self, account_key: str, error: str, worker: str | None = None) -> float:
        """Exponential backoff: 1, 2, 4 … min (capped at 1h). Returns the cooldown in seconds."""
        k = health_key(account_key)
        fails = await self.r.hincrby(k, "fails", 1)
        cooldown = min(MAX_COOLDOWN_S, BASE_COOLDOWN_S * 2 ** (fails - 1))
        await self.r.hset(k, mapping={"cooldown_until": time.time() + cooldown, "last_error": error[:300],
                                      **({"worker": worker} if worker else {})})
        return cooldown

    async def reset(self, account_key: str) -> None:
        await self.r.delete(health_key(account_key))

    async def rotation(self, group: str, keys: list[str]) -> list[str]:
        """Healthy accounts first, rotated round-robin per call; accounts in cooldown are excluded."""
        ready = [k for k in keys if await self.available(k)]
        if not ready:
            return []
        start = await self.r.incr(f"pool:rr:{group}") % len(ready)
        return ready[start:] + ready[:start]
