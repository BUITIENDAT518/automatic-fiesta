from __future__ import annotations

import httpx

from orderhub.config import Settings
from orderhub.connectors.base import Connector, ConnectorError
from orderhub.connectors.lazada import LazadaConnector
from orderhub.connectors.mock import MockConnector
from orderhub.connectors.nhanh import NhanhConnector
from orderhub.connectors.pancake import PancakeConnector
from orderhub.connectors.sapo import SapoConnector
from orderhub.connectors.shopee import ShopeeConnector
from orderhub.connectors.tiktok import TikTokConnector
from orderhub.connectors.tpos import TposConnector
from orderhub.models import Platform, Shop

REGISTRY: dict[Platform, type[Connector]] = {
    Platform.shopee: ShopeeConnector,
    Platform.tiktok: TikTokConnector,
    Platform.lazada: LazadaConnector,
    Platform.sapo: SapoConnector,
    Platform.tpos: TposConnector,
    Platform.nhanh: NhanhConnector,
    Platform.pancake: PancakeConnector,
}


def build_connector(shop: Shop, settings: Settings, http: httpx.AsyncClient) -> Connector:
    cls = MockConnector if shop.credentials.get("mock") else REGISTRY[shop.platform]
    return cls(shop, settings, http)


__all__ = ["REGISTRY", "Connector", "ConnectorError", "build_connector"]
