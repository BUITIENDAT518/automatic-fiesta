"""Exporter: appends new orders to the master XLSX file (XLSX_PATH) once a day.

The `exporter:last_run` watermark (epoch seconds) lives in Redis. Each run exports orders created
since the last run — filtering on created_at, not ingested_at, so orders re-ingested later with a
new status are never appended twice. With no watermark the first run exports everything at once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orderhub import bus
from orderhub.config import get_settings
from orderhub.report import append_xlsx
from orderhub.runtime import open_db, setup_logging, stop_event

log = logging.getLogger("orderhub.exporter")
WATERMARK_KEY = "exporter:last_run"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


async def export_once(db, r, settings) -> int:
    raw = await r.get(WATERMARK_KEY)
    since = datetime.fromtimestamp(float(raw), tz=timezone.utc) if raw else EPOCH
    rows = await db.orders_created_since(since, reveal_pii=True)
    added = append_xlsx(Path(settings.xlsx_path), rows)
    await r.set(WATERMARK_KEY, str(datetime.now(timezone.utc).timestamp()))
    return added


async def main() -> None:
    setup_logging()
    settings = get_settings()
    db = await open_db(settings)
    r = bus.connect(settings.redis_url)
    stop = stop_event()
    log.info("exporter %s started (file=%s, hour=%d)", settings.worker_id, settings.xlsx_path,
             settings.export_hour_local)
    if not await r.get(WATERMARK_KEY):
        try:
            log.info("no watermark yet: exporting all existing orders")
            log.info("appended %d orders to %s", await export_once(db, r, settings), settings.xlsx_path)
        except Exception:
            log.exception("initial export failed")
    while not stop.is_set():
        now = datetime.now(timezone.utc).astimezone()
        nxt = now.replace(hour=settings.export_hour_local % 24, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        try:
            await asyncio.wait_for(stop.wait(), timeout=(nxt - now).total_seconds())
            break
        except asyncio.TimeoutError:
            pass
        try:
            log.info("appended %d orders to %s", await export_once(db, r, settings), settings.xlsx_path)
        except Exception:
            log.exception("export failed")
    await db.close()
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
