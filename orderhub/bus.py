from __future__ import annotations

import redis.asyncio as aioredis

from orderhub.config import PROCESSOR_GROUP, STREAM_INGEST


def connect(url: str) -> aioredis.Redis:
    return aioredis.from_url(url, decode_responses=True)


async def ensure_group(r: aioredis.Redis, stream: str = STREAM_INGEST, group: str = PROCESSOR_GROUP) -> None:
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as e:  # BUSYGROUP: already exists
        if "BUSYGROUP" not in str(e):
            raise


_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class Lease:
    """Per-shop distributed lease so each shop is polled by exactly one collector replica at a time."""

    def __init__(self, r: aioredis.Redis, owner: str, ttl_ms: int):
        self.r, self.owner, self.ttl_ms = r, owner, ttl_ms

    @staticmethod
    def key(shop_key: str) -> str:
        return f"lease:shop:{shop_key}"

    async def acquire(self, shop_key: str) -> bool:
        k = self.key(shop_key)
        if await self.r.set(k, self.owner, nx=True, px=self.ttl_ms):
            return True
        return bool(await self.r.eval(_RENEW, 1, k, self.owner, self.ttl_ms))

    async def release(self, shop_key: str) -> None:
        await self.r.eval(_RELEASE, 1, self.key(shop_key), self.owner)
