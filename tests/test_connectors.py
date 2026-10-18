from datetime import datetime, timedelta, timezone

import httpx
import pytest

from orderhub.config import Settings
from orderhub.connectors import build_connector
from orderhub.connectors.lazada import LazadaConnector, lazada_sign
from orderhub.connectors.mock import MockConnector
from orderhub.connectors.shopee import ShopeeConnector
from orderhub.connectors.tiktok import TikTokConnector, tiktok_sign
from orderhub.crypto import PiiCipher, mask_name, mask_phone
from orderhub.models import OrderStatus, Platform, Shop

S = Settings()


def shop(p, creds=None):
    return Shop(platform=p, shop_id="123", name="Test", credentials=creds or {"access_token": "t"})


def test_registry_picks_mock():
    c = build_connector(shop(Platform.shopee, {"mock": True}), S, httpx.AsyncClient())
    assert isinstance(c, MockConnector)
    assert isinstance(build_connector(shop(Platform.tiktok), S, httpx.AsyncClient()), TikTokConnector)


def test_signatures_deterministic():
    a = tiktok_sign("sec", "/p", {"b": 2, "a": 1, "sign": "x", "access_token": "y"}, "{}")
    assert a == tiktok_sign("sec", "/p", {"a": 1, "b": 2}, "{}")
    s = lazada_sign("sec", "/orders/get", {"b": "2", "a": "1"})
    assert s.isupper() and len(s) == 64


def test_shopee_normalize():
    c = ShopeeConnector(shop(Platform.shopee), S, httpx.AsyncClient())
    o = c.normalize({
        "order_sn": "2410ABC", "order_status": "SHIPPED", "create_time": 1729000000, "update_time": 1729003600,
        "total_amount": 250000, "currency": "VND", "buyer_user_id": 9, "buyer_username": "buyer",
        "recipient_address": {"name": "Nguyễn A", "phone": "0901234567", "full_address": "1 Lê Lợi, Q1, TP. Hồ Chí Minh",
                              "state": "TP. Hồ Chí Minh", "city": "Quận 1"},
        "package_list": [{"shipping_carrier": "SPX Express", "logistics_status": "LOGISTICS_PICKUP_DONE"}],
        "item_list": [{"item_name": "Áo", "model_quantity_purchased": 2, "model_discounted_price": 100000, "item_sku": "A1"}],
    }, "SPXVN123")
    assert o.status == OrderStatus.shipping and o.shipping.tracking_number == "SPXVN123"
    assert o.items[0].quantity == 2 and o.shipping.carrier == "SPX Express"


def test_tiktok_normalize_groups_line_items():
    c = TikTokConnector(shop(Platform.tiktok), S, httpx.AsyncClient())
    li = {"sku_id": "s1", "product_name": "Serum", "sale_price": "189000", "tracking_number": "JT1"}
    o = c.normalize({
        "id": "5771", "status": "IN_TRANSIT", "create_time": 1729000000, "update_time": 1729000100,
        "payment": {"total_amount": "378000", "currency": "VND"}, "shipping_provider": "J&T Express",
        "recipient_address": {"name": "B", "phone_number": "(+84)90***", "full_address": "x",
                              "district_info": [{"address_level": "L1", "address_name": "Hà Nội"}]},
        "line_items": [li, dict(li)],
    })
    assert o.status == OrderStatus.shipping and o.items[0].quantity == 2
    assert o.shipping.province == "Hà Nội" and o.shipping.tracking_number == "JT1"


def test_lazada_normalize():
    c = LazadaConnector(shop(Platform.lazada), S, httpx.AsyncClient())
    o = c.normalize(
        {"order_id": 4001, "statuses": ["shipped"], "created_at": "2024-10-15 10:00:00 +0700",
         "updated_at": "2024-10-15 12:00:00 +0700", "price": "459,000.00",
         "address_shipping": {"first_name": "C", "phone": "0912", "address1": "9 Quang Trung", "address3": "Đà Nẵng"}},
        [{"sku": "G1", "name": "Giày", "paid_price": 459000, "shipment_provider": "LEX VN", "tracking_code": "LEX1"}],
    )
    assert o.status == OrderStatus.shipping and o.total_amount == 459000
    assert o.shipping.province == "Đà Nẵng" and o.shipping.tracking_number == "LEX1"
    assert o.created_at.utcoffset() == timedelta(hours=7)


async def test_mock_backfill_then_incremental():
    c = MockConnector(shop(Platform.lazada, {"mock": True}).model_copy(update={"shop_id": "m1"}), S, httpx.AsyncClient())
    now = datetime.now(timezone.utc)
    first = [o async for o in c.fetch_orders(now - timedelta(hours=48), now)]
    assert len(first) >= 120 and all(now - timedelta(hours=48) <= o.created_at <= now for o in first)
    [o async for o in c.fetch_orders(now, now)]


def test_pii():
    c = PiiCipher("any-passphrase")
    assert c.decrypt(c.encrypt("0901234567")) == "0901234567"
    assert mask_phone("0901234567") == "090****567"
    assert mask_name("Nguyễn Văn An") == "Nguyễn V. An"


@pytest.mark.parametrize("p", [Platform.shopee, Platform.tiktok, Platform.lazada, Platform.tpos, Platform.nhanh])
def test_token_expiring(p):
    c = build_connector(shop(p, {"access_token": "t", "expires_at": 0}), S, httpx.AsyncClient())
    assert c.token_expiring()
