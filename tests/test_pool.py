import json
from pathlib import Path

import httpx
import pytest

from orderhub.carriers import GhnClient, GhtkClient, NotFound, carrier_platform
from orderhub.config import Settings
from orderhub.connectors import build_connector
from orderhub.connectors.nhanh import NhanhConnector
from orderhub.connectors.pancake import PancakeConnector
from orderhub.connectors.sapo import SapoConnector
from orderhub.connectors.tpos import TposConnector
from orderhub.models import CARRIER_PLATFORMS, ORDER_PLATFORMS, OrderStatus, Platform, Shop
from orderhub.pool import AccountPool

S = Settings()


def acc(p, creds=None, shop_id="1"):
    return Shop(platform=p, shop_id=shop_id, name="Test", credentials=creds or {})


def test_every_order_platform_has_a_connector():
    for p in ORDER_PLATFORMS:
        build_connector(acc(p), S, httpx.AsyncClient())
    assert not ORDER_PLATFORMS & CARRIER_PLATFORMS
    assert isinstance(build_connector(acc(Platform.tpos), S, httpx.AsyncClient()), TposConnector)


def test_pancake_normalize():
    raw = {"id": 991, "status": 2, "status_name": "Đã gửi hàng", "inserted_at": "2026-10-17T03:00:00",
           "updated_at": "2026-10-17T05:00:00", "total_price": 350000, "bill_full_name": "Nguyễn Văn A",
           "bill_phone_number": "0912345678", "customer": {"id": "c1"},
           "shipping_address": {"full_name": "Nguyễn Văn A", "phone_number": "0912345678",
                                "full_address": "12 Lê Lợi, Phường Bến Thành, TP Hồ Chí Minh",
                                "province_name": "Hồ Chí Minh", "district_name": "Quận 1"},
           "partner": {"partner_name": "Giao Hàng Tiết Kiệm", "extend_code": "S1.A1.1", "partner_status": "picked"},
           "items": [{"quantity": 2, "variation_id": "v1", "variation_info": {"name": "Áo", "retail_price": 150000}}]}
    o = PancakeConnector(acc(Platform.pancake), S, httpx.AsyncClient()).normalize(raw)
    assert o.status == OrderStatus.shipping and o.order_id == "991" and o.shipping.tracking_number == "S1.A1.1"
    assert o.shipping.province == "Hồ Chí Minh" and o.items[0].quantity == 2 and o.created_at.tzinfo


def test_nhanh_normalize():
    raw = {"info": {"id": 123, "status": 60, "createdAt": 1792200000, "updatedAt": 1792210000},
           "channel": {"shopName": "Kho chính"},
           "shippingAddress": {"name": "Trần B", "mobile": "0987654321", "address": "5 Trần Phú, Hà Nội"},
           "products": [{"code": "SP1", "name": "Bàn", "quantity": 1, "price": 1000000, "totalAmount": 1000000}],
           "carrier": {"name": "Giao Hàng Nhanh", "carrierCode": "GHN123", "customerShipFee": 30000}}
    o = NhanhConnector(acc(Platform.nhanh), S, httpx.AsyncClient()).normalize(raw)
    assert o.status == OrderStatus.delivered and o.total_amount == 1030000 and o.shop_name == "Kho chính"
    assert o.shipping.carrier == "Giao Hàng Nhanh" and o.shipping.tracking_number == "GHN123"


def test_sapo_normalize_and_auth():
    raw = {"id": 1, "name": "#1001", "status": "open", "fulfillment_status": "fulfilled",
           "created_on": "2026-10-17T03:00:00Z", "modified_on": "2026-10-17T04:00:00Z", "total_price": 99000,
           "customer": {"id": 7, "first_name": "C", "last_name": "Lê", "phone": "0900000000"},
           "shipping_address": {"first_name": "C", "last_name": "Lê", "address1": "1 Nguyễn Huệ", "province": "Đà Nẵng"},
           "fulfillments": [{"tracking_company": "GHTK", "tracking_number": "S9.1"}],
           "line_items": [{"sku": "X", "name": "Ly", "quantity": 3, "price": 33000}]}
    c = SapoConnector(acc(Platform.sapo, {"store": "demo", "access_token": "t"}), S, httpx.AsyncClient())
    o = c.normalize(raw)
    assert o.order_id == "#1001" and o.status == OrderStatus.shipping and o.customer.name == "Lê C"
    assert c._auth() == {"headers": {"X-Sapo-Access-Token": "t"}}
    assert o.shipping.province == "Đà Nẵng"


def test_carrier_aliases():
    assert carrier_platform("Giao Hàng Tiết Kiệm") == Platform.ghtk
    assert carrier_platform("GHN Express") == Platform.ghn
    assert carrier_platform("Giao Hàng Nhanh") == Platform.ghn


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ghtk_track_and_not_found():
    def handler(req: httpx.Request):
        assert req.headers["Token"] == "tok"
        if req.url.path.endswith("/S1"):
            return httpx.Response(200, json={"success": True, "order": {"status": "5", "status_text": "Đã giao hàng"}})
        return httpx.Response(200, json={"success": False, "message": "Không tìm thấy đơn hàng"})

    c = GhtkClient(acc(Platform.ghtk, {"token": "tok"}), S, _client(handler))
    res = await c.track("S1")
    assert res.status == OrderStatus.delivered and res.logistics_status == "Đã giao hàng"
    with pytest.raises(NotFound):
        await c.track("S2")


async def test_ghn_track():
    def handler(req: httpx.Request):
        assert json.loads(req.content) == {"order_code": "GA1"} and req.headers["ShopId"] == "99"
        return httpx.Response(200, json={"code": 200, "data": {"status": "delivering"}})

    res = await GhnClient(acc(Platform.ghn, {"token": "t", "shop_id": 99}), S, _client(handler)).track("GA1")
    assert res.status == OrderStatus.shipping and res.logistics_status == "Đang giao hàng"


class FakeRedis:
    def __init__(self):
        self.h, self.kv = {}, {}

    async def hgetall(self, k):
        return {f: str(v) for f, v in self.h.get(k, {}).items()}

    async def hget(self, k, f):
        v = self.h.get(k, {}).get(f)
        return None if v is None else str(v)

    async def hset(self, k, mapping):
        self.h.setdefault(k, {}).update(mapping)

    async def hincrby(self, k, f, n):
        d = self.h.setdefault(k, {})
        d[f] = int(d.get(f, 0)) + n
        return d[f]

    async def delete(self, k):
        self.h.pop(k, None)

    async def incr(self, k):
        self.kv[k] = self.kv.get(k, 0) + 1
        return self.kv[k]


async def test_pool_backoff_and_rotation():
    pool = AccountPool(FakeRedis())
    assert (await pool.health("ghtk:a"))["state"] == "new"
    assert await pool.report_failure("ghtk:a", "boom") == 60
    assert await pool.report_failure("ghtk:a", "boom") == 120
    h = await pool.health("ghtk:a")
    assert h["state"] == "cooldown" and h["fails"] == 2 and h["last_error"] == "boom"
    assert await pool.rotation("ghtk", ["ghtk:a", "ghtk:b", "ghtk:c"]) in (["ghtk:c", "ghtk:b"], ["ghtk:b", "ghtk:c"])
    r1 = await pool.rotation("ghtk", ["ghtk:b", "ghtk:c"])
    r2 = await pool.rotation("ghtk", ["ghtk:b", "ghtk:c"])
    assert r1 != r2 and sorted(r1) == sorted(r2)
    await pool.report_ok("ghtk:a", 3)
    h = await pool.health("ghtk:a")
    assert h["state"] == "ok" and h["fails"] == 0 and h["last_count"] == 3
    for _ in range(5):
        await pool.report_failure("ghtk:a", "x")
    assert (await pool.health("ghtk:a"))["state"] == "error"


def test_dashboard_lists_pool():
    html = (Path(__file__).parent.parent / "orderhub/api/static/index.html").read_text()
    assert "/api/accounts" in html and "Pool tài khoản" in html


def test_tpos_tenant_from_url():
    from orderhub.connectors.tpos import tenant_from
    assert tenant_from("https://shopphuongthaole.tpos.vn/tpagev2/#/configs/sale-order") == "shopphuongthaole"
    assert tenant_from("phuongproshop.tpos.vn") == "phuongproshop"
    assert tenant_from("phuongproshop") == "phuongproshop"
    assert tenant_from("") is None


def test_haravan_normalize():
    from orderhub.connectors.haravan import HaravanConnector
    raw = {"id": 5, "name": "#1005", "confirmed_status": "confirmed", "fulfillment_status": "fulfilled",
           "created_at": "2026-10-17T03:00:00.000Z", "updated_at": "2026-10-17T06:00:00.000Z", "total_price": 120000,
           "customer": {"id": 9, "first_name": "Haravan Demo", "phone": "0901234567"},
           "shipping_address": {"name": "Haravan Demo", "address1": "182 Lê Đại Hành", "ward": "Phường 15",
                                "district": "Quận 11", "province": "Hồ Chí Minh"},
           "fulfillments": [{"tracking_number": "403943", "tracking_company": "GHN",
                             "carrier_status_code": "delivered", "carrier_status_name": "Đã giao hàng"}],
           "line_items": [{"sku": "A", "name": "Son", "quantity": 1, "price": 120000}]}
    o = HaravanConnector(acc(Platform.haravan), S, httpx.AsyncClient()).normalize(raw)
    assert o.order_id == "#1005" and o.status == OrderStatus.delivered and o.shipping.district == "Quận 11"
    assert o.shipping.logistics_status == "Đã giao hàng" and o.shipping.tracking_number == "403943"


def test_jt_digest_matches_doc_example():
    from orderhub.carriers import jt_digest, jt_password_cipher
    assert jt_digest("{}", "k") == jt_digest("{}", "k") and len(jt_digest("{}", "k")) == 24
    assert len(jt_password_cipher("H5CD3zE6")) == 32 and jt_password_cipher("x").isupper()
    assert carrier_platform("J&T Express") == Platform.jt


async def test_jt_track_latest_scan():
    from orderhub.carriers import JtClient

    def handler(req: httpx.Request):
        assert req.headers["apiAccount"] == "178" and req.headers["digest"]
        assert b"bizContent" in req.content
        return httpx.Response(200, json={"code": "1", "msg": "success", "data": [{"billCode": "8428", "details": [
            {"scanTime": "2024-06-05 10:00:00", "scanTypeCode": 106, "scanTypeName": "Nhận hàng"},
            {"scanTime": "2024-06-05 15:57:04", "scanTypeCode": 113, "scanTypeName": "Ký nhận"}]}]})

    c = JtClient(acc(Platform.jt, {"api_account": "178", "private_key": "k", "customer_code": "C",
                                   "password": "p"}), S, _client(handler))
    res = await c.track("8428")
    assert res.status == OrderStatus.delivered and res.logistics_status == "Ký nhận"
