"""Tracker: refreshes shipment status of recent orders shipped by GHTK/GHN using the carrier account pool.

Each tracking number is tried against the pool's healthy accounts (round-robin); the account that owns it is
remembered in Redis. Failing accounts go into cooldown. Runs as one or more replicas (per-carrier lease).
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from orderhub import bus
from orderhub.carriers import CarrierError, NotFound, build_carrier, carrier_platform
from orderhub.config import CHANNEL_UPDATES, get_settings
from orderhub.models import CARRIER_PLATFORMS, Shop
from orderhub.pool import AccountPool
from orderhub.runtime import open_db, setup_logging, stop_event

log = logging.getLogger("orderhub.tracker")
POKE_KEY = "tracker:poke"
OWNER_TTL_S = 7 * 86400


async def track_one(order: dict, accounts: list[Shop], pool: AccountPool, r, http, settings) -> dict | None:
    carrier = carrier_platform(order["carrier"])
    tn = order["tracking_number"]
    by_key = {a.key: a for a in accounts if a.platform == carrier}
    if not by_key:
        return None
    owner = await r.get(f"track:owner:{tn}")
    order_keys = [owner] if owner in by_key and await pool.available(owner) else []
    order_keys += [k for k in await pool.rotation(carrier.value, list(by_key)) if k not in order_keys]
    for k in order_keys:
        try:
            res = await build_carrier(by_key[k], settings, http).track(tn)
        except NotFound:
            continue
        except (CarrierError, httpx.HTTPError, ValueError) as e:
            await pool.report_failure(k, str(e), settings.worker_id)
            continue
        await pool.report_ok(k, worker=settings.worker_id)
        await r.set(f"track:owner:{tn}", k, ex=OWNER_TTL_S)
        return res.model_dump(mode="json")
    return None


async def run_cycle(db, r, pool, http, settings, only: set[str] | None = None) -> int:
    accounts = [a for a in await db.list_shops(with_credentials=True) if a.platform in CARRIER_PLATFORMS]
    if not accounts:
        return 0
    orders = await db.orders_to_track(settings.window_hours)
    if only:
        orders = [o for o in orders if o["tracking_number"] in only]
    n = 0
    sem = asyncio.Semaphore(8)

    async def handle(o: dict) -> None:
        nonlocal n
        async with sem:
            res = await track_one(o, accounts, pool, r, http, settings)
        if res and res["logistics_status"] != o["logistics_status"]:
            await db.update_logistics(o["platform"], o["shop_id"], o["order_id"], res["logistics_status"], res["status"])
            await r.publish(CHANNEL_UPDATES, json.dumps({
                "event": "updated", "platform": o["platform"], "shop_id": o["shop_id"], "order_id": o["order_id"],
                "status": res["status"], "logistics_status": res["logistics_status"], "carrier": o["carrier"],
                "tracking_number": o["tracking_number"]}, ensure_ascii=False))
            n += 1

    await asyncio.gather(*(handle(o) for o in orders if carrier_platform(o["carrier"]) in CARRIER_PLATFORMS))
    return n


async def main() -> None:
    setup_logging()
    settings = get_settings()
    db = await open_db(settings)
    r = bus.connect(settings.redis_url)
    pool = AccountPool(r)
    lease = bus.Lease(r, settings.worker_id, ttl_ms=max(settings.track_interval_s * 3, 60) * 1000)
    stop = stop_event()
    log.info("tracker %s started", settings.worker_id)
    loop = asyncio.get_running_loop()
    next_full = 0.0
    async with httpx.AsyncClient(timeout=20) as http:
        while not stop.is_set():
            if await lease.acquire("tracker"):
                poked = set(await r.smembers(POKE_KEY))
                if poked:
                    await r.srem(POKE_KEY, *poked)
                if loop.time() >= next_full or poked:
                    try:
                        n = await run_cycle(db, r, pool, http, settings, None if loop.time() >= next_full else poked)
                        if n:
                            log.info("updated %d shipments", n)
                    except Exception:
                        log.exception("tracking cycle failed")
                    if loop.time() >= next_full:
                        next_full = loop.time() + settings.track_interval_s
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        await lease.release("tracker")
    await db.close()
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())

