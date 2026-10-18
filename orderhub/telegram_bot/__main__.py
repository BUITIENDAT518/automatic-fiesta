"""Telegram bot for bulk account import into the pool.

Admins (TELEGRAM_ADMIN_IDS) send CSV / XLSX / JSON files; each row is mapped to a platform account
(orderhub.importer), saved encrypted, login-checked, and the original message is deleted because it carries
credentials. Commands: /pool (pool status), /mau (template file), /help.
"""

from __future__ import annotations

import asyncio
import collections
import logging

import httpx

from orderhub import accounts, bus
from orderhub.config import get_settings
from orderhub.importer import TEMPLATE_CSV, parse_file
from orderhub.pool import AccountPool
from orderhub.runtime import open_db, setup_logging, stop_event

log = logging.getLogger("orderhub.telegram")
API = "https://api.telegram.org"
MAX_FILE_BYTES = 5 * 1024 * 1024
HELP = (
    "Gửi file CSV, Excel (.xlsx) hoặc JSON chứa tài khoản các nền tảng.\n"
    "- Nền tảng lấy từ cột 'platform', tên sheet hoặc tên file (vd ghtk.xlsx, nhanh_vn.csv).\n"
    "- Cột được nhận dạng tự động: shop_id, name, username, password, token, tenant, store, business_id…\n"
    "- Tin nhắn chứa file sẽ bị xoá ngay sau khi nạp.\n"
    "Lệnh: /mau (file mẫu), /pool (tình trạng pool)."
)


class TelegramError(RuntimeError):
    pass


class Bot:
    def __init__(self, token: str, http: httpx.AsyncClient):
        self.http, self.base, self.file_base = http, f"{API}/bot{token}", f"{API}/file/bot{token}"

    async def call(self, method: str, **params):
        resp = await self.http.post(f"{self.base}/{method}", json=params, timeout=60)
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description')}")
        return data["result"]

    async def download(self, file_id: str) -> bytes:
        f = await self.call("getFile", file_id=file_id)
        resp = await self.http.get(f"{self.file_base}/{f['file_path']}", timeout=60)
        resp.raise_for_status()
        return resp.content

    async def send(self, chat_id: int, text: str) -> None:
        for i in range(0, len(text), 4000):
            await self.call("sendMessage", chat_id=chat_id, text=text[i:i + 4000])

    async def send_file(self, chat_id: int, filename: str, content: bytes, caption: str = "") -> None:
        await self.http.post(f"{self.base}/sendDocument", data={"chat_id": str(chat_id), "caption": caption},
                             files={"document": (filename, content)}, timeout=60)


def admin_ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


async def import_document(bot: Bot, msg: dict, settings, db, pool: AccountPool, r) -> str:
    doc = msg["document"]
    name = doc.get("file_name") or "upload.csv"
    if int(doc.get("file_size") or 0) > MAX_FILE_BYTES:
        return f"File {name} quá lớn (tối đa 5 MB)."
    data = await bot.download(doc["file_id"])
    try:
        await bot.call("deleteMessage", chat_id=msg["chat"]["id"], message_id=msg["message_id"])
        deleted = True
    except TelegramError:
        deleted = False
    try:
        rows = parse_file(name, data)
    except (ValueError, KeyError) as e:
        return f"Không đọc được {name}: {e}"
    sem = asyncio.Semaphore(4)

    async def save_and_check(row):
        await accounts.save_account(row.account, db, pool, r)
        async with sem:
            return await accounts.check_account(row.account, settings, db, pool)

    checks = await asyncio.gather(*(save_and_check(row) for row in rows if row.account))
    it = iter(checks)
    lines, ok = [], 0
    for row in rows:
        label = f"#{row.row} {row.platform or '?'} {row.shop_id or ''}".strip()
        if row.error:
            lines.append(f"[BỎ QUA] {label}: {row.error}")
            continue
        res = next(it)
        if res["ok"]:
            ok += 1
            lines.append(f"[OK] {label}")
        elif res["ok"] is None:
            ok += 1
            lines.append(f"[ĐÃ NẠP] {label}: {res['error']}")
        else:
            lines.append(f"[LỖI ĐĂNG NHẬP] {label}: {res['error'][:150]}")
    head = f"{name}: nạp {sum(1 for x in rows if x.account)}/{len(rows)} dòng, đăng nhập được {ok}."
    tail = "" if deleted else "\nKhông xoá được tin nhắn gốc, hãy tự xoá vì file có mật khẩu (cấp quyền xoá tin cho bot trong nhóm)."
    return head + "\n" + "\n".join(lines) + tail


async def pool_summary(db, pool: AccountPool) -> str:
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for acc in await db.list_shops(active_only=False):
        state = (await pool.health(acc.key))["state"] if acc.active else "disabled"
        counts[acc.platform.value][state] += 1
    if not counts:
        return "Pool đang trống."
    return "\n".join(f"{p}: " + ", ".join(f"{k} {v}" for k, v in c.most_common()) for p, c in sorted(counts.items()))


async def handle_update(update: dict, bot: Bot, settings, db, pool: AccountPool, r, admins: set[int]) -> None:
    msg = update.get("message") or {}
    if not msg:
        return
    chat_id, user_id = msg["chat"]["id"], (msg.get("from") or {}).get("id")
    if user_id not in admins:
        await bot.send(chat_id, f"Bạn chưa có quyền dùng bot này. Telegram ID của bạn: {user_id}. "
                                "Nhờ quản trị thêm ID này vào TELEGRAM_ADMIN_IDS.")
        return
    text = (msg.get("text") or "").strip()
    if msg.get("document"):
        await bot.send(chat_id, await import_document(bot, msg, settings, db, pool, r))
    elif text.startswith("/pool"):
        await bot.send(chat_id, await pool_summary(db, pool))
    elif text.startswith("/mau"):
        await bot.send_file(chat_id, "mau_tai_khoan.csv", TEMPLATE_CSV.encode(), "File mẫu: mỗi dòng một tài khoản.")
    else:
        await bot.send(chat_id, HELP)


async def main() -> None:
    setup_logging()
    settings = get_settings()
    stop = stop_event()
    if not settings.telegram_bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set; telegram bot idle")
        await stop.wait()
        return
    admins = admin_ids(settings.telegram_admin_ids)
    db = await open_db(settings)
    r = bus.connect(settings.redis_url)
    pool = AccountPool(r)
    lease = bus.Lease(r, settings.worker_id, ttl_ms=120_000)
    log.info("telegram bot %s started (%d admins)", settings.worker_id, len(admins))
    async with httpx.AsyncClient() as http:
        bot = Bot(settings.telegram_bot_token, http)
        while not stop.is_set():
            if not await lease.acquire("telegram"):
                await asyncio.sleep(5)
                continue
            offset = int(await r.get("telegram:offset") or 0)
            try:
                updates = await bot.call("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
            except (TelegramError, httpx.HTTPError) as e:
                log.warning("getUpdates failed: %s", e)
                await asyncio.sleep(5)
                continue
            for upd in updates:
                await r.set("telegram:offset", upd["update_id"] + 1)
                try:
                    await handle_update(upd, bot, settings, db, pool, r, admins)
                except Exception:
                    log.exception("update %s failed", upd.get("update_id"))
        await lease.release("telegram")
    await db.close()
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
