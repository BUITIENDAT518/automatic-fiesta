from __future__ import annotations

import asyncio
import contextlib
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from orderhub import bus
from orderhub.collector.__main__ import POKE_KEY
from orderhub.config import CHANNEL_UPDATES, get_settings
from orderhub.models import OrderStatus, Platform, Shop
from orderhub.regions import BAC, NAM, PROVINCES_2025, TRUNG
from orderhub.runtime import open_db

STATIC = Path(__file__).parent / "static"
settings = get_settings()
state: dict = {}


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    state["db"] = await open_db(settings)
    state["redis"] = bus.connect(settings.redis_url)
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
        "provinces": PROVINCES_2025, "platforms": [p.value for p in Platform],
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


@app.get("/api/shops")
async def list_shops(role: str = Depends(require_role)):
    shops = await state["db"].list_shops(active_only=False)
    status = await state["redis"].hgetall("collector:status")
    out = []
    for s in shops:
        last = status.get(s.key, "")
        parts = last.split("|", 3) if last else []
        out.append({**s.model_dump(exclude={"credentials"}),
                    "last_poll_at": parts[0] if parts else None, "last_poll_result": parts[1] if len(parts) > 1 else None,
                    "last_poll_detail": parts[2] if len(parts) > 2 else None, "worker": parts[3] if len(parts) > 3 else None})
    return out


@app.post("/api/shops", status_code=201)
async def add_shop(shop: Shop, _: str = Depends(require_admin)):
    shop_id = await state["db"].upsert_shop(shop)
    await state["redis"].sadd(POKE_KEY, shop.key)
    return {"id": shop_id}


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
