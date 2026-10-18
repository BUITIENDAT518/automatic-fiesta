"""Bulk account import: maps CSV / Excel / JSON files from any platform into pool accounts.

Platform and column names are matched loosely (case, accents, spaces, `_vn` suffixes are ignored), so files like
`giaohangtietkiem_vn.xlsx` with columns `Tên shop | Token` or `nhanh_vn.csv` with `businessId, accessToken` work.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field

from orderhub.models import Platform, Shop


def norm(text: object) -> str:
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]", "", s)


PLATFORM_ALIASES: dict[str, Platform] = {}
for _p, _names in {
    Platform.shopee: ["shopee", "autoshopee"], Platform.tiktok: ["tiktok", "tiktokshop"],
    Platform.lazada: ["lazada"], Platform.sapo: ["sapo", "mysapo"], Platform.tpos: ["tpos", "tpage"],
    Platform.nhanh: ["nhanh", "nhanhvn"], Platform.pancake: ["pancake", "pancakepos", "pospancake"],
    Platform.haravan: ["haravan"], Platform.ghtk: ["ghtk", "giaohangtietkiem", "giaohangtietkiemvn"],
    Platform.ghn: ["ghn", "giaohangnhanh", "giaohangnhanhvn"], Platform.jt: ["jt", "jtexpress", "jandt", "jnt"],
    Platform.viettelpost: ["viettelpost", "vtp", "viettel"], Platform.vnpost: ["vnpost", "vietnampost", "buudien", "myvnpost"],
}.items():
    for _n in _names:
        PLATFORM_ALIASES[_n] = _p


def platform_from(text: object) -> Platform | None:
    key = norm(text)
    if not key:
        return None
    if key in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[key]
    for alias in sorted(PLATFORM_ALIASES, key=len, reverse=True):
        if len(alias) >= 4 and alias in key:
            return PLATFORM_ALIASES[alias]
    return None


COLUMN_ALIASES = {
    "platform": ["platform", "nentang", "san", "kenh", "loai"],
    "shop_id": ["shopid", "mashop", "macuahang", "storeid", "idshop", "id", "ma"],
    "name": ["name", "ten", "tenshop", "tenhienthi", "tencuahang", "shopname", "ghichu"],
    "username": ["username", "user", "taikhoan", "tendangnhap", "login", "email", "sodienthoai", "sdt"],
    "password": ["password", "pass", "matkhau", "mk"],
    "token": ["token", "accesstoken", "apitoken", "key", "apikey"],
    "tenant": ["tenant", "link", "url", "domain", "subdomain", "tenmien"],
    "store": ["store", "alias"],
    "business_id": ["businessid", "madoanhnghiep"],
    "partner_code": ["partnercode", "xclientsource", "clientsource"],
    "carrier_shop_id": ["ghnshopid", "shopidghn"],
    "api_account": ["apiaccount"], "private_key": ["privatekey"], "customer_code": ["customercode", "makhachhang"],
    "api_secret": ["apisecret", "secret"], "refresh_token": ["refreshtoken"], "expires_at": ["expiresat", "hethan"],
}
_COLUMN_LOOKUP = {a: canon for canon, aliases in COLUMN_ALIASES.items() for a in aliases}
TOKEN_FIELD = {Platform.ghtk: "token", Platform.ghn: "token", Platform.pancake: "api_key",
               Platform.viettelpost: "token", Platform.vnpost: "token"}
REQUIRED: dict[Platform, list[tuple[str, ...]]] = {
    Platform.sapo: [("store",), ("access_token", "api_key")], Platform.tpos: [("tenant",), ("username",), ("password",)],
    Platform.nhanh: [("access_token",), ("business_id",)], Platform.pancake: [("api_key",)],
    Platform.haravan: [("access_token",)], Platform.ghtk: [("token",)], Platform.ghn: [("token",)],
    Platform.jt: [("api_account",), ("private_key",), ("customer_code",), ("password",)],
    Platform.viettelpost: [("username", "token"), ("password", "token")],
    Platform.vnpost: [("username", "token"), ("password", "token")],
    Platform.shopee: [("refresh_token",)], Platform.tiktok: [("refresh_token",)], Platform.lazada: [("refresh_token",)],
}


@dataclass
class RowResult:
    row: int
    platform: str | None
    shop_id: str | None
    error: str | None = None
    account: Shop | None = field(default=None, repr=False)


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def map_row(raw: dict, default_platform: Platform | None, row_no: int) -> RowResult:
    fields: dict[str, str] = {}
    for col, value in raw.items():
        value = _cell(value)
        if not value:
            continue
        key = _COLUMN_LOOKUP.get(norm(col)) or re.sub(r"[^a-z0-9]+", "_", str(col).strip().lower()).strip("_")
        fields.setdefault(key, value)
    platform = platform_from(fields.pop("platform", "")) or default_platform
    if not platform:
        return RowResult(row_no, None, None, "không xác định được nền tảng (thêm cột 'platform' hoặc đặt tên file/sheet)")
    creds = dict(fields)
    shop_id = creds.pop("shop_id", None)
    name = creds.pop("name", None)
    if "token" in creds:
        creds[TOKEN_FIELD.get(platform, "access_token")] = creds.pop("token")
    if platform == Platform.ghn and "carrier_shop_id" in creds:
        creds["shop_id"] = creds.pop("carrier_shop_id")
    if platform == Platform.pancake and shop_id:
        creds.setdefault("shop_id", shop_id)
    if platform == Platform.tpos:
        from orderhub.connectors.tpos import tenant_from
        creds["tenant"] = tenant_from(creds.get("tenant") or shop_id) or ""
        shop_id = shop_id or creds["tenant"]
    if platform == Platform.sapo and "store" not in creds and creds.get("tenant"):
        creds["store"] = creds.pop("tenant").split("//")[-1].split(".")[0]
    missing = [" hoặc ".join(group) for group in REQUIRED.get(platform, []) if not any(creds.get(f) for f in group)]
    shop_id = (shop_id or creds.get("username") or creds.get("customer_code") or creds.get("store")
               or creds.get("shop_id") or creds.get("business_id"))
    if missing:
        return RowResult(row_no, platform.value, shop_id, f"thiếu {', '.join(missing)}")
    if not shop_id:
        return RowResult(row_no, platform.value, None, "thiếu mã shop / tài khoản")
    acc = Shop(platform=platform, shop_id=shop_id, name=name or f"{platform.value} {shop_id}", credentials=creds)
    return RowResult(row_no, platform.value, shop_id, None, acc)


def _rows_from_csv(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def _sheets_from_xlsx(data: bytes) -> list[tuple[str, list[dict]]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [r for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
        if len(rows) < 2:
            continue
        header = [_cell(h) or f"col{i}" for i, h in enumerate(rows[0])]
        out.append((ws.title, [dict(zip(header, r)) for r in rows[1:]]))
    return out


def _rows_from_json(data: bytes) -> list[tuple[str, list[dict]]]:
    obj = json.loads(data.decode("utf-8-sig"))
    if isinstance(obj, list):
        return [("", obj)]
    if isinstance(obj, dict):
        if all(isinstance(v, list) for v in obj.values()):
            return list(obj.items())
        return [("", [obj])]
    raise ValueError("JSON phải là object hoặc danh sách object")


def parse_file(filename: str, data: bytes) -> list[RowResult]:
    """Parse one uploaded file into per-row results; platform can come from a column, sheet name or file name."""
    file_platform = platform_from(re.sub(r"\.[a-z0-9]+$", "", filename.lower()))
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        groups = _sheets_from_xlsx(data)
    elif lower.endswith(".json") or data.lstrip()[:1] in (b"[", b"{"):
        groups = _rows_from_json(data)
    else:
        groups = [("", _rows_from_csv(data))]
    results: list[RowResult] = []
    n = 0
    for group, rows in groups:
        default = platform_from(group) or file_platform
        for raw in rows:
            n += 1
            results.append(map_row(raw, default, n))
    return results


TEMPLATE_CSV = (
    "platform,shop_id,name,username,password,token,tenant,store,business_id,api_account,private_key,customer_code\n"
    "tpos,shopphuongthaole,TPOS Phương Thảo,user,***,,shopphuongthaole,,,,,\n"
    "giaohangtietkiem_vn,ghtk-kho-hcm,GHTK kho HCM,,,TOKEN_GHTK,,,,,,\n"
    "nhanh_vn,185432,Nhanh Kho chính,,,ACCESS_TOKEN,,,185432,,,\n"
    "viettelpost,0901234567,Viettel Post shop,0901234567,***,,,,,,,\n"
)
