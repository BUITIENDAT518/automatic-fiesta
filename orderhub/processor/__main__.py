"""Processor: consumes the ingest stream (consumer group), enriches region, upserts Postgres, fans out updates.

Scale horizontally: replicas share the consumer group; stuck messages from dead replicas are reclaimed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from orderhub import bus
from orderhub.config import CHANNEL_UPDATES, PROCESSOR_GROUP, STREAM_DLQ, STREAM_INGEST, get_settings
from orderhub.crypto import mask_name
from orderhub.models import NormalizedOrder
from orderhub.regions import resolve
from orderhub.runtime import open_db, setup_logging, stop_event

log = logging.getLogger("orderhub.processor")
MAX_DELIVERIES = 5


def enrich(order: NormalizedOrder) -> NormalizedOrder:
    s = order.shipping
    info = resolve(s.province, s.district, address=s.address)
    if info:
        s.province, s.province_legacy, s.region, s.subregion = info.province, info.province_legacy, info.region, info.subregion
    return order


def summary(order: NormalizedOrder, inserted: bool) -> str:
    return json.dumps({
        "event": "created" if inserted else "updated",
        "platform": order.platform.value, "shop_id": order.shop_id, "shop_name": order.shop_name,
        "order_id": order.order_id, "status": order.status.value, "total_amount": float(order.total_amount),
        "created_at": order.created_at.isoformat(), "updated_at": order.updated_at.isoformat(),
        "province": order.shipping.province, "region": order.shipping.region, "carrier": order.shipping.carrier,
        "tracking_number": order.shipping.tracking_number, "customer": mask_name(order.customer.name),
    }, ensure_ascii=False)


async def main() -> None:
    setup_logging()
    settings = get_settings()
    db = await open_db(settings)
    r = bus.connect(settings.redis_url)
    await bus.ensure_group(r)
    consumer = settings.worker_id
    stop = stop_event()
    last_purge = 0.0
    log.info("processor %s started", consumer)

    async def handle(msg_id: str, fields: dict) -> None:
        try:
            order = enrich(NormalizedOrder.model_validate_json(fields["order"]))
            inserted = await db.upsert_order(order)
            if inserted is not None:
                await r.publish(CHANNEL_UPDATES, summary(order, inserted))
            await r.xack(STREAM_INGEST, PROCESSOR_GROUP, msg_id)
        except Exception:
            log.exception("failed to process %s", msg_id)
            pending = await r.xpending_range(STREAM_INGEST, PROCESSOR_GROUP, min=msg_id, max=msg_id, count=1)
            if pending and pending[0]["times_delivered"] >= MAX_DELIVERIES:
                await r.xadd(STREAM_DLQ, fields)
                await r.xack(STREAM_INGEST, PROCESSOR_GROUP, msg_id)

    while not stop.is_set():
        _, claimed, _ = await r.xautoclaim(STREAM_INGEST, PROCESSOR_GROUP, consumer, min_idle_time=60_000, count=100)
        for msg_id, fields in claimed:
            await handle(msg_id, fields)
        resp = await r.xreadgroup(PROCESSOR_GROUP, consumer, {STREAM_INGEST: ">"}, count=200, block=2000)
        for _, messages in resp or []:
            for msg_id, fields in messages:
                await handle(msg_id, fields)
        if time.time() - last_purge > 600:
            purged = await db.purge_older_than(settings.retention_hours)
            if purged:
                log.info("purged %d orders older than %dh", purged, settings.retention_hours)
            last_purge = time.time()

    await db.close()
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
