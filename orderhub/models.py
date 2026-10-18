from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    shopee = "shopee"
    tiktok = "tiktok"
    lazada = "lazada"
    sapo = "sapo"
    tpos = "tpos"
    nhanh = "nhanh"
    pancake = "pancake"
    haravan = "haravan"
    ghtk = "ghtk"
    ghn = "ghn"
    jt = "jt"


ORDER_PLATFORMS = frozenset({Platform.shopee, Platform.tiktok, Platform.lazada, Platform.sapo, Platform.tpos,
                             Platform.nhanh, Platform.pancake, Platform.haravan})
CARRIER_PLATFORMS = frozenset({Platform.ghtk, Platform.ghn, Platform.jt})


class OrderStatus(str, Enum):
    pending = "pending"
    ready_to_ship = "ready_to_ship"
    shipping = "shipping"
    delivered = "delivered"
    cancelled = "cancelled"
    returned = "returned"
    unknown = "unknown"


class Customer(BaseModel):
    id: str | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None


class Shipping(BaseModel):
    recipient_name: str | None = None
    phone: str | None = None
    address: str | None = None
    ward: str | None = None
    district: str | None = None
    province: str | None = None
    province_legacy: str | None = None
    region: str | None = None
    subregion: str | None = None
    carrier: str | None = None
    tracking_number: str | None = None
    logistics_status: str | None = None


class Item(BaseModel):
    sku: str | None = None
    name: str
    quantity: int = 1
    price: Decimal = Decimal(0)


class NormalizedOrder(BaseModel):
    platform: Platform
    shop_id: str
    shop_name: str | None = None
    order_id: str
    status: OrderStatus = OrderStatus.unknown
    raw_status: str | None = None
    created_at: datetime
    updated_at: datetime
    total_amount: Decimal = Decimal(0)
    currency: str = "VND"
    customer: Customer = Field(default_factory=Customer)
    shipping: Shipping = Field(default_factory=Shipping)
    items: list[Item] = Field(default_factory=list)


class Shop(BaseModel):
    id: int | None = None
    platform: Platform
    shop_id: str
    name: str
    active: bool = True
    credentials: dict = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.platform.value}:{self.shop_id}"
