# Order Hub

Hệ thống phân tán, phân tầng thu thập đơn hàng đa sàn (Shopee, TikTok Shop, Lazada) gần thời gian thực trong 48 giờ gần nhất, kèm vận chuyển, khách hàng, shop và vùng miền.

```
 Sàn (API chính thức/OAuth, webhook)
        │
 [collector ×N]  đăng nhập/refresh token, poll gia tăng theo update_time, lease Redis theo shop
        │  Redis Stream orders.ingest
 [processor ×N]  consumer group, chuẩn hoá + vùng miền (34 tỉnh sau 2025, map tỉnh cũ), mã hoá PII, upsert idempotent
        │  PostgreSQL                │ Redis Pub/Sub orders.updates
 [api ×N]  REST lọc/thống kê + WebSocket realtime + dashboard
```

- **Phân tán**: chạy bao nhiêu replica collector/processor/api cũng được. Mỗi shop chỉ do 1 collector poll tại một thời điểm (lease Redis); collector chết thì replica khác nhận shop sau khi lease hết hạn. Processor dùng consumer group, tự claim lại message treo, lỗi quá 5 lần vào `orders.dlq`.
- **Realtime**: poll mỗi `POLL_INTERVAL_S` (mặc định 30s) với overlap để không sót; webhook `POST /webhooks/{platform}/{shop_id}` kích hoạt poll ngay.
- **Bảo mật**: tên/SĐT/địa chỉ/credentials mã hoá Fernet (`PII_KEY`). Key `viewer` chỉ thấy dữ liệu đã che; `admin` thấy đầy đủ.
- **Retention**: đơn quá `RETENTION_HOURS` (72h) bị xoá; truy vấn mặc định `WINDOW_HOURS` (48h).

## Chạy thử (dữ liệu giả lập)

```bash
cp .env.example .env
sed -i "s|^PII_KEY=.*|PII_KEY=$(python -m orderhub.crypto)|" .env   # hoặc tự sinh Fernet key
docker compose up -d --build
# Dashboard: http://localhost:8000  (key: viewer-dev-key hoặc admin-dev-key)
```

## Triển khai AWS (ap-southeast-1)

Terraform trong `infra/aws` tạo: VPC, ECR, ECS Fargate (api/collector/processor, mỗi loại 2 replica), RDS PostgreSQL 16 (mã hoá, backup 7 ngày), ElastiCache Redis 7 (TLS), ALB (WebSocket, HTTPS nếu có `certificate_arn`), Secrets Manager (DB URL, `PII_KEY`, API key sinh ngẫu nhiên) và CloudWatch Logs.

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
infra/aws/deploy.sh            # build + push image, terraform apply
```

API key của dashboard nằm trong secret `orderhub/app`; key/secret của sàn điền vào secret `orderhub/marketplaces`.

## Kết nối shop thật

1. Điền app key/secret của từng sàn vào `.env` (`SHOPEE_PARTNER_*`, `TIKTOK_APP_*`, `LAZADA_APP_*`), đặt `SEED_MOCK_SHOPS=false`, đổi `API_KEY_*`.
2. Hoàn tất OAuth của sàn để lấy `access_token`/`refresh_token`, rồi đăng ký shop:

```bash
curl -X POST localhost:8000/api/shops -H 'X-API-Key: <admin>' -H 'content-type: application/json' -d '{
  "platform": "tiktok", "shop_id": "...", "name": "Shop A",
  "credentials": {"access_token": "...", "refresh_token": "...", "expires_at": 0, "shop_cipher": "..."}
}'
```

Token được tự refresh và lưu lại (đã mã hoá).

## API

| Endpoint | Mô tả |
|---|---|
| `GET /api/orders` | lọc `hours, platform, shop_id, region, province, status, carrier, q`, phân trang `limit/offset` |
| `GET /api/orders/{platform}/{shop_id}/{order_id}` | chi tiết đơn |
| `GET /api/stats` | tổng hợp theo vùng, tỉnh, sàn, shop, trạng thái, đơn vị vận chuyển, giờ |
| `GET /api/shops`, `POST /api/shops` | danh sách shop + trạng thái poll; thêm shop (admin) |
| `WS /ws?key=...` | luồng đơn mới/cập nhật |

## Phát triển

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q && .venv/bin/ruff check .
```

## Pool tài khoản nền tảng

Mỗi tài khoản (shop sàn, OMS hay hãng vận chuyển) là một dòng trong bảng `shops`, credentials được mã hoá.
Tình trạng của từng tài khoản (`ok`, `retry`, `cooldown`, `error`, `disabled`) được lưu trong Redis (`pool:health:*`)
để mọi replica dùng chung: lỗi liên tiếp sẽ tạm nghỉ 1, 2, 4… phút (tối đa 1 giờ), lỗi 5 lần liên tiếp thì chuyển
sang `error`. Tài khoản vận chuyển được xoay vòng round-robin, và hệ thống nhớ tài khoản nào sở hữu mã vận đơn nào.

| Nền tảng | Loại | Credentials | Ghi chú |
| --- | --- | --- | --- |
| Sapo | Lấy đơn | `{"store": "<alias>", "access_token": "..."}` hoặc `api_key` + `api_secret` | `/admin/orders.json` |
| Nhanh.vn | Lấy đơn | `{"access_token", "business_id", "expires_at"}`, env `NHANH_APP_ID` | API v3, token 1 năm, không refresh |
| Pancake POS | Lấy đơn | `{"api_key": "..."}` | `pos.pages.fm/api/v1` |
| Haravan | Lấy đơn | `{"access_token": "..."}` (private app token, Bearer) | `apis.haravan.com/com/orders.json` |
| TPOS | Lấy đơn | `{"tenant", "username", "password"}` | **Thử nghiệm**: TPOS không có tài liệu API công khai |
| GHTK | Vận chuyển | `{"token": "...", "partner_code": "..."}` | `/services/shipment/v2/{mã}` |
| GHN | Vận chuyển | `{"token": "...", "shop_id": 123}` | `/v2/shipping-order/detail` |
| J&T Express | Vận chuyển | `{"api_account", "private_key", "customer_code", "password"}` | `/logistics/trace`, ký `digest` |

SPX Express, BEST Express và Ninja Van chưa có connector: SPX và BEST không công bố API công khai (chỉ cấp cho
đối tác), tài liệu API Ninja Van chưa truy cập được để xác minh endpoint.

API (admin): `GET /api/accounts`, `POST /api/accounts`, `PATCH /api/accounts/{platform}/{id}?active=false`,
`DELETE /api/accounts/{platform}/{id}`, `POST /api/accounts/{platform}/{id}/check` (kiểm tra đăng nhập thật).
Service `tracker` cập nhật hành trình đơn GHTK/GHN; webhook `POST /webhooks/carrier/{ghtk|ghn}` kích hoạt kiểm tra
ngay mã vận đơn đó (payload không được tin là dữ liệu, luôn hỏi lại API của hãng).
