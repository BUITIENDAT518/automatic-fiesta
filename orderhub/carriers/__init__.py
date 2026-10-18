"""Carrier tracking clients (GHTK, GHN). One instance per carrier account from the pool."""

from __future__ import annotations

import abc
import random

import httpx
from pydantic import BaseModel

from orderhub.config import Settings
from orderhub.connectors.status_map import GHN, GHN_TEXT, GHTK
from orderhub.models import OrderStatus, Platform, Shop


class CarrierError(RuntimeError):
    pass


class NotFound(CarrierError):
    """Tracking number does not belong to this account; try the next account in the pool."""


class TrackingResult(BaseModel):
    tracking_number: str
    status: OrderStatus
    logistics_status: str


CARRIER_ALIASES = {
    Platform.ghtk: ("ghtk", "giao hàng tiết kiệm", "giao hang tiet kiem", "giaohangtietkiem"),
    Platform.ghn: ("ghn", "giao hàng nhanh", "giao hang nhanh", "giaohangnhanh"),
}


def carrier_platform(carrier_name: str | None) -> Platform | None:
    name = (carrier_name or "").lower()
    for platform, aliases in CARRIER_ALIASES.items():
        if any(a in name for a in aliases):
            return platform
    return None


class CarrierClient(abc.ABC):
    def __init__(self, account: Shop, settings: Settings, http: httpx.AsyncClient):
        self.account, self.settings, self.http = account, settings, http

    @property
    def creds(self) -> dict:
        return self.account.credentials

    @abc.abstractmethod
    async def track(self, tracking_number: str) -> TrackingResult: ...


class GhtkClient(CarrierClient):
    """GET /services/shipment/v2/{label}; headers Token + X-Client-Source (https://api.ghtk.vn)."""

    async def track(self, tracking_number: str) -> TrackingResult:
        headers = {"Token": self.creds.get("token", "")}
        if self.creds.get("partner_code"):
            headers["X-Client-Source"] = self.creds["partner_code"]
        resp = await self.http.get(f"{self.settings.ghtk_host}/services/shipment/v2/{tracking_number}", headers=headers)
        data = resp.json()
        if not data.get("success"):
            msg = str(data.get("message") or resp.status_code)
            if resp.status_code in (401, 403) or "token" in msg.lower():
                raise CarrierError(f"ghtk: {msg}")
            raise NotFound(msg)
        o = data.get("order") or {}
        code = str(o.get("status"))
        return TrackingResult(tracking_number=tracking_number, status=GHTK.get(code, OrderStatus.unknown),
                              logistics_status=o.get("status_text") or code)


class GhnClient(CarrierClient):
    """POST /v2/shipping-order/detail {order_code}; header Token (+ ShopId)."""

    async def track(self, tracking_number: str) -> TrackingResult:
        headers = {"Token": self.creds.get("token", "")}
        if self.creds.get("shop_id"):
            headers["ShopId"] = str(self.creds["shop_id"])
        resp = await self.http.post(f"{self.settings.ghn_host}/v2/shipping-order/detail", headers=headers,
                                    json={"order_code": tracking_number})
        data = resp.json()
        if data.get("code") != 200:
            if resp.status_code == 401 or data.get("code") == 401:
                raise CarrierError(f"ghn: {data.get('message')}")
            raise NotFound(str(data.get("message")))
        st = (data.get("data") or {}).get("status", "")
        return TrackingResult(tracking_number=tracking_number, status=GHN.get(st, OrderStatus.unknown),
                              logistics_status=GHN_TEXT.get(st, st))


class MockCarrierClient(CarrierClient):
    async def track(self, tracking_number: str) -> TrackingResult:
        st = random.choice(["transporting", "delivering", "delivered", "delivered", "delivery_fail"])
        return TrackingResult(tracking_number=tracking_number, status=GHN[st], logistics_status=GHN_TEXT[st])


CLIENTS: dict[Platform, type[CarrierClient]] = {Platform.ghtk: GhtkClient, Platform.ghn: GhnClient}


def build_carrier(account: Shop, settings: Settings, http: httpx.AsyncClient) -> CarrierClient:
    cls = MockCarrierClient if account.credentials.get("mock") else CLIENTS[account.platform]
    return cls(account, settings, http)
