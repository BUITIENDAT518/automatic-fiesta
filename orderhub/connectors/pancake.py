"""Pancake POS connector (https://docs.pancake.biz/pos/api/). Credentials: {"api_key": "..."}."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from orderhub.connectors.base import ConnectorError, StaticTokenConnector, iso_to_dt
from orderhub.connectors.status_map import PANCAKE
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

PAGE_SIZE = 100


class PancakeConnector(StaticTokenConnector):
    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        self.require("api_key")
        url = f"{self.settings.pancake_host}/shops/{self.shop.shop_id}/orders"
        page = 1
        while True:
            params = {
                "api_key": self.creds["api_key"], "page_size": PAGE_SIZE, "page_number": page,
                "updateStatus": "updated_at", "startDateTime": int(since.timestamp()),
                "endDateTime": int(until.timestamp()), "option_sort": "last_updated_order_asc",
            }
            resp = await self.http.get(url, params=params)
            data = resp.json()
            if resp.status_code != 200 or data.get("success") is False:
                raise ConnectorError(f"pancake: {resp.status_code} {data.get('message')}")
            for raw in data.get("data") or []:
                yield self.normalize(raw)
            if page >= int(data.get("total_pages") or 1):
                return
            page += 1

    def normalize(self, raw: dict) -> NormalizedOrder:
        addr = raw.get("shipping_address") or {}
        partner = raw.get("partner") or {}
        status = str(raw.get("status"))
        items = []
        for it in raw.get("items") or []:
            v = it.get("variation_info") or {}
            items.append(Item(sku=v.get("display_id") or it.get("variation_id"), name=v.get("name") or "Sản phẩm",
                              quantity=int(it.get("quantity") or 1), price=Decimal(str(v.get("retail_price") or 0))))
        return NormalizedOrder(
            platform=Platform.pancake, shop_id=self.shop.shop_id, shop_name=self.shop.name,
            order_id=str(raw["id"]), status=PANCAKE.get(status, OrderStatus.unknown),
            raw_status=raw.get("status_name") or status,
            created_at=iso_to_dt(raw.get("inserted_at")), updated_at=iso_to_dt(raw.get("updated_at")),
            total_amount=Decimal(str(raw.get("total_price") or 0)),
            customer=Customer(id=str((raw.get("customer") or {}).get("id") or "") or None,
                              name=raw.get("bill_full_name"), phone=raw.get("bill_phone_number"),
                              email=raw.get("bill_email")),
            shipping=Shipping(
                recipient_name=addr.get("full_name"), phone=addr.get("phone_number"),
                address=addr.get("full_address") or addr.get("address"), ward=addr.get("commnue_name"),
                district=addr.get("district_name"), province=addr.get("province_name"),
                carrier=partner.get("partner_name"), tracking_number=partner.get("extend_code"),
                logistics_status=partner.get("partner_status"),
            ),
            items=items,
        )
