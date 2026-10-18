"""Vietnam province -> region lookup, covering both the 34 post-2025 provinces and the 63 legacy names."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

BAC, TRUNG, NAM = "Miền Bắc", "Miền Trung", "Miền Nam"

# legacy province -> (subregion, province after the 2025-07-01 merger)
_LEGACY: dict[str, tuple[str, str]] = {
    # Đồng bằng sông Hồng
    "Hà Nội": ("Đồng bằng sông Hồng", "Hà Nội"),
    "Hải Phòng": ("Đồng bằng sông Hồng", "Hải Phòng"),
    "Hải Dương": ("Đồng bằng sông Hồng", "Hải Phòng"),
    "Bắc Ninh": ("Đồng bằng sông Hồng", "Bắc Ninh"),
    "Hà Nam": ("Đồng bằng sông Hồng", "Ninh Bình"),
    "Nam Định": ("Đồng bằng sông Hồng", "Ninh Bình"),
    "Ninh Bình": ("Đồng bằng sông Hồng", "Ninh Bình"),
    "Hưng Yên": ("Đồng bằng sông Hồng", "Hưng Yên"),
    "Thái Bình": ("Đồng bằng sông Hồng", "Hưng Yên"),
    "Vĩnh Phúc": ("Đồng bằng sông Hồng", "Phú Thọ"),
    "Quảng Ninh": ("Đồng bằng sông Hồng", "Quảng Ninh"),
    # Đông Bắc
    "Hà Giang": ("Đông Bắc Bộ", "Tuyên Quang"),
    "Tuyên Quang": ("Đông Bắc Bộ", "Tuyên Quang"),
    "Cao Bằng": ("Đông Bắc Bộ", "Cao Bằng"),
    "Bắc Kạn": ("Đông Bắc Bộ", "Thái Nguyên"),
    "Thái Nguyên": ("Đông Bắc Bộ", "Thái Nguyên"),
    "Lạng Sơn": ("Đông Bắc Bộ", "Lạng Sơn"),
    "Phú Thọ": ("Đông Bắc Bộ", "Phú Thọ"),
    "Bắc Giang": ("Đông Bắc Bộ", "Bắc Ninh"),
    "Lào Cai": ("Đông Bắc Bộ", "Lào Cai"),
    "Yên Bái": ("Đông Bắc Bộ", "Lào Cai"),
    # Tây Bắc
    "Lai Châu": ("Tây Bắc Bộ", "Lai Châu"),
    "Điện Biên": ("Tây Bắc Bộ", "Điện Biên"),
    "Sơn La": ("Tây Bắc Bộ", "Sơn La"),
    "Hòa Bình": ("Tây Bắc Bộ", "Phú Thọ"),
    # Bắc Trung Bộ
    "Thanh Hóa": ("Bắc Trung Bộ", "Thanh Hóa"),
    "Nghệ An": ("Bắc Trung Bộ", "Nghệ An"),
    "Hà Tĩnh": ("Bắc Trung Bộ", "Hà Tĩnh"),
    "Quảng Bình": ("Bắc Trung Bộ", "Quảng Trị"),
    "Quảng Trị": ("Bắc Trung Bộ", "Quảng Trị"),
    "Huế": ("Bắc Trung Bộ", "Huế"),
    # Nam Trung Bộ
    "Đà Nẵng": ("Nam Trung Bộ", "Đà Nẵng"),
    "Quảng Nam": ("Nam Trung Bộ", "Đà Nẵng"),
    "Quảng Ngãi": ("Nam Trung Bộ", "Quảng Ngãi"),
    "Bình Định": ("Nam Trung Bộ", "Gia Lai"),
    "Phú Yên": ("Nam Trung Bộ", "Đắk Lắk"),
    "Khánh Hòa": ("Nam Trung Bộ", "Khánh Hòa"),
    "Ninh Thuận": ("Nam Trung Bộ", "Khánh Hòa"),
    "Bình Thuận": ("Nam Trung Bộ", "Lâm Đồng"),
    # Tây Nguyên
    "Kon Tum": ("Tây Nguyên", "Quảng Ngãi"),
    "Gia Lai": ("Tây Nguyên", "Gia Lai"),
    "Đắk Lắk": ("Tây Nguyên", "Đắk Lắk"),
    "Đắk Nông": ("Tây Nguyên", "Lâm Đồng"),
    "Lâm Đồng": ("Tây Nguyên", "Lâm Đồng"),
    # Đông Nam Bộ
    "Hồ Chí Minh": ("Đông Nam Bộ", "Hồ Chí Minh"),
    "Bình Dương": ("Đông Nam Bộ", "Hồ Chí Minh"),
    "Bà Rịa - Vũng Tàu": ("Đông Nam Bộ", "Hồ Chí Minh"),
    "Bình Phước": ("Đông Nam Bộ", "Đồng Nai"),
    "Đồng Nai": ("Đông Nam Bộ", "Đồng Nai"),
    "Tây Ninh": ("Đông Nam Bộ", "Tây Ninh"),
    # Đồng bằng sông Cửu Long
    "Long An": ("Đồng bằng sông Cửu Long", "Tây Ninh"),
    "Cần Thơ": ("Đồng bằng sông Cửu Long", "Cần Thơ"),
    "Sóc Trăng": ("Đồng bằng sông Cửu Long", "Cần Thơ"),
    "Hậu Giang": ("Đồng bằng sông Cửu Long", "Cần Thơ"),
    "Vĩnh Long": ("Đồng bằng sông Cửu Long", "Vĩnh Long"),
    "Bến Tre": ("Đồng bằng sông Cửu Long", "Vĩnh Long"),
    "Trà Vinh": ("Đồng bằng sông Cửu Long", "Vĩnh Long"),
    "Đồng Tháp": ("Đồng bằng sông Cửu Long", "Đồng Tháp"),
    "Tiền Giang": ("Đồng bằng sông Cửu Long", "Đồng Tháp"),
    "Cà Mau": ("Đồng bằng sông Cửu Long", "Cà Mau"),
    "Bạc Liêu": ("Đồng bằng sông Cửu Long", "Cà Mau"),
    "An Giang": ("Đồng bằng sông Cửu Long", "An Giang"),
    "Kiên Giang": ("Đồng bằng sông Cửu Long", "An Giang"),
}

_SUBREGION_TO_REGION = {
    "Đồng bằng sông Hồng": BAC,
    "Đông Bắc Bộ": BAC,
    "Tây Bắc Bộ": BAC,
    "Bắc Trung Bộ": TRUNG,
    "Nam Trung Bộ": TRUNG,
    "Tây Nguyên": TRUNG,
    "Đông Nam Bộ": NAM,
    "Đồng bằng sông Cửu Long": NAM,
}

_EXTRA_ALIASES = {
    "Hồ Chí Minh": ["tp hcm", "tphcm", "hcm", "sai gon", "saigon", "ho chi minh city", "hcmc"],
    "Hà Nội": ["hanoi", "hn"],
    "Huế": ["thua thien hue", "thua thien - hue"],
    "Bà Rịa - Vũng Tàu": ["ba ria vung tau", "brvt", "vung tau"],
    "Đắk Lắk": ["dak lak", "daklak", "dac lac"],
    "Đắk Nông": ["dak nong", "daknong"],
    "Bắc Kạn": ["bac can"],
    "Hòa Bình": ["hoa binh"],
    "Thanh Hóa": ["thanh hoa"],
    "Khánh Hòa": ["khanh hoa"],
}

PROVINCES_2025 = sorted({new for _, new in _LEGACY.values()})


@dataclass(frozen=True)
class RegionInfo:
    province: str
    province_legacy: str | None
    region: str
    subregion: str


def fold(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9\- ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_PREFIX = re.compile(r"^(tinh|thanh pho|tp|t p)\s+")


def _strip_prefix(s: str) -> str:
    return _PREFIX.sub("", s).strip()


def _build_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for name in _LEGACY:
        folded = fold(name)
        idx[folded] = name
        idx[folded.replace(" - ", " ").replace("-", " ")] = name
        idx[folded.replace(" ", "")] = name
    for name, aliases in _EXTRA_ALIASES.items():
        for a in aliases:
            idx[fold(a)] = name
    return idx


_INDEX = _build_index()
_KEYS_BY_LEN = sorted(_INDEX, key=len, reverse=True)


def _info(name: str) -> RegionInfo:
    subregion, new = _LEGACY[name]
    region = _SUBREGION_TO_REGION[subregion]
    legacy = name if name != new else None
    if legacy is None:
        subregion = _LEGACY[new][0]
    return RegionInfo(province=new, province_legacy=legacy, region=region, subregion=subregion)


def lookup_province(name: str | None) -> RegionInfo | None:
    if not name:
        return None
    key = _strip_prefix(fold(name))
    for candidate in (key, key.replace("-", " "), key.replace(" ", "")):
        if candidate in _INDEX:
            return _info(_INDEX[candidate])
    return None


def resolve(*candidates: str | None, address: str | None = None) -> RegionInfo | None:
    """Resolve from explicit province fields first, then by scanning the free-text address from the end."""
    for c in candidates:
        info = lookup_province(c)
        if info:
            return info
    if not address:
        return None
    parts = [p.strip() for p in re.split(r"[,\n]", address) if p.strip()]
    for part in reversed(parts):
        info = lookup_province(part)
        if info:
            return info
    folded = f" {fold(address)} "
    best: tuple[int, str] | None = None
    for key in _KEYS_BY_LEN:
        if len(key) < 4:
            continue
        pos = folded.rfind(f" {key} ")
        if pos >= 0 and (best is None or pos > best[0]):
            best = (pos, _INDEX[key])
    return _info(best[1]) if best else None
