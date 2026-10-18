from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://orderhub:orderhub@localhost:5432/orderhub"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    pii_key: str = field(default_factory=lambda: _env("PII_KEY", ""))
    api_key_viewer: str = field(default_factory=lambda: _env("API_KEY_VIEWER", "viewer-dev-key"))
    api_key_admin: str = field(default_factory=lambda: _env("API_KEY_ADMIN", "admin-dev-key"))
    window_hours: int = field(default_factory=lambda: int(_env("WINDOW_HOURS", "48")))
    retention_hours: int = field(default_factory=lambda: int(_env("RETENTION_HOURS", "72")))
    poll_interval_s: int = field(default_factory=lambda: int(_env("POLL_INTERVAL_S", "30")))
    poll_overlap_s: int = field(default_factory=lambda: int(_env("POLL_OVERLAP_S", "300")))
    max_concurrent_shops: int = field(default_factory=lambda: int(_env("MAX_CONCURRENT_SHOPS", "8")))
    seed_mock_shops: bool = field(default_factory=lambda: _env("SEED_MOCK_SHOPS", "false").lower() == "true")
    worker_id: str = field(default_factory=lambda: _env("WORKER_ID", f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"))
    shopee_partner_id: str = field(default_factory=lambda: _env("SHOPEE_PARTNER_ID", ""))
    shopee_partner_key: str = field(default_factory=lambda: _env("SHOPEE_PARTNER_KEY", ""))
    shopee_host: str = field(default_factory=lambda: _env("SHOPEE_HOST", "https://partner.shopeemobile.com"))
    tiktok_app_key: str = field(default_factory=lambda: _env("TIKTOK_APP_KEY", ""))
    tiktok_app_secret: str = field(default_factory=lambda: _env("TIKTOK_APP_SECRET", ""))
    tiktok_host: str = field(default_factory=lambda: _env("TIKTOK_HOST", "https://open-api.tiktokglobalshop.com"))
    tiktok_auth_host: str = field(default_factory=lambda: _env("TIKTOK_AUTH_HOST", "https://auth.tiktok-shops.com"))
    lazada_app_key: str = field(default_factory=lambda: _env("LAZADA_APP_KEY", ""))
    lazada_app_secret: str = field(default_factory=lambda: _env("LAZADA_APP_SECRET", ""))
    lazada_host: str = field(default_factory=lambda: _env("LAZADA_HOST", "https://api.lazada.vn/rest"))
    lazada_auth_host: str = field(default_factory=lambda: _env("LAZADA_AUTH_HOST", "https://auth.lazada.com/rest"))
    nhanh_app_id: str = field(default_factory=lambda: _env("NHANH_APP_ID", ""))
    nhanh_host: str = field(default_factory=lambda: _env("NHANH_HOST", "https://pos.open.nhanh.vn"))
    pancake_host: str = field(default_factory=lambda: _env("PANCAKE_HOST", "https://pos.pages.fm/api/v1"))
    ghtk_host: str = field(default_factory=lambda: _env("GHTK_HOST", "https://services.giaohangtietkiem.vn"))
    ghn_host: str = field(default_factory=lambda: _env("GHN_HOST", "https://online-gateway.ghn.vn/shiip/public-api"))
    haravan_host: str = field(default_factory=lambda: _env("HARAVAN_HOST", "https://apis.haravan.com"))
    jt_host: str = field(default_factory=lambda: _env("JT_HOST", "https://ylopenapi.jtexpress.vn/webopenplatformapi/api"))
    track_interval_s: int = field(default_factory=lambda: int(_env("TRACK_INTERVAL_S", "300")))


STREAM_INGEST = "orders.ingest"
STREAM_DLQ = "orders.dlq"
CHANNEL_UPDATES = "orders.updates"
PROCESSOR_GROUP = "processors"


def get_settings() -> Settings:
    return Settings()
