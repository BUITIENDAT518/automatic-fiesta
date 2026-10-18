"""Shopee Open Platform v2 connector (https://open.shopee.com/documents)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from orderhub.connectors.base import Connector, ConnectorError, hmac_sha256, ts_to_dt
from orderhub.connectors.status_map import SHOPEE
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

DETAIL_FIELDS = (
    "buyer_user_id,buyer_username,recipient_address,item_list,total_amount,shipping_carrier,"
    "package_list,pay_time,cancel_reason"
)
MAX_WINDOW_S = 15 * 86400


class ShopeeConnector(Connector):
    def sign(self, path: str, timestamp: int, access_token: str = "", shop_id: str = "") -> str:
        base = f"{self.settings.shopee_partner_id}{path}{timestamp}{access_token}{shop_id}"
        return hmac_sha256(self.settings.shopee_partner_key, base)

    async def refresh_token(self) -> None:
        path, ts = "/api/v2/auth/access_token/get", int(time.time())
        params = {"partner_id": self.settings.shopee_partner_id, "timestamp": ts, "sign": self.sign(path, ts)}
        body = {
            "refresh_token": self.creds.get("refresh_token"),
            "partner_id": int(self.settings.shopee_partner_id),
            "shop_id": int(self.shop.shop_id),
        }
        resp = await self.http.post(self.settings.shopee_host + path, params=params, json=body)
        data = resp.json()
        if data.get("error") or "access_token" not in data:
            raise ConnectorError(f"shopee token refresh failed: {data.get('error')} {data.get('message')}")
        self.creds.update(
            access_token=data["access_token"], refresh_token=data["refresh_token"],
            expires_at=int(time.time()) + int(data.get("expire_in", 14400)),
        )

    async def _get(self, path: str, params: dict) -> dict:
        ts, token = int(time.time()), self.creds["access_token"]
        common = {
            "partner_id": self.settings.shopee_partner_id, "timestamp": ts, "access_token": token,
            "shop_id": self.shop.shop_id, "sign": self.sign(path, ts, token, self.shop.shop_id),
        }
        resp = await self.http.get(self.settings.shopee_host + path, params={**common, **params})
        data = resp.json()
        if data.get("error"):
            raise ConnectorError(f"shopee {path}: {data.get('error')} {data.get('message')}")
        return data.get("response", {})

    async def _order_sns(self, since: datetime, until: datetime) -> list[str]:
        sns: list[str] = []
        start, end = int(since.timestamp()), int(until.timestamp())
        while start < end:
            chunk_end = min(start + MAX_WINDOW_S, end)
            cursor = ""
            while True:
                r = await self._get("/api/v2/order/get_order_list", {
                    "time_range_field": "update_time", "time_from": start, "time_to": chunk_end,
                    "page_size": 100, "cursor": cursor, "response_optional_fields": "order_status",
                })
                sns += [o["order_sn"] for o in r.get("order_list", [])]
                if not r.get("more"):
                    break
                cursor = r.get("next_cursor", "")
            start = chunk_end
        return sns

    async def _tracking(self, order_sn: str) -> str | None:
        try:
            r = await self._get("/api/v2/logistics/get_tracking_number", {"order_sn": order_sn})
            return r.get("tracking_number") or None
        except ConnectorError:
            return None

    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        sns = await self._order_sns(since, until)
        for i in range(0, len(sns), 50):
            r = await self._get("/api/v2/order/get_order_detail", {
                "order_sn_list": ",".join(sns[i:i + 50]), "response_optional_fields": DETAIL_FIELDS,
            })
            orders = r.get("order_list", [])
            shipped = [o["order_sn"] for o in orders if SHOPEE.get(o.get("order_status")) in
                       (OrderStatus.shipping, OrderStatus.delivered, OrderStatus.ready_to_ship)]
            tracking = dict(zip(shipped, await asyncio.gather(*(self._tracking(sn) for sn in shipped))))
            for raw in orders:
                yield self.normalize(raw, tracking.get(raw["order_sn"]))

    def normalize(self, raw: dict, tracking_number: str | None = None) -> NormalizedOrder:
        addr = raw.get("recipient_address") or {}
        pkg = (raw.get("package_list") or [{}])[0]
        return NormalizedOrder(
            platform=Platform.shopee, shop_id=self.shop.shop_id, shop_name=self.shop.name,
            order_id=raw["order_sn"], raw_status=raw.get("order_status"),
            status=SHOPEE.get(raw.get("order_status", ""), OrderStatus.unknown),
            created_at=ts_to_dt(raw.get("create_time")), updated_at=ts_to_dt(raw.get("update_time")),
            total_amount=Decimal(str(raw.get("total_amount", 0))), currency=raw.get("currency", "VND"),
            customer=Customer(id=str(raw.get("buyer_user_id") or "") or None, name=raw.get("buyer_username")),
            shipping=Shipping(
                recipient_name=addr.get("name"), phone=addr.get("phone"), address=addr.get("full_address"),
                ward=addr.get("town") or addr.get("district"), district=addr.get("city") or addr.get("district"),
                province=addr.get("state") or addr.get("city"),
                carrier=pkg.get("shipping_carrier") or raw.get("shipping_carrier"),
                tracking_number=tracking_number, logistics_status=pkg.get("logistics_status"),
            ),
            items=[
                Item(sku=it.get("model_sku") or it.get("item_sku"), name=it.get("item_name", ""),
                     quantity=int(it.get("model_quantity_purchased", 1)),
                     price=Decimal(str(it.get("model_discounted_price", 0))))
                for it in raw.get("item_list", [])
            ],
        )
