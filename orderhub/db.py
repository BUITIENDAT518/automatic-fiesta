from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import asyncpg

from orderhub.crypto import PiiCipher, mask_name, mask_phone
from orderhub.models import NormalizedOrder, Platform, Shop

SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
  id              SERIAL PRIMARY KEY,
  platform        TEXT NOT NULL,
  shop_id         TEXT NOT NULL,
  name            TEXT NOT NULL,
  active          BOOLEAN NOT NULL DEFAULT TRUE,
  credentials_enc TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (platform, shop_id)
);

CREATE TABLE IF NOT EXISTS orders (
  platform           TEXT NOT NULL,
  shop_id            TEXT NOT NULL,
  order_id           TEXT NOT NULL,
  shop_name          TEXT,
  status             TEXT NOT NULL,
  raw_status         TEXT,
  created_at         TIMESTAMPTZ NOT NULL,
  updated_at         TIMESTAMPTZ NOT NULL,
  total_amount       NUMERIC(18, 2) NOT NULL DEFAULT 0,
  currency           TEXT NOT NULL DEFAULT 'VND',
  customer_id        TEXT,
  customer_name_enc  TEXT,
  customer_phone_enc TEXT,
  customer_email_enc TEXT,
  recipient_name_enc TEXT,
  recipient_phone_enc TEXT,
  address_enc        TEXT,
  ward               TEXT,
  district           TEXT,
  province           TEXT,
  province_legacy    TEXT,
  region             TEXT,
  subregion          TEXT,
  carrier            TEXT,
  tracking_number    TEXT,
  logistics_status   TEXT,
  items              JSONB NOT NULL DEFAULT '[]',
  ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (platform, shop_id, order_id)
);

CREATE INDEX IF NOT EXISTS orders_created_idx ON orders (created_at DESC);
CREATE INDEX IF NOT EXISTS orders_region_idx ON orders (region, province);
CREATE INDEX IF NOT EXISTS orders_shop_idx ON orders (platform, shop_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders (status);
"""

UPSERT = """
INSERT INTO orders (
  platform, shop_id, order_id, shop_name, status, raw_status, created_at, updated_at, total_amount, currency,
  customer_id, customer_name_enc, customer_phone_enc, customer_email_enc,
  recipient_name_enc, recipient_phone_enc, address_enc, ward, district, province, province_legacy,
  region, subregion, carrier, tracking_number, logistics_status, items, ingested_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,now())
ON CONFLICT (platform, shop_id, order_id) DO UPDATE SET
  shop_name = EXCLUDED.shop_name, status = EXCLUDED.status, raw_status = EXCLUDED.raw_status,
  updated_at = EXCLUDED.updated_at, total_amount = EXCLUDED.total_amount, currency = EXCLUDED.currency,
  customer_id = EXCLUDED.customer_id, customer_name_enc = EXCLUDED.customer_name_enc,
  customer_phone_enc = EXCLUDED.customer_phone_enc, customer_email_enc = EXCLUDED.customer_email_enc,
  recipient_name_enc = EXCLUDED.recipient_name_enc, recipient_phone_enc = EXCLUDED.recipient_phone_enc,
  address_enc = EXCLUDED.address_enc, ward = EXCLUDED.ward, district = EXCLUDED.district,
  province = EXCLUDED.province, province_legacy = EXCLUDED.province_legacy, region = EXCLUDED.region,
  subregion = EXCLUDED.subregion, carrier = EXCLUDED.carrier,
  tracking_number = COALESCE(EXCLUDED.tracking_number, orders.tracking_number),
  logistics_status = EXCLUDED.logistics_status, items = EXCLUDED.items, ingested_at = now()
WHERE EXCLUDED.updated_at >= orders.updated_at
RETURNING (xmax = 0) AS inserted
"""

FILTERS = {
    "platform": "platform = ${}",
    "shop_id": "shop_id = ${}",
    "region": "region = ${}",
    "province": "province = ${}",
    "status": "status = ${}",
    "carrier": "carrier = ${}",
}


class Database:
    def __init__(self, pool: asyncpg.Pool, cipher: PiiCipher):
        self.pool, self.cipher = pool, cipher

    @classmethod
    async def connect(cls, dsn: str, cipher: PiiCipher) -> Database:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        return cls(pool, cipher)

    async def close(self) -> None:
        await self.pool.close()

    async def init_schema(self) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(827364)")
            await conn.execute(SCHEMA)

    # shops ----------------------------------------------------------------
    async def upsert_shop(self, shop: Shop) -> int:
        return await self.pool.fetchval(
            """INSERT INTO shops (platform, shop_id, name, active, credentials_enc)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (platform, shop_id) DO UPDATE SET name = EXCLUDED.name, active = EXCLUDED.active,
                 credentials_enc = EXCLUDED.credentials_enc, updated_at = now()
               RETURNING id""",
            shop.platform.value, shop.shop_id, shop.name, shop.active, self.cipher.encrypt_json(shop.credentials),
        )

    async def save_credentials(self, shop: Shop) -> None:
        await self.pool.execute(
            "UPDATE shops SET credentials_enc = $3, updated_at = now() WHERE platform = $1 AND shop_id = $2",
            shop.platform.value, shop.shop_id, self.cipher.encrypt_json(shop.credentials),
        )

    async def list_shops(self, active_only: bool = True, with_credentials: bool = False) -> list[Shop]:
        rows = await self.pool.fetch(
            "SELECT * FROM shops" + (" WHERE active" if active_only else "") + " ORDER BY platform, name"
        )
        return [
            Shop(
                id=r["id"], platform=Platform(r["platform"]), shop_id=r["shop_id"], name=r["name"], active=r["active"],
                credentials=self.cipher.decrypt_json(r["credentials_enc"]) if with_credentials else {},
            )
            for r in rows
        ]

    async def count_shops(self) -> int:
        return await self.pool.fetchval("SELECT count(*) FROM shops")

    # orders ---------------------------------------------------------------
    async def upsert_order(self, o: NormalizedOrder) -> bool | None:
        """Returns True if inserted, False if updated, None if skipped as stale."""
        c, s, enc = o.customer, o.shipping, self.cipher.encrypt
        return await self.pool.fetchval(
            UPSERT,
            o.platform.value, o.shop_id, o.order_id, o.shop_name, o.status.value, o.raw_status,
            o.created_at, o.updated_at, o.total_amount, o.currency,
            c.id, enc(c.name), enc(c.phone), enc(c.email),
            enc(s.recipient_name), enc(s.phone), enc(s.address), s.ward, s.district, s.province, s.province_legacy,
            s.region, s.subregion, s.carrier, s.tracking_number, s.logistics_status,
            json.dumps([i.model_dump(mode="json") for i in o.items]),
        )

    async def purge_older_than(self, hours: int) -> int:
        res = await self.pool.execute(
            "DELETE FROM orders WHERE created_at < $1", datetime.now(timezone.utc) - timedelta(hours=hours)
        )
        return int(res.split()[-1])

    def _where(self, hours: int, filters: dict, q: str | None) -> tuple[str, list]:
        clauses, args = ["created_at >= $1"], [datetime.now(timezone.utc) - timedelta(hours=hours)]
        for k, tmpl in FILTERS.items():
            if filters.get(k):
                args.append(filters[k])
                clauses.append(tmpl.format(len(args)))
        if q:
            args.append(f"%{q}%")
            n = len(args)
            clauses.append(f"(order_id ILIKE ${n} OR tracking_number ILIKE ${n} OR shop_name ILIKE ${n})")
        return " AND ".join(clauses), args

    def _row_to_dict(self, r: asyncpg.Record, reveal_pii: bool) -> dict:
        dec = self.cipher.decrypt
        name, phone = dec(r["customer_name_enc"]), dec(r["customer_phone_enc"])
        rname, rphone = dec(r["recipient_name_enc"]), dec(r["recipient_phone_enc"])
        address = dec(r["address_enc"])
        if not reveal_pii:
            name, phone, rname, rphone = mask_name(name), mask_phone(phone), mask_name(rname), mask_phone(rphone)
            address = ", ".join(p for p in [r["ward"], r["district"], r["province"]] if p) or None
        return {
            "platform": r["platform"], "shop_id": r["shop_id"], "shop_name": r["shop_name"], "order_id": r["order_id"],
            "status": r["status"], "raw_status": r["raw_status"],
            "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
            "total_amount": float(r["total_amount"]), "currency": r["currency"],
            "customer": {"id": r["customer_id"], "name": name, "phone": phone,
                         "email": dec(r["customer_email_enc"]) if reveal_pii else None},
            "shipping": {
                "recipient_name": rname, "phone": rphone, "address": address, "ward": r["ward"],
                "district": r["district"], "province": r["province"], "province_legacy": r["province_legacy"],
                "region": r["region"], "subregion": r["subregion"], "carrier": r["carrier"],
                "tracking_number": r["tracking_number"], "logistics_status": r["logistics_status"],
            },
            "items": json.loads(r["items"]) if isinstance(r["items"], str) else r["items"],
        }

    async def query_orders(
        self, hours: int, filters: dict, q: str | None, limit: int, offset: int, reveal_pii: bool
    ) -> tuple[int, list[dict]]:
        where, args = self._where(hours, filters, q)
        total = await self.pool.fetchval(f"SELECT count(*) FROM orders WHERE {where}", *args)
        rows = await self.pool.fetch(
            f"SELECT * FROM orders WHERE {where} ORDER BY created_at DESC LIMIT {int(limit)} OFFSET {int(offset)}",
            *args,
        )
        return total, [self._row_to_dict(r, reveal_pii) for r in rows]

    async def get_order(self, platform: str, shop_id: str, order_id: str, reveal_pii: bool) -> dict | None:
        r = await self.pool.fetchrow(
            "SELECT * FROM orders WHERE platform=$1 AND shop_id=$2 AND order_id=$3", platform, shop_id, order_id
        )
        return self._row_to_dict(r, reveal_pii) if r else None

    async def stats(self, hours: int, filters: dict) -> dict:
        where, args = self._where(hours, filters, None)
        totals = await self.pool.fetchrow(
            f"SELECT count(*) AS orders, COALESCE(sum(total_amount),0) AS revenue FROM orders WHERE {where}", *args
        )
        out: dict = {"orders": totals["orders"], "revenue": float(totals["revenue"]), "window_hours": hours}
        for dim in ("region", "province", "platform", "shop_name", "status", "carrier"):
            rows = await self.pool.fetch(
                f"SELECT COALESCE({dim}, 'Không xác định') AS k, count(*) AS n, COALESCE(sum(total_amount),0) AS rev "
                f"FROM orders WHERE {where} GROUP BY 1 ORDER BY n DESC LIMIT 40",
                *args,
            )
            out[f"by_{dim}"] = [{"key": r["k"], "orders": r["n"], "revenue": float(r["rev"])} for r in rows]
        hourly = await self.pool.fetch(
            f"SELECT date_trunc('hour', created_at) AS h, count(*) AS n FROM orders WHERE {where} GROUP BY 1 ORDER BY 1",
            *args,
        )
        out["by_hour"] = [{"key": r["h"].isoformat(), "orders": r["n"]} for r in hourly]
        return out
