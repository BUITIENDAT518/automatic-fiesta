from orderhub.regions import BAC, NAM, PROVINCES_2025, TRUNG, lookup_province, resolve


def test_34_provinces():
    assert len(PROVINCES_2025) == 34


def test_lookup_variants():
    assert lookup_province("TP. Hồ Chí Minh").province == "Hồ Chí Minh"
    assert lookup_province("Thành phố Hà Nội").region == BAC
    assert lookup_province("ha noi").province == "Hà Nội"
    assert lookup_province("Thừa Thiên Huế").province == "Huế"
    assert lookup_province("Ba Ria - Vung Tau").province == "Hồ Chí Minh"
    assert lookup_province("Không tồn tại") is None


def test_legacy_mapping():
    info = lookup_province("Bình Dương")
    assert (info.province, info.province_legacy, info.region) == ("Hồ Chí Minh", "Bình Dương", NAM)
    info = lookup_province("Quảng Nam")
    assert (info.province, info.region, info.subregion) == ("Đà Nẵng", TRUNG, "Nam Trung Bộ")
    assert lookup_province("Kon Tum").subregion == "Tây Nguyên"


def test_resolve_from_address():
    info = resolve(None, address="12 Lê Lợi, Phường Bến Nghé, Quận 1, TP.HCM")
    assert info.province == "Hồ Chí Minh"
    info = resolve(None, address="Số 5 Trần Phú, Phường Trần Phú, TP. Quy Nhơn, Bình Định")
    assert (info.province, info.province_legacy) == ("Gia Lai", "Bình Định")
    assert resolve("Đà Nẵng", address="Hà Nội").province == "Đà Nẵng"
