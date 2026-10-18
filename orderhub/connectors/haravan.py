"""Haravan Omni API connector (https://docs.haravan.com/docs/omni-apis/orders/).

Credentials: {"access_token": "..."} — a private-app token (Authorization: Bearer) or an OAuth app token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from orderhub.connectors.base import ConnectorError, StaticTokenConnector, iso_to_dt
from orderhub.connectors.sapo import _name
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

PAGE_SIZE = 50
CARRIER_STATUS = {"readytopick": OrderStatus.ready_to_ship, "picking": OrderStatus.ready_to_ship,
                  "delivering": OrderStatus.shipping, "delivered": OrderStatus.delivered,
                  "cancel": OrderStatus.cancelled, "return": OrderStatus.returned, "returned": OrderStatus.returned}


class HaravanConnector(StaticTokenConnector):
    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        self.require("access_token")
        url = f"{self.settings.haravan_host}/com/orders.json"
        headers = {"Authorization": f"Bearer {self.creds['access_token']}"}
        page = 1
        while True:
            params = {"updated_at_min": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                      "updated_at_max": until.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                      "limit": PAGE_SIZE, "page": page, "order": "updated_at asc"}
            resp = await self.http.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                raise ConnectorError(f"haravan: HTTP {resp.status_code} {resp.text[:200]}")
            orders = resp.json().get("orders") or []
            for raw in orders:
                yield self.normalize(raw)
            if len(orders) < PAGE_SIZE:
                return
            page += 1

    def normalize(self, raw: dict) -> NormalizedOrder:
        cust, addr = raw.get("customer") or {}, raw.get("shipping_address") or {}
        ful = (raw.get("fulfillments") or [{}])[-1]
        carrier_code = (ful.get("carrier_status_code") or "").lower()
        if raw.get("cancelled_status") == "cancelled" or raw.get("cancelled_at"):
            status = OrderStatus.cancelled
        elif carrier_code in CARRIER_STATUS:
            status = CARRIER_STATUS[carrier_code]
        elif raw.get("fulfillment_status") in ("fulfilled", "partial"):
            status = OrderStatus.shipping
        elif raw.get("confirmed_status") == "confirmed":
            status = OrderStatus.ready_to_ship
        else:
            status = OrderStatus.pending
        return NormalizedOrder(
            platform=Platform.haravan, shop_id=self.shop.shop_id, shop_name=self.shop.name,
            order_id=str(raw.get("name") or raw.get("order_number") or raw["id"]), status=status,
            raw_status=f"{raw.get('confirmed_status')}/{raw.get('fulfillment_status')}/{carrier_code or '-'}",
            created_at=iso_to_dt(raw.get("created_at")), updated_at=iso_to_dt(raw.get("updated_at")),
            total_amount=Decimal(str(raw.get("total_price") or 0)),
            customer=Customer(id=str(cust.get("id") or "") or None, name=_name(cust),
                              phone=cust.get("phone") or raw.get("phone"), email=cust.get("email") or raw.get("email")),
            shipping=Shipping(
                recipient_name=_name(addr), phone=addr.get("phone"),
                address=", ".join(p for p in (addr.get("address1"), addr.get("ward"), addr.get("district"),
                                              addr.get("province")) if p) or None,
                ward=addr.get("ward"), district=addr.get("district"), province=addr.get("province"),
                carrier=ful.get("tracking_company"), tracking_number=ful.get("tracking_number"),
                logistics_status=ful.get("carrier_status_name"),
            ),
            items=[Item(sku=li.get("sku"), name=li.get("name") or li.get("title") or "Sản phẩm",
                        quantity=int(li.get("quantity") or 1), price=Decimal(str(li.get("price") or 0)))
                   for li in raw.get("line_items") or []],
        )
