import io
import json

import httpx
from openpyxl import Workbook

from orderhub.carriers import CarrierError, NotFound, ViettelPostClient
from orderhub.config import Settings
from orderhub.importer import parse_file, platform_from
from orderhub.models import Platform, Shop
from orderhub.telegram_bot.__main__ import admin_ids, handle_update


def test_platform_aliases():
    assert platform_from("giaohangtietkiem_vn") == Platform.ghtk
    assert platform_from("Giao Hàng Nhanh") == Platform.ghn
    assert platform_from("nhanh_vn") == Platform.nhanh
    assert platform_from("Viettel Post") == Platform.viettelpost
    assert platform_from("VNPost") == Platform.vnpost
    assert platform_from("J&T") == Platform.jt
    assert platform_from("tai_khoan_tpos_2025") == Platform.tpos
    assert platform_from("abc") is None


def test_csv_with_platform_column_and_vietnamese_headers():
    data = ("Nền tảng;Mã shop;Tên shop;Tài khoản;Mật khẩu;Token;Link\n"
            "tpos;;TPOS Thảo;u1;p1;;https://shopphuongthaole.tpos.vn/tpagev2/#/x\n"
            "giaohangtietkiem_vn;kho-hcm;GHTK HCM;;;TK123;\n"
            "nhanh_vn;185;Nhanh;;;AT;\n").encode()
    rows = parse_file("tai_khoan.csv", data)
    tpos, ghtk, nhanh = rows
    assert tpos.account.platform == Platform.tpos and tpos.shop_id == "shopphuongthaole"
    assert tpos.account.credentials == {"username": "u1", "password": "p1", "tenant": "shopphuongthaole"}
    assert ghtk.account.credentials == {"token": "TK123"} and ghtk.account.name == "GHTK HCM"
    assert nhanh.account is None and "business_id" in nhanh.error


def test_platform_from_filename_and_xlsx_sheets():
    wb = Workbook()
    ws = wb.active
    ws.title = "GHN"
    ws.append(["Tên", "Token", "ShopId GHN"])
    ws.append(["GHN 1", "tok", 2510001])
    ws2 = wb.create_sheet("viettelpost")
    ws2.append(["SĐT", "Mật khẩu"])
    ws2.append(["0901234567", "pw"])
    buf = io.BytesIO()
    wb.save(buf)
    ghn, vtp = parse_file("all.xlsx", buf.getvalue())
    assert ghn.account.platform == Platform.ghn and ghn.account.credentials == {"token": "tok", "shop_id": "2510001"}
    assert ghn.error is None and ghn.shop_id == "2510001"
    assert vtp.account.platform == Platform.viettelpost and vtp.shop_id == "0901234567"
    rows = parse_file("pancake.json", json.dumps([{"shop_id": "8812", "api_key": "k"}]).encode())
    assert rows[0].account.platform == Platform.pancake and rows[0].account.credentials["api_key"] == "k"


def test_missing_platform_reported():
    (row,) = parse_file("x.csv", b"name,token\nA,B\n")
    assert row.account is None and "nền tảng" in row.error


async def test_viettelpost_login():
    def handler(req: httpx.Request):
        body = json.loads(req.content)
        if body["PASSWORD"] == "ok":
            return httpx.Response(200, json={"status": 200, "error": False, "data": {"token": "T"}})
        return httpx.Response(200, json={"status": 204, "error": True, "message": "Username or password is not valid!"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    good = ViettelPostClient(Shop(platform=Platform.viettelpost, shop_id="1", name="v",
                                  credentials={"username": "u", "password": "ok"}), Settings(), http)
    await good.login()
    bad = ViettelPostClient(Shop(platform=Platform.viettelpost, shop_id="1", name="v",
                                 credentials={"username": "u", "password": "no"}), Settings(), http)
    try:
        await bad.login()
        raise AssertionError
    except CarrierError as e:
        assert "not valid" in str(e)
    try:
        await good.track("X")
        raise AssertionError
    except NotFound:
        pass


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))


async def test_bot_rejects_non_admin():
    bot = FakeBot()
    await handle_update({"message": {"chat": {"id": 5}, "from": {"id": 42}, "text": "/pool"}},
                        bot, Settings(), None, None, None, admin_ids("1, 2"))
    assert "42" in bot.sent[0][1] and "quyền" in bot.sent[0][1]
    assert admin_ids("1;2, x,-100") == {1, 2, -100}


def test_report_csv_and_digest():
    from orderhub.report import digest_text, rows_to_csv
    rows = [{"created_at": "2026-10-18T01:00:00+00:00", "updated_at": "2026-10-18T02:00:00+00:00",
             "platform": "shopee", "shop_name": "Shop A", "order_id": "X1", "status": "delivered",
             "raw_status": "COMPLETED", "total_amount": 120000.0, "currency": "VND",
             "customer": {"name": "Nguyễn A", "phone": "0900000000"},
             "shipping": {"recipient_name": "Nguyễn A", "address": "1 Lê Lợi", "province": "Hồ Chí Minh",
                          "region": "Nam", "carrier": "GHTK", "tracking_number": "S1.1",
                          "logistics_status": "Đã giao"},
             "items": [{"name": "Áo", "quantity": 2}]}]
    csv_text = rows_to_csv(rows)
    assert csv_text.startswith("\ufeff") and "order_id" in csv_text.splitlines()[0]
    assert "Áo x2" in csv_text and "Hồ Chí Minh" in csv_text
    txt = digest_text({"orders": 10, "revenue": 1000000, "by_platform": [{"key": "shopee", "orders": 10}],
                       "by_region": [{"key": "Nam", "orders": 10}],
                       "by_status": [{"key": "delivered", "orders": 7}]}, 24)
    assert "Tổng đơn: 10" in txt and "Đã giao: 7" in txt and "shopee" in txt
