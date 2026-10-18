"""Nhanh.vn Open API v3 connector (https://apidocs.nhanh.vn/v3/order/list).

Credentials: {"access_token": "...", "business_id": "...", "expires_at": <unix>}; app id from NHANH_APP_ID.
Nhanh access tokens last one year and cannot be refreshed: the account must re-authorize before expiry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal

from orderhub.connectors.base import ConnectorError, StaticTokenConnector, ts_to_dt
from orderhub.connectors.status_map import NHANH
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

MAX_WINDOW = timedelta(days=31)


class NhanhConnector(StaticTokenConnector):
    def token_expiring(self, margin_s: int = 0) -> bool:
        exp = self.creds.get("expires_at")
        return exp is not None and int(exp) - margin_s < datetime.now().timestamp()

    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        self.require("access_token", "business_id")
        since = max(since, until - MAX_WINDOW)
        url = f"{self.settings.nhanh_host}/v3.0/order/list"
        params = {"appId": self.settings.nhanh_app_id, "businessId": self.creds["business_id"]}
        body: dict = {"filters": {"updatedAtFrom": int(since.timestamp()), "updatedAtTo": int(until.timestamp())},
                      "paginator": {"size": 100}}
        while True:
            resp = await self.http.post(url, params=params, json=body,
                                        headers={"Authorization": self.creds["access_token"]})
            data = resp.json()
            if data.get("code") != 1:
                raise ConnectorError(f"nhanh: {data.get('errorCode')} {data.get('messages')}")
            for raw in data.get("data") or []:
                yield self.normalize(raw)
            nxt = (data.get("paginator") or {}).get("next")
            if not nxt:
                return
            body["paginator"] = {"size": 100, "next": nxt}

    def normalize(self, raw: dict) -> NormalizedOrder:
        info, ch = raw.get("info") or {}, raw.get("channel") or {}
        addr, carrier = raw.get("shippingAddress") or {}, raw.get("carrier") or {}
        status = str(info.get("status"))
        items = [Item(sku=p.get("code"), name=p.get("name") or "Sản phẩm", quantity=int(p.get("quantity") or 1),
                      price=Decimal(str(p.get("priceAfterVAT") or p.get("price") or 0)))
                 for p in raw.get("products") or []]
        total = sum((Decimal(str(p.get("totalAmount") or 0)) for p in raw.get("products") or []), Decimal(0))
        total += Decimal(str(carrier.get("customerShipFee") or 0))
        return NormalizedOrder(
            platform=Platform.nhanh, shop_id=self.shop.shop_id, shop_name=ch.get("shopName") or self.shop.name,
            order_id=str(info["id"]), status=NHANH.get(status, OrderStatus.unknown), raw_status=status,
            created_at=ts_to_dt(info.get("createdAt")), updated_at=ts_to_dt(info.get("updatedAt")),
            total_amount=total,
            customer=Customer(id=str(addr.get("id") or "") or None, name=addr.get("name"), phone=addr.get("mobile"),
                              email=addr.get("email")),
            shipping=Shipping(recipient_name=addr.get("name"), phone=addr.get("mobile"), address=addr.get("address"),
                              carrier=carrier.get("name"), tracking_number=carrier.get("carrierCode")),
            items=items,
        )
