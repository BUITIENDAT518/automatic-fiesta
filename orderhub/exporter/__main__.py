"""Exporter: appends new orders to the master XLSX file (XLSX_PATH) once a day.

The `exporter:last_run` watermark (epoch seconds) lives in Redis. Each run selects orders whose
ingested_at is newer than the watermark minus poll_overlap_s, so orders committed late (collector
backlog, a newly added shop's backfill) are still caught; append_xlsx skips rows already in the
file, which also makes a retried export idempotent. If the workbook is missing while a watermark
exists, the run rebuilds it from scratch. With no watermark the first run exports everything.
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
    path = Path(settings.xlsx_path)
    raw = await r.get(WATERMARK_KEY)
    # A missing workbook must be rebuilt from scratch; the watermark alone cannot restore its rows.
    since = (datetime.fromtimestamp(float(raw), tz=timezone.utc) - timedelta(seconds=settings.poll_overlap_s)
             if raw and path.exists() else EPOCH)
    started = datetime.now(timezone.utc)
    rows = await db.orders_ingested_since(since, reveal_pii=True)
    added = append_xlsx(path, rows)
    await r.set(WATERMARK_KEY, str(started.timestamp()))
    return added


async def main() -> None:
    setup_logging()
    settings = get_settings()
    db = await open_db(settings)
    r = bus.connect(settings.redis_url)
    stop = stop_event()
    log.info("exporter %s started (file=%s, hour=%d)", settings.worker_id, settings.xlsx_path,
             settings.export_hour_local)
    if not await r.get(WATERMARK_KEY) or not Path(settings.xlsx_path).exists():
        try:
            log.info("no watermark or workbook missing: exporting all existing orders")
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
