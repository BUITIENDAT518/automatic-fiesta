"""TikTok Shop Partner API (version 202309) connector (https://partner.tiktokshop.com/docv2)."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from orderhub.connectors.base import Connector, ConnectorError, hmac_sha256, ts_to_dt
from orderhub.connectors.status_map import TIKTOK
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

SEARCH_PATH = "/order/202309/orders/search"


def tiktok_sign(secret: str, path: str, params: dict, body: str = "") -> str:
    items = sorted((k, v) for k, v in params.items() if k not in ("sign", "access_token"))
    payload = path + "".join(f"{k}{v}" for k, v in items) + body
    return hmac_sha256(secret, secret + payload + secret)


class TikTokConnector(Connector):
    async def refresh_token(self) -> None:
        resp = await self.http.get(self.settings.tiktok_auth_host + "/api/v2/token/refresh", params={
            "app_key": self.settings.tiktok_app_key, "app_secret": self.settings.tiktok_app_secret,
            "refresh_token": self.creds.get("refresh_token"), "grant_type": "refresh_token",
        })
        body = resp.json()
        data = body.get("data") or {}
        if body.get("code") != 0 or "access_token" not in data:
            raise ConnectorError(f"tiktok token refresh failed: {body.get('code')} {body.get('message')}")
        self.creds.update(
            access_token=data["access_token"], refresh_token=data["refresh_token"],
            expires_at=int(data.get("access_token_expire_in", time.time() + 3600)),
        )

    async def _post(self, path: str, query: dict, body: dict) -> dict:
        params = {
            "app_key": self.settings.tiktok_app_key, "timestamp": int(time.time()),
            "shop_cipher": self.creds.get("shop_cipher", ""), **query,
        }
        raw_body = json.dumps(body, separators=(",", ":"))
        params["sign"] = tiktok_sign(self.settings.tiktok_app_secret, path, params, raw_body)
        resp = await self.http.post(
            self.settings.tiktok_host + path, params=params, content=raw_body,
            headers={"x-tts-access-token": self.creds["access_token"], "content-type": "application/json"},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise ConnectorError(f"tiktok {path}: {data.get('code')} {data.get('message')}")
        return data.get("data") or {}

    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        token = ""
        body = {"update_time_ge": int(since.timestamp()), "update_time_lt": int(until.timestamp())}
        while True:
            query = {"page_size": 50, "sort_field": "update_time", "sort_order": "ASC"}
            if token:
                query["page_token"] = token
            data = await self._post(SEARCH_PATH, query, body)
            for raw in data.get("orders", []):
                yield self.normalize(raw)
            token = data.get("next_page_token") or ""
            if not token:
                break

    def normalize(self, raw: dict) -> NormalizedOrder:
        addr = raw.get("recipient_address") or {}
        levels = {d.get("address_level"): d.get("address_name") for d in addr.get("district_info", [])}
        payment = raw.get("payment") or {}
        lines = raw.get("line_items", [])
        qty = Counter(li.get("sku_id") or li.get("id") for li in lines)
        seen: set = set()
        items = []
        for li in lines:
            key = li.get("sku_id") or li.get("id")
            if key in seen:
                continue
            seen.add(key)
            items.append(Item(
                sku=li.get("seller_sku") or li.get("sku_id"),
                name=" - ".join(p for p in [li.get("product_name"), li.get("sku_name")] if p),
                quantity=qty[key], price=Decimal(str(li.get("sale_price") or 0)),
            ))
        tracking = raw.get("tracking_number") or next((li.get("tracking_number") for li in lines if li.get("tracking_number")), None)
        carrier = raw.get("shipping_provider") or next((li.get("shipping_provider_name") for li in lines if li.get("shipping_provider_name")), None)
        return NormalizedOrder(
            platform=Platform.tiktok, shop_id=self.shop.shop_id, shop_name=self.shop.name,
            order_id=str(raw["id"]), raw_status=raw.get("status"),
            status=TIKTOK.get(raw.get("status", ""), OrderStatus.unknown),
            created_at=ts_to_dt(raw.get("create_time")), updated_at=ts_to_dt(raw.get("update_time")),
            total_amount=Decimal(str(payment.get("total_amount") or 0)), currency=payment.get("currency", "VND"),
            customer=Customer(id=raw.get("user_id"), name=addr.get("name"), email=raw.get("buyer_email")),
            shipping=Shipping(
                recipient_name=addr.get("name"), phone=addr.get("phone_number"), address=addr.get("full_address"),
                ward=levels.get("L3") or levels.get("L4"), district=levels.get("L2"), province=levels.get("L1"),
                carrier=carrier, tracking_number=tracking, logistics_status=raw.get("status"),
            ),
            items=items,
        )
