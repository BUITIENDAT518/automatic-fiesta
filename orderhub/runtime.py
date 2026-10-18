from __future__ import annotations

import asyncio
import logging
import signal

from orderhub.config import Settings
from orderhub.crypto import PiiCipher
from orderhub.db import Database
from orderhub.models import Platform, Shop

log = logging.getLogger("orderhub")

MOCK_SHOPS = [
    (Platform.shopee, "100001", "Shopee - Thời Trang Sài Gòn"),
    (Platform.shopee, "100002", "Shopee - Phụ Kiện Hà Nội"),
    (Platform.tiktok, "7494000001", "TikTok - Mỹ Phẩm Xinh"),
    (Platform.tiktok, "7494000002", "TikTok - Gia Dụng Tiện Ích"),
    (Platform.lazada, "200001", "Lazada - Điện Tử Số"),
    (Platform.lazada, "200002", "Lazada - Giày Dép Việt"),
    (Platform.sapo, "dongho-24h", "Sapo - Đồng Hồ 24h"),
    (Platform.tpos, "shop-me-be", "TPOS - Shop Mẹ & Bé"),
    (Platform.nhanh, "185432", "Nhanh.vn - Nội Thất Gỗ"),
    (Platform.pancake, "8812", "Pancake - Thời Trang Livestream"),
    (Platform.haravan, "haravan-myphamxanh", "Haravan - Mỹ Phẩm Xanh"),
    (Platform.jt, "jt-084LC00001", "J&T - KH 084LC00001"),
    (Platform.ghtk, "ghtk-kho-hcm", "GHTK - Kho HCM"),
    (Platform.ghtk, "ghtk-kho-hn", "GHTK - Kho Hà Nội"),
    (Platform.ghn, "ghn-2510001", "GHN - Shop 2510001"),
]


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def open_db(settings: Settings) -> Database:
    db = await Database.connect(settings.database_url, PiiCipher(settings.pii_key))
    await db.init_schema()
    if settings.seed_mock_shops and await db.count_shops() == 0:
        for platform, shop_id, name in MOCK_SHOPS:
            await db.upsert_shop(Shop(platform=platform, shop_id=shop_id, name=name, credentials={"mock": True}))
        log.info("seeded %d mock shops", len(MOCK_SHOPS))
    return db


def stop_event() -> asyncio.Event:
    ev = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, ev.set)
    return ev
