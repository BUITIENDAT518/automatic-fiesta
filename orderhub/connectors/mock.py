"""Simulated marketplace for demos and local development: realistic Vietnamese orders, no credentials needed."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from orderhub.connectors.base import Connector
from orderhub.models import Customer, Item, NormalizedOrder, OrderStatus, Platform, Shipping

LAST = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Ngô", "Dương"]
MID = ["Văn", "Thị", "Minh", "Ngọc", "Thanh", "Hoàng", "Quốc", "Thu", "Hữu", "Bảo"]
FIRST = ["An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hải", "Hương", "Khoa", "Lan", "Linh", "Long", "Mai",
         "Nam", "Nhung", "Phúc", "Quân", "Sơn", "Tâm", "Thảo", "Trang", "Tuấn", "Vy", "Yến"]
PLACES = [
    ("Phường Bến Nghé", "Quận 1", "TP. Hồ Chí Minh", 18), ("Phường Thảo Điền", "TP. Thủ Đức", "Hồ Chí Minh", 8),
    ("Phường Dĩ An", "TP. Dĩ An", "Bình Dương", 5), ("Phường Tân Hiệp", "TP. Biên Hòa", "Đồng Nai", 4),
    ("Phường Cái Khế", "Quận Ninh Kiều", "Cần Thơ", 4), ("Phường 7", "TP. Vũng Tàu", "Bà Rịa - Vũng Tàu", 2),
    ("Phường Mỹ Bình", "TP. Long Xuyên", "An Giang", 2), ("Phường 1", "TP. Mỹ Tho", "Tiền Giang", 2),
    ("Phường Tràng Tiền", "Quận Hoàn Kiếm", "Hà Nội", 16), ("Phường Dịch Vọng", "Quận Cầu Giấy", "Thành phố Hà Nội", 6),
    ("Phường Lê Chân", "Quận Lê Chân", "Hải Phòng", 4), ("Phường Suối Hoa", "TP. Bắc Ninh", "Bắc Ninh", 3),
    ("Phường Hồng Gai", "TP. Hạ Long", "Quảng Ninh", 2), ("Phường Tân Thịnh", "TP. Thái Nguyên", "Thái Nguyên", 2),
    ("Phường Trần Hưng Đạo", "TP. Nam Định", "Nam Định", 2), ("Phường Đông Sơn", "TP. Thanh Hóa", "Thanh Hóa", 3),
    ("Phường Hải Châu 1", "Quận Hải Châu", "Đà Nẵng", 5), ("Phường Vĩnh Ninh", "TP. Huế", "Thừa Thiên Huế", 2),
    ("Phường Hưng Dũng", "TP. Vinh", "Nghệ An", 3), ("Phường Lộc Thọ", "TP. Nha Trang", "Khánh Hòa", 2),
    ("Phường Tân Lợi", "TP. Buôn Ma Thuột", "Đắk Lắk", 2), ("Phường 1", "TP. Đà Lạt", "Lâm Đồng", 2),
    ("Phường Trần Phú", "TP. Quy Nhơn", "Bình Định", 2),
]
STREETS = ["Lê Lợi", "Nguyễn Huệ", "Trần Hưng Đạo", "Hai Bà Trưng", "Lý Thường Kiệt", "Điện Biên Phủ", "Quang Trung"]
CARRIERS = {
    Platform.shopee: ["SPX Express", "Giao Hàng Nhanh", "J&T Express", "Ninja Van", "Viettel Post"],
    Platform.tiktok: ["J&T Express", "Giao Hàng Nhanh", "BEST Express", "Ninja Van"],
    Platform.lazada: ["Lazada Express", "Giao Hàng Tiết Kiệm", "Ninja Van", "J&T Express"],
}
PRODUCTS = [("AO-THUN-01", "Áo thun cotton basic", 129000), ("QUAN-JEAN-02", "Quần jean slim fit", 349000),
            ("TAI-NGHE-03", "Tai nghe Bluetooth TWS", 259000), ("SAC-DU-PHONG-04", "Sạc dự phòng 10000mAh", 299000),
            ("SERUM-05", "Serum vitamin C 30ml", 189000), ("GIAY-06", "Giày sneaker trắng", 459000),
            ("BINH-NUOC-07", "Bình giữ nhiệt 500ml", 159000), ("OP-LUNG-08", "Ốp lưng iPhone trong suốt", 49000)]
FLOW = [OrderStatus.pending, OrderStatus.ready_to_ship, OrderStatus.shipping, OrderStatus.delivered]
RAW = {OrderStatus.pending: "UNPAID", OrderStatus.ready_to_ship: "READY_TO_SHIP", OrderStatus.shipping: "SHIPPED",
       OrderStatus.delivered: "COMPLETED", OrderStatus.cancelled: "CANCELLED", OrderStatus.returned: "TO_RETURN"}
LOGISTICS = {OrderStatus.pending: None, OrderStatus.ready_to_ship: "Chờ lấy hàng", OrderStatus.shipping: "Đang vận chuyển",
             OrderStatus.delivered: "Giao thành công", OrderStatus.cancelled: "Đã hủy", OrderStatus.returned: "Đang hoàn hàng"}

_STATE: dict[str, dict[str, NormalizedOrder]] = {}


class MockConnector(Connector):
    async def refresh_token(self) -> None:
        self.creds.update(access_token="mock", expires_at=int(datetime.now().timestamp()) + 86400)

    def _rng(self) -> random.Random:
        return random.Random()

    def _new_order(self, rng: random.Random, created: datetime) -> NormalizedOrder:
        platform = self.shop.platform
        ward, district, province, _ = rng.choices(PLACES, weights=[p[3] for p in PLACES])[0]
        name = f"{rng.choice(LAST)} {rng.choice(MID)} {rng.choice(FIRST)}"
        phone = "0" + rng.choice(["3", "5", "7", "8", "9"]) + "".join(rng.choices("0123456789", k=8))
        picks = rng.sample(PRODUCTS, k=rng.choice([1, 1, 1, 2, 3]))
        items = [Item(sku=s, name=n, quantity=rng.choice([1, 1, 2]), price=Decimal(p)) for s, n, p in picks]
        total = sum(i.price * i.quantity for i in items) + Decimal(rng.choice([0, 15000, 22000, 30000]))
        age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        stage = min(len(FLOW) - 1, int(age_h / rng.uniform(4, 14)))
        status = FLOW[stage]
        r = rng.random()
        if r < 0.05:
            status = OrderStatus.cancelled
        elif r < 0.07 and stage >= 2:
            status = OrderStatus.returned
        prefix = {"shopee": "", "tiktok": "57", "lazada": "4"}[platform.value]
        order_id = prefix + created.strftime("%y%m%d") + "".join(rng.choices("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ", k=8))
        carrier = rng.choice(CARRIERS[platform])
        return NormalizedOrder(
            platform=platform, shop_id=self.shop.shop_id, shop_name=self.shop.name, order_id=order_id,
            status=status, raw_status=RAW[status], created_at=created, updated_at=created, total_amount=total,
            customer=Customer(id=f"u{rng.randint(10**6, 10**7)}", name=name, phone=phone),
            shipping=Shipping(
                recipient_name=name, phone=phone,
                address=f"{rng.randint(1, 300)} {rng.choice(STREETS)}, {ward}, {district}, {province}",
                ward=ward, district=district, province=None if rng.random() < 0.3 else province, carrier=carrier,
                tracking_number=None if status == OrderStatus.pending else f"{carrier[:2].upper()}{rng.randint(10**9, 10**10)}VN",
                logistics_status=LOGISTICS[status],
            ),
            items=items,
        )

    async def fetch_orders(self, since: datetime, until: datetime) -> AsyncIterator[NormalizedOrder]:
        rng = self._rng()
        state = _STATE.setdefault(self.shop.key, {})
        now = datetime.now(timezone.utc)
        if not state and until - since > timedelta(hours=1):
            n = rng.randint(120, 220)
            for _ in range(n):
                created = until - timedelta(seconds=rng.uniform(0, (until - since).total_seconds()))
                o = self._new_order(rng, created)
                state[o.order_id] = o
                yield o
            return
        for _ in range(rng.randint(0, 4)):
            o = self._new_order(rng, now - timedelta(seconds=rng.uniform(0, 20)))
            state[o.order_id] = o
            yield o
        for o in rng.sample(list(state.values()), k=min(len(state), rng.randint(0, 3))):
            if o.status in FLOW[:-1]:
                o.status = FLOW[FLOW.index(o.status) + 1]
                o.raw_status, o.updated_at = RAW[o.status], now
                o.shipping.logistics_status = LOGISTICS[o.status]
                if not o.shipping.tracking_number:
                    o.shipping.tracking_number = f"{(o.shipping.carrier or 'XX')[:2].upper()}{rng.randint(10**9, 10**10)}VN"
                yield o
        cutoff = now - timedelta(hours=self.settings.retention_hours)
        for k in [k for k, o in state.items() if o.created_at < cutoff]:
            del state[k]
