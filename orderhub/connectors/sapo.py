"""Sapo (mysapo.net) Admin API connector.

Credentials: {"store": "<alias>.mysapo.net alias", "access_token": "..."} or {"store", "api_key", "api_secret"}
for private apps (HTTP basic auth).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from orderhub.connectors.base import ConnectorError, StaticTokenConnector, iso_to_dt
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

PAGE_SIZE = 250


def _name(d: dict) -> str | None:
    return d.get("name") or " ".join(p for p in (d.get("last_name"), d.get("first_name")) if p) or None


class SapoConnector(StaticTokenConnector):
    def _auth(self) -> dict:
        if self.creds.get("access_token"):
            return {"headers": {"X-Sapo-Access-Token": self.creds["access_token"]}}
        self.require("api_key", "api_secret")
        return {"auth": (self.creds["api_key"], self.creds["api_secret"])}

    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        self.require("store")
        url = f"https://{self.creds['store']}.mysapo.net/admin/orders.json"
        page = 1
        while True:
            params = {"status": "any", "limit": PAGE_SIZE, "page": page,
                      "modified_on_min": since.isoformat(), "modified_on_max": until.isoformat()}
            resp = await self.http.get(url, params=params, **self._auth())
            if resp.status_code != 200:
                raise ConnectorError(f"sapo: HTTP {resp.status_code} {resp.text[:200]}")
            orders = resp.json().get("orders") or []
            for raw in orders:
                yield self.normalize(raw)
            if len(orders) < PAGE_SIZE:
                return
            page += 1

    def normalize(self, raw: dict) -> NormalizedOrder:
        cust, addr = raw.get("customer") or {}, raw.get("shipping_address") or {}
        ful = (raw.get("fulfillments") or [{}])[-1]
        if raw.get("cancelled_on") or raw.get("status") == "cancelled":
            status = OrderStatus.cancelled
        elif raw.get("status") == "closed" and raw.get("fulfillment_status") == "fulfilled":
            status = OrderStatus.delivered
        elif raw.get("fulfillment_status") in ("fulfilled", "partial"):
            status = OrderStatus.shipping
        else:
            status = OrderStatus.pending
        return NormalizedOrder(
            platform=Platform.sapo, shop_id=self.shop.shop_id, shop_name=self.shop.name,
            order_id=str(raw.get("name") or raw["id"]), status=status,
            raw_status=f"{raw.get('status')}/{raw.get('financial_status')}/{raw.get('fulfillment_status')}",
            created_at=iso_to_dt(raw.get("created_on")), updated_at=iso_to_dt(raw.get("modified_on")),
            total_amount=Decimal(str(raw.get("total_price") or 0)),
            customer=Customer(id=str(cust.get("id") or "") or None, name=_name(cust),
                              phone=cust.get("phone") or raw.get("phone"), email=cust.get("email") or raw.get("email")),
            shipping=Shipping(
                recipient_name=_name(addr), phone=addr.get("phone"),
                address=", ".join(p for p in (addr.get("address1"), addr.get("ward"), addr.get("district"),
                                              addr.get("province") or addr.get("city")) if p) or None,
                ward=addr.get("ward"), district=addr.get("district"), province=addr.get("province") or addr.get("city"),
                carrier=ful.get("tracking_company"), tracking_number=ful.get("tracking_number"),
                logistics_status=ful.get("shipment_status"),
            ),
            items=[Item(sku=li.get("sku"), name=li.get("name") or li.get("title") or "Sản phẩm",
                        quantity=int(li.get("quantity") or 1), price=Decimal(str(li.get("price") or 0)))
                   for li in raw.get("line_items") or []],
        )
