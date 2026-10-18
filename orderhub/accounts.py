"""Shared account operations used by the API and the Telegram bot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from orderhub.carriers import CarrierError, LoginOnlyCarrier, NotFound, build_carrier
from orderhub.connectors import ConnectorError, build_connector
from orderhub.models import CARRIER_PLATFORMS, Shop
from orderhub.pool import AccountPool

POKE_KEY = "collector:poke"


async def check_account(acc: Shop, settings, db, pool: AccountPool, tracking_number: str | None = None) -> dict:
    """Live login test: order accounts fetch the last hour of orders; carrier accounts log in or track one code."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            if acc.platform in CARRIER_PLATFORMS:
                client = build_carrier(acc, settings, http)
                if isinstance(client, LoginOnlyCarrier):
                    await client.login()
                    detail: dict = {"login": "ok"}
                elif not tracking_number:
                    return {"ok": None, "error": "cần một mã vận đơn để kiểm tra tài khoản vận chuyển này"}
                else:
                    try:
                        detail = (await client.track(tracking_number)).model_dump(mode="json")
                    except NotFound as e:
                        detail = {"tracking_number": tracking_number, "not_found": str(e)}
            else:
                conn = build_connector(acc, settings, http)
                await conn.login()
                if conn.credentials_changed:
                    await db.save_credentials(acc)
                now = datetime.now(timezone.utc)
                n = 0
                async for _o in conn.fetch_orders(now - timedelta(hours=1), now):
                    n += 1
                detail = {"orders_last_hour": n}
    except (ConnectorError, CarrierError, httpx.HTTPError, KeyError, ValueError) as e:
        await pool.report_failure(acc.key, f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e), "health": await pool.health(acc.key)}
    await pool.report_ok(acc.key)
    return {"ok": True, "detail": detail, "health": await pool.health(acc.key)}


async def save_account(acc: Shop, db, pool: AccountPool, r) -> int:
    account_id = await db.upsert_shop(acc)
    await pool.reset(acc.key)
    await r.sadd(POKE_KEY, acc.key)
    return account_id
