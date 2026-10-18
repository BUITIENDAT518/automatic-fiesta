"""Collector: logs in to each shop and streams orders from the last WINDOW_HOURS onward into Redis Streams.

Run any number of replicas; a per-shop Redis lease guarantees each shop is polled by one replica at a time,
and a dead replica's shops are picked up by the others once its lease expires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from orderhub import bus
from orderhub.config import STREAM_INGEST, get_settings
from orderhub.connectors import ConnectorError, build_connector
from orderhub.models import ORDER_PLATFORMS, Shop
from orderhub.pool import AccountPool
from orderhub.runtime import open_db, setup_logging, stop_event

log = logging.getLogger("orderhub.collector")
POKE_KEY = "collector:poke"


def cursor_key(shop: Shop) -> str:
    return f"cursor:{shop.key}"


async def poll_shop(shop: Shop, settings, db, r, http) -> int:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=settings.window_hours)
    raw = await r.get(cursor_key(shop))
    since = datetime.fromtimestamp(float(raw), tz=timezone.utc) - timedelta(seconds=settings.poll_overlap_s) if raw else window_start
    since = max(since, window_start)
    conn = build_connector(shop, settings, http)
    await conn.login()
    if conn.credentials_changed:
        await db.save_credentials(shop)
    n = 0
    async for order in conn.fetch_orders(since, now):
        await r.xadd(STREAM_INGEST, {"order": order.model_dump_json()}, maxlen=200_000, approximate=True)
        n += 1
    await r.set(cursor_key(shop), now.timestamp())
    return n


async def main() -> None:
    setup_logging()
    settings = get_settings()
    db = await open_db(settings)
    r = bus.connect(settings.redis_url)
    pool = AccountPool(r)
    lease = bus.Lease(r, settings.worker_id, ttl_ms=max(settings.poll_interval_s * 3, 30) * 1000)
    sem = asyncio.Semaphore(settings.max_concurrent_shops)
    next_due: dict[str, float] = {}
    running: dict[str, asyncio.Task] = {}
    owned: set[str] = set()
    stop = stop_event()
    log.info("collector %s started", settings.worker_id)

    async with httpx.AsyncClient(timeout=30) as http:

        async def run(shop: Shop) -> None:
            async with sem:
                try:
                    n = await poll_shop(shop, settings, db, r, http)
                    await pool.report_ok(shop.key, n, settings.worker_id)
                    if n:
                        log.info("%s: %d orders", shop.key, n)
                except (ConnectorError, httpx.HTTPError, KeyError, ValueError) as e:
                    cooldown = await pool.report_failure(shop.key, f"{type(e).__name__}: {e}", settings.worker_id)
                    log.warning("%s: poll failed (cooldown %ds): %s", shop.key, cooldown, e)

        shops: list[Shop] = []
        last_refresh = 0.0
        while not stop.is_set():
            if time.time() - last_refresh > 30:
                shops = [s for s in await db.list_shops(with_credentials=True) if s.platform in ORDER_PLATFORMS]
                last_refresh = time.time()
            poked = set(await r.smembers(POKE_KEY))
            for shop in shops:
                k = shop.key
                if not await lease.acquire(k):
                    owned.discard(k)
                    continue
                owned.add(k)
                if k in poked:
                    await r.srem(POKE_KEY, k)
                    next_due[k] = 0
                    await pool.reset(k)
                due = time.time() >= next_due.get(k, 0) and await pool.available(k)
                if due and (k not in running or running[k].done()):
                    next_due[k] = time.time() + settings.poll_interval_s
                    running[k] = asyncio.create_task(run(shop))
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

        await asyncio.gather(*running.values(), return_exceptions=True)
        for k in owned:
            await lease.release(k)
    await db.close()
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
