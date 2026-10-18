"""TPOS (tpos.vn) connector — EXPERIMENTAL.

TPOS has no public API docs. Login uses the OAuth password grant that the TPOS web app uses
(POST https://<tenant>.tpos.vn/token, client_id=tmtWebApp); orders come from the same OData view the web app's
invoice grid uses (/odata/FastSaleOrder/ODataService.GetView, Type eq 'invoice'). Field names are taken from the
public TPOS web bundle and still need confirming against a real account.
Credentials: {"tenant": "<subdomain>", "username": "...", "password": "..."}.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from orderhub.connectors.base import Connector, ConnectorError, iso_to_dt
from orderhub.models import Customer, NormalizedOrder, OrderStatus, Platform, Shipping

STATE = {"draft": OrderStatus.pending, "open": OrderStatus.ready_to_ship, "paid": OrderStatus.delivered,
         "cancel": OrderStatus.cancelled}
PAGE_SIZE = 200


def tenant_from(value: str | None) -> str | None:
    """Accepts 'shop', 'shop.tpos.vn' or any URL such as 'https://shop.tpos.vn/tpagev2/#/...'."""
    m = re.match(r"^(?:https?://)?([a-z0-9-]+)(?:\.tpos\.vn)?(?:[/:#?].*)?$", (value or "").strip().lower())
    return m.group(1) if m else None


class TposConnector(Connector):
    @property
    def host(self) -> str:
        tenant = tenant_from(self.creds.get("tenant") or self.shop.shop_id)
        if not tenant:
            raise ConnectorError("tpos: thiếu tenant trong credentials")
        return f"https://{tenant}.tpos.vn"

    async def refresh_token(self) -> None:
        resp = await self.http.post(f"{self.host}/token", data={
            "grant_type": "password", "username": self.creds.get("username"), "password": self.creds.get("password"),
            "client_id": "tmtWebApp",
        })
        data = resp.json()
        if "access_token" not in data:
            raise ConnectorError(f"tpos login failed: {data.get('error_description') or data.get('error')}")
        self.creds.update(access_token=data["access_token"],
                          expires_at=int(time.time()) + int(data.get("expires_in") or 3600))

    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        url = f"{self.host}/odata/FastSaleOrder/ODataService.GetView"
        lo, hi = (d.strftime("%Y-%m-%dT%H:%M:%SZ") for d in (since, until))
        flt = (f"Type eq 'invoice' and ((DateCreated ge {lo} and DateCreated lt {hi}) or "
               f"(ShipStatusLastUpdated ge {lo} and ShipStatusLastUpdated lt {hi}))")
        skip = 0
        while True:
            resp = await self.http.get(url, params={"$filter": flt, "$top": PAGE_SIZE, "$skip": skip,
                                                    "$orderby": "DateCreated"},
                                       headers={"Authorization": f"Bearer {self.creds['access_token']}"})
            if resp.status_code != 200:
                raise ConnectorError(f"tpos: HTTP {resp.status_code} {resp.text[:200]}")
            rows = resp.json().get("value") or []
            for raw in rows:
                yield self.normalize(raw)
            if len(rows) < PAGE_SIZE:
                return
            skip += PAGE_SIZE

    def normalize(self, raw: dict) -> NormalizedOrder:
        state = str(raw.get("State") or "")
        return NormalizedOrder(
            platform=Platform.tpos, shop_id=self.shop.shop_id, shop_name=self.shop.name,
            order_id=str(raw.get("Number") or raw["Id"]), status=STATE.get(state, OrderStatus.unknown),
            raw_status=raw.get("ShowState") or state,
            created_at=iso_to_dt(raw.get("DateInvoice") or raw.get("DateCreated")),
            updated_at=max(iso_to_dt(raw.get("DateCreated") or raw.get("DateInvoice")),
                           iso_to_dt(raw.get("ShipStatusLastUpdated") or raw.get("DateCreated"))),
            total_amount=Decimal(str(raw.get("AmountTotal") or 0)),
            customer=Customer(id=str(raw.get("PartnerId") or "") or None, name=raw.get("PartnerDisplayName"),
                              phone=raw.get("Phone")),
            shipping=Shipping(recipient_name=raw.get("ReceiverName") or raw.get("PartnerDisplayName"),
                              phone=raw.get("ReceiverPhone") or raw.get("Phone"),
                              address=raw.get("ReceiverAddress") or raw.get("Address"),
                              ward=raw.get("WardName"), district=raw.get("DistrictName"), province=raw.get("CityName"),
                              carrier=raw.get("CarrierName"), tracking_number=raw.get("TrackingRef"),
                              logistics_status=raw.get("ShowShipStatus") or raw.get("ShipStatus")),
        )
