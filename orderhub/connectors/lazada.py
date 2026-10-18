"""Lazada Open Platform connector (https://open.lazada.com/apps/doc/api)."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from orderhub.connectors.base import Connector, ConnectorError, hmac_sha256
from orderhub.connectors.status_map import LAZADA
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

VN_TZ = timezone(timedelta(hours=7))


def lazada_sign(secret: str, api_path: str, params: dict) -> str:
    payload = api_path + "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac_sha256(secret, payload).upper()


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace(" +", "+"))
    except ValueError:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=VN_TZ)


class LazadaConnector(Connector):
    def _params(self, extra: dict, with_token: bool = True) -> dict:
        p = {"app_key": self.settings.lazada_app_key, "timestamp": str(int(time.time() * 1000)), "sign_method": "sha256"}
        if with_token:
            p["access_token"] = self.creds["access_token"]
        p.update({k: str(v) for k, v in extra.items()})
        return p

    async def refresh_token(self) -> None:
        path = "/auth/token/refresh"
        params = self._params({"refresh_token": self.creds.get("refresh_token")}, with_token=False)
        params["sign"] = lazada_sign(self.settings.lazada_app_secret, path, params)
        data = (await self.http.post(self.settings.lazada_auth_host + path, data=params)).json()
        if "access_token" not in data:
            raise ConnectorError(f"lazada token refresh failed: {data.get('code')} {data.get('message')}")
        self.creds.update(
            access_token=data["access_token"], refresh_token=data["refresh_token"],
            expires_at=int(time.time()) + int(data.get("expires_in", 3600)),
        )

    async def _get(self, path: str, extra: dict) -> dict:
        params = self._params(extra)
        params["sign"] = lazada_sign(self.settings.lazada_app_secret, path, params)
        data = (await self.http.get(self.settings.lazada_host + path, params=params)).json()
        if str(data.get("code")) != "0":
            raise ConnectorError(f"lazada {path}: {data.get('code')} {data.get('message')}")
        return data

    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        offset = 0
        while True:
            data = (await self._get("/orders/get", {
                "update_after": since.astimezone(VN_TZ).isoformat(timespec="seconds"),
                "update_before": until.astimezone(VN_TZ).isoformat(timespec="seconds"),
                "sort_by": "updated_at", "sort_direction": "ASC", "offset": offset, "limit": 100,
            })).get("data") or {}
            orders = data.get("orders", [])
            if not orders:
                break
            ids = [o["order_id"] for o in orders]
            items_resp = await self._get("/orders/items/get", {"order_ids": json.dumps(ids)})
            items_by_order = {str(e["order_id"]): e.get("order_items", []) for e in items_resp.get("data", [])}
            for raw in orders:
                yield self.normalize(raw, items_by_order.get(str(raw["order_id"]), []))
            offset += len(orders)
            if offset >= int(data.get("countTotal", data.get("count", 0)) or 0):
                break

    def normalize(self, raw: dict, order_items: list[dict]) -> NormalizedOrder:
        addr = raw.get("address_shipping") or {}
        statuses = raw.get("statuses") or ([order_items[0].get("status")] if order_items else [])
        raw_status = statuses[0] if statuses else None
        qty = Counter(i.get("sku") for i in order_items)
        seen: set = set()
        items = []
        for it in order_items:
            if it.get("sku") in seen:
                continue
            seen.add(it.get("sku"))
            items.append(Item(sku=it.get("sku"), name=it.get("name", ""), quantity=qty[it.get("sku")],
                              price=Decimal(str(it.get("paid_price") or it.get("item_price") or 0))))
        first = order_items[0] if order_items else {}
        full_address = ", ".join(
            p for p in [addr.get("address1"), addr.get("address5"), addr.get("address4"), addr.get("address3")] if p
        )
        name = " ".join(p for p in [addr.get("first_name"), addr.get("last_name")] if p) or None
        return NormalizedOrder(
            platform=Platform.lazada, shop_id=self.shop.shop_id, shop_name=self.shop.name,
            order_id=str(raw["order_id"]), raw_status=raw_status,
            status=LAZADA.get(raw_status or "", OrderStatus.unknown),
            created_at=_parse_dt(raw.get("created_at")), updated_at=_parse_dt(raw.get("updated_at")),
            total_amount=Decimal(str(raw.get("price", "0")).replace(",", "")), currency="VND",
            customer=Customer(
                name=" ".join(p for p in [raw.get("customer_first_name"), raw.get("customer_last_name")] if p) or None,
            ),
            shipping=Shipping(
                recipient_name=name, phone=addr.get("phone"), address=full_address or addr.get("address1"),
                ward=addr.get("address5"), district=addr.get("address4"), province=addr.get("address3") or addr.get("city"),
                carrier=first.get("shipment_provider"), tracking_number=first.get("tracking_code") or None,
                logistics_status=first.get("status"),
            ),
            items=items,
        )
