from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from orderhub import bus
from orderhub.carriers import CarrierError, NotFound, build_carrier
from orderhub.collector.__main__ import POKE_KEY
from orderhub.config import CHANNEL_UPDATES, get_settings
from orderhub.connectors import ConnectorError, build_connector
from orderhub.models import CARRIER_PLATFORMS, ORDER_PLATFORMS, OrderStatus, Platform, Shop
from orderhub.pool import AccountPool
from orderhub.regions import BAC, NAM, PROVINCES_2025, TRUNG
from orderhub.runtime import open_db
from orderhub.tracker.__main__ import POKE_KEY as TRACKER_POKE_KEY

STATIC = Path(__file__).parent / "static"
settings = get_settings()
state: dict = {}


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    state["db"] = await open_db(settings)
    state["redis"] = bus.connect(settings.redis_url)
    state["pool"] = AccountPool(state["redis"])
    yield
    await state["db"].close()
    await state["redis"].aclose()


app = FastAPI(title="Order Hub", version="0.1.0", lifespan=lifespan)


def role_for(key: str | None) -> str | None:
    if key and secrets.compare_digest(key, settings.api_key_admin):
        return "admin"
    if key and secrets.compare_digest(key, settings.api_key_viewer):
        return "viewer"
    return None


def require_role(x_api_key: str | None = Header(default=None)) -> str:
    role = role_for(x_api_key)
    if not role:
        raise HTTPException(401, "invalid or missing X-API-Key")
    return role


def require_admin(role: str = Depends(require_role)) -> str:
    if role != "admin":
        raise HTTPException(403, "admin key required")
    return role


@app.get("/healthz")
async def healthz():
    await state["db"].pool.fetchval("SELECT 1")
    await state["redis"].ping()
    return {"ok": True}


@app.get("/")
async def dashboard():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
async def meta(role: str = Depends(require_role)):
    return {
        "role": role, "window_hours": settings.window_hours, "regions": [BAC, TRUNG, NAM],
        "provinces": PROVINCES_2025, "platforms": [p.value for p in Platform if p in ORDER_PLATFORMS],
        "carrier_platforms": [p.value for p in CARRIER_PLATFORMS],
        "statuses": [s.value for s in OrderStatus],
    }


def _filters(platform, shop_id, region, province, status, carrier) -> dict:
    return {"platform": platform, "shop_id": shop_id, "region": region, "province": province,
            "status": status, "carrier": carrier}


@app.get("/api/orders")
async def list_orders(
    role: str = Depends(require_role),
    hours: int = Query(default=None, ge=1, le=24 * 30),
    platform: str | None = None, shop_id: str | None = None, region: str | None = None,
    province: str | None = None, status: str | None = None, carrier: str | None = None,
    q: str | None = None, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
):
    total, rows = await state["db"].query_orders(
        hours or settings.window_hours, _filters(platform, shop_id, region, province, status, carrier),
        q, limit, offset, reveal_pii=role == "admin",
    )
    return {"total": total, "limit": limit, "offset": offset, "orders": rows}


@app.get("/api/orders/{platform}/{shop_id}/{order_id}")
async def get_order(platform: str, shop_id: str, order_id: str, role: str = Depends(require_role)):
    row = await state["db"].get_order(platform, shop_id, order_id, reveal_pii=role == "admin")
    if not row:
        raise HTTPException(404, "order not found")
    return row


@app.get("/api/stats")
async def stats(
    role: str = Depends(require_role), hours: int = Query(default=None, ge=1, le=24 * 30),
    platform: str | None = None, shop_id: str | None = None, region: str | None = None,
    province: str | None = None, status: str | None = None, carrier: str | None = None,
):
    return await state["db"].stats(hours or settings.window_hours,
                                   _filters(platform, shop_id, region, province, status, carrier))


def _kind(p: Platform) -> str:
    return "carrier" if p in CARRIER_PLATFORMS else "orders"


@app.get("/api/accounts")
async def list_accounts(role: str = Depends(require_role), platform: Platform | None = None):
    """Account pool: every platform account with its live health (ok / cooldown / error …)."""
    out = []
    for s in await state["db"].list_shops(active_only=False):
        if platform and s.platform != platform:
            continue
        health = await state["pool"].health(s.key)
        out.append({**s.model_dump(exclude={"credentials"}), "kind": _kind(s.platform),
                    "health": health if s.active else {**health, "state": "disabled"}})
    return out


@app.get("/api/shops")
async def list_shops(role: str = Depends(require_role)):
    return [a for a in await list_accounts(role) if a["kind"] == "orders"]


@app.post("/api/accounts", status_code=201)
@app.post("/api/shops", status_code=201, include_in_schema=False)
async def add_account(shop: Shop, _: str = Depends(require_admin)):
    shop_id = await state["db"].upsert_shop(shop)
    await state["pool"].reset(shop.key)
    await state["redis"].sadd(POKE_KEY, shop.key)
    return {"id": shop_id}


async def _account(platform: Platform, shop_id: str) -> Shop:
    acc = await state["db"].get_shop(platform.value, shop_id)
    if not acc:
        raise HTTPException(404, "account not found")
    return acc


@app.patch("/api/accounts/{platform}/{shop_id}")
async def set_account_active(platform: Platform, shop_id: str, active: bool, _: str = Depends(require_admin)):
    await _account(platform, shop_id)
    await state["db"].set_shop_active(platform.value, shop_id, active)
    await state["pool"].reset(f"{platform.value}:{shop_id}")
    return {"active": active}


@app.delete("/api/accounts/{platform}/{shop_id}", status_code=204)
async def delete_account(platform: Platform, shop_id: str, _: str = Depends(require_admin)):
    await _account(platform, shop_id)
    await state["db"].delete_shop(platform.value, shop_id)
    await state["pool"].reset(f"{platform.value}:{shop_id}")


@app.post("/api/accounts/{platform}/{shop_id}/check")
async def check_account(platform: Platform, shop_id: str, tracking_number: str | None = None,
                        _: str = Depends(require_admin)):
    """Live login test: order accounts fetch the last hour of orders; carrier accounts track one shipment."""
    acc = await _account(platform, shop_id)
    pool = state["pool"]
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            if platform in CARRIER_PLATFORMS:
                if not tracking_number:
                    raise HTTPException(422, "tracking_number is required to check a carrier account")
                try:
                    res = await build_carrier(acc, settings, http).track(tracking_number)
                    detail = res.model_dump(mode="json")
                except NotFound as e:
                    detail = {"tracking_number": tracking_number, "not_found": str(e)}
            else:
                conn = build_connector(acc, settings, http)
                await conn.login()
                if conn.credentials_changed:
                    await state["db"].save_credentials(acc)
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


@app.post("/webhooks/carrier/{carrier}", status_code=200)
async def carrier_webhook(carrier: Platform, request: Request):
    """GHTK/GHN status push: only queues the tracking number for an immediate re-check via the carrier API."""
    if carrier not in CARRIER_PLATFORMS:
        raise HTTPException(404)
    raw = await request.body()
    try:
        body = json.loads(raw)
    except ValueError:
        body = {k: v[0] for k, v in parse_qs(raw.decode(errors="ignore")).items()}
    tn = body.get("label_id") or body.get("OrderCode") or body.get("order_code")
    if tn:
        await state["redis"].sadd(TRACKER_POKE_KEY, str(tn))
    return {"accepted": bool(tn)}


@app.post("/webhooks/{platform}/{shop_id}", status_code=202)
async def webhook(platform: Platform, shop_id: str):
    """Marketplace push notifications only trigger an immediate re-poll; payloads are never trusted as data."""
    await state["redis"].sadd(POKE_KEY, f"{platform.value}:{shop_id}")
    return {"accepted": True}


@app.websocket("/ws")
async def ws(websocket: WebSocket, key: str | None = None):
    if not role_for(key):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    pubsub = state["redis"].pubsub()
    await pubsub.subscribe(CHANNEL_UPDATES)

    async def pump():
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                await websocket.send_text(msg["data"])

    task = asyncio.create_task(pump())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await pubsub.unsubscribe(CHANNEL_UPDATES)
        await pubsub.aclose()
