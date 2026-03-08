"""
╔══════════════════════════════════════════════════════╗
║   SHOPBOT — Магазин одежды / Шымкент, Казахстан     ║
║   aiogram 3.x | aiosqlite | CryptoPay | KZT         ║
╚══════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import json
import aiosqlite
import aiohttp
import ssl
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ContentType, BotCommand, BotCommandScopeChat,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════
#  Конфигурация
# ══════════════════════════════════════════════
BOT_TOKEN        = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN  = os.getenv("CRYPTOBOT_TOKEN")
ADMIN_IDS        = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support")
SHOP_NAME        = os.getenv("SHOP_NAME", "👕 Магазин одежды")
KASPI_PHONE      = os.getenv("KASPI_PHONE", "+7XXXXXXXXXX")
MANAGER_ID       = int(os.getenv("MANAGER_ID", str(ADMIN_IDS[0])))

# Фиксированный курс USD/KZT (используется если live-запрос не удался)
USD_KZT_RATE: float = 494.0

# Процент кэшбэка на бонусный баланс
CASHBACK_PERCENT: float = 5.0

DB_PATH = "shop.db"

bot     = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)
router  = Router()
dp.include_router(router)

# ══════════════════════════════════════════════
#  Анимированные эмодзи (только в тексте сообщений, НЕ в кнопках!)
# ══════════════════════════════════════════════
AE = {
    "shop":    '<tg-emoji emoji-id="5373052667671093676">🛍</tg-emoji>',
    "down":    '<tg-emoji emoji-id="5470177992950946662">👇</tg-emoji>',
    "folder":  '<tg-emoji emoji-id="5433653135799228968">📁</tg-emoji>',
    "money":   '<tg-emoji emoji-id="5472030678633684592">💸</tg-emoji>',
    "cart":    '<tg-emoji emoji-id="5431499171045581032">🛒</tg-emoji>',
    "cal":     '<tg-emoji emoji-id="5431897022456145283">📆</tg-emoji>',
    "archive": '<tg-emoji emoji-id="5431736674147114227">🗂</tg-emoji>',
    "store":   '<tg-emoji emoji-id="5265105755677159697">🏬</tg-emoji>',
    "support": '<tg-emoji emoji-id="5467666648263564704">❓</tg-emoji>',
    "star":    "⭐",
    "gift":    "🎁",
    "truck":   "🚚",
    "tag":     "🏷",
}
def ae(k): return AE.get(k, "")

# ══════════════════════════════════════════════
#  FSM-состояния
# ══════════════════════════════════════════════
class AdminSt(StatesGroup):
    broadcast           = State()
    set_media_file      = State()
    add_cat_name        = State()
    add_prod_name       = State()
    add_prod_desc       = State()
    add_prod_price      = State()
    add_prod_sizes      = State()
    add_prod_stock      = State()
    add_prod_seller_ph  = State()   # телефон продавца (обязательно)
    add_prod_seller_un  = State()   # юзернейм продавца (необязательно)
    edit_shop_info      = State()

class ProfileSt(StatesGroup):
    """Редактирование профиля."""
    phone   = State()
    address = State()

class ReviewSt(StatesGroup):
    """Сбор отзыва после двойного подтверждения доставки."""
    rating  = State()
    comment = State()

# ══════════════════════════════════════════════
#  База данных
# ══════════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id           INTEGER PRIMARY KEY,
                username          TEXT    DEFAULT '',
                first_name        TEXT    DEFAULT '',
                phone             TEXT    DEFAULT '',
                default_address   TEXT    DEFAULT '',
                total_purchases   INTEGER DEFAULT 0,
                total_spent       REAL    DEFAULT 0,
                bonus_balance     REAL    DEFAULT 0,
                registered_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id     INTEGER,
                name            TEXT    NOT NULL,
                description     TEXT    DEFAULT '',
                price           REAL    NOT NULL,
                sizes           TEXT    DEFAULT '[]',
                stock           INTEGER DEFAULT 0,
                seller_username TEXT    DEFAULT '',
                seller_phone    TEXT    DEFAULT '',
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                product_id  INTEGER,
                size        TEXT    DEFAULT '',
                price       REAL,
                method      TEXT    DEFAULT 'crypto',
                phone       TEXT    DEFAULT '',
                address     TEXT    DEFAULT '',
                status      TEXT    DEFAULT 'processing',
                created_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
                product_id   INTEGER,
                price        REAL,
                method       TEXT    DEFAULT 'crypto',
                purchased_at TEXT
            );

            CREATE TABLE IF NOT EXISTS media_settings (
                key        TEXT PRIMARY KEY,
                media_type TEXT,
                file_id    TEXT
            );

            CREATE TABLE IF NOT EXISTS shop_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS crypto_payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                product_id INTEGER,
                size       TEXT    DEFAULT '',
                invoice_id TEXT UNIQUE,
                amount_kzt REAL,
                amount_usd REAL,
                status     TEXT DEFAULT 'pending',
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS kaspi_payments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                product_id     INTEGER,
                size           TEXT    DEFAULT '',
                amount         REAL,
                status         TEXT    DEFAULT 'pending',
                manager_msg_id INTEGER DEFAULT 0,
                created_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                product_id INTEGER,
                order_id   INTEGER,
                rating     INTEGER,
                comment    TEXT,
                created_at TEXT
            );
        ''')
        await db.commit()

# ── Универсальные хелперы ──────────────────────
async def db_one(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as c:
            return await c.fetchone()

async def db_all(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as c:
            return await c.fetchall()

async def db_run(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, params)
        await db.commit()

async def db_insert(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        c = await db.execute(sql, params)
        await db.commit()
        return c.lastrowid

# ── Пользователи ───────────────────────────────
async def ensure_user(u: types.User):
    await db_run(
        '''INSERT OR IGNORE INTO users
           (user_id, username, first_name, registered_at)
           VALUES (?, ?, ?, ?)''',
        (u.id, u.username or '', u.first_name or '', datetime.now().isoformat())
    )

async def get_user(uid):
    return await db_one('SELECT * FROM users WHERE user_id=?', (uid,))

async def update_user_phone(uid, phone: str):
    await db_run('UPDATE users SET phone=? WHERE user_id=?', (phone, uid))

async def update_user_address(uid, address: str):
    await db_run('UPDATE users SET default_address=? WHERE user_id=?', (address, uid))

async def add_bonus(uid, amount_kzt: float) -> float:
    bonus = round(amount_kzt * CASHBACK_PERCENT / 100, 0)
    await db_run(
        'UPDATE users SET bonus_balance=bonus_balance+? WHERE user_id=?',
        (bonus, uid)
    )
    return bonus

# ── Категории ──────────────────────────────────
async def get_categories():
    return await db_all('SELECT * FROM categories ORDER BY id')

async def add_category(name: str):
    await db_run('INSERT INTO categories(name) VALUES(?)', (name,))

async def del_category(cid: int):
    await db_run('UPDATE products SET is_active=0 WHERE category_id=?', (cid,))
    await db_run('DELETE FROM categories WHERE id=?', (cid,))

# ── Товары ─────────────────────────────────────
async def get_products(cid: int):
    return await db_all(
        'SELECT * FROM products WHERE category_id=? AND is_active=1', (cid,)
    )

async def get_product(pid: int):
    return await db_one('SELECT * FROM products WHERE id=?', (pid,))

async def add_product(cid, name, desc, price, sizes_list,
                      stock, seller_username='', seller_phone=''):
    sizes_json = json.dumps(sizes_list, ensure_ascii=False)
    await db_run(
        '''INSERT INTO products
           (category_id, name, description, price, sizes, stock,
            seller_username, seller_phone, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (cid, name, desc, price, sizes_json, stock,
         seller_username, seller_phone, datetime.now().isoformat())
    )

async def del_product(pid: int):
    await db_run('UPDATE products SET is_active=0 WHERE id=?', (pid,))

async def reduce_stock(pid: int):
    await db_run('UPDATE products SET stock=MAX(0, stock-1) WHERE id=?', (pid,))

def parse_sizes(product) -> list:
    try:
        return json.loads(product['sizes'] or '[]')
    except Exception:
        return []

# ── Заказы ─────────────────────────────────────
async def create_order(uid, pid, size, price, method, phone='', address=''):
    return await db_insert(
        '''INSERT INTO orders
           (user_id, product_id, size, price, method, phone, address, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', ?)''',
        (uid, pid, size, price, method, phone, address, datetime.now().isoformat())
    )

async def get_order(oid: int):
    return await db_one('SELECT * FROM orders WHERE id=?', (oid,))

async def set_order_status(oid: int, status: str):
    await db_run('UPDATE orders SET status=? WHERE id=?', (status, oid))

async def get_user_orders(uid: int):
    return await db_all(
        '''SELECT o.*, p.name AS pname
           FROM orders o JOIN products p ON o.product_id=p.id
           WHERE o.user_id=? ORDER BY o.created_at DESC LIMIT 10''',
        (uid,)
    )

# ── Статистика покупок ─────────────────────────
async def add_purchase(uid, pid, price, method='crypto'):
    await db_run(
        'INSERT INTO purchases(user_id,product_id,price,method,purchased_at) VALUES(?,?,?,?,?)',
        (uid, pid, price, method, datetime.now().isoformat())
    )
    await db_run(
        'UPDATE users SET total_purchases=total_purchases+1, total_spent=total_spent+? WHERE user_id=?',
        (price, uid)
    )

# ── Отзывы ─────────────────────────────────────
async def add_review(uid, pid, oid, rating, comment):
    await db_run(
        'INSERT INTO reviews(user_id,product_id,order_id,rating,comment,created_at) VALUES(?,?,?,?,?,?)',
        (uid, pid, oid, rating, comment, datetime.now().isoformat())
    )

async def get_reviews(pid, limit=10):
    return await db_all(
        'SELECT * FROM reviews WHERE product_id=? ORDER BY created_at DESC LIMIT ?',
        (pid, limit)
    )

# ── Медиа / настройки ──────────────────────────
async def set_media(key, mtype, fid):
    await db_run(
        'INSERT OR REPLACE INTO media_settings(key,media_type,file_id) VALUES(?,?,?)',
        (key, mtype, fid)
    )

async def get_media(key):
    return await db_one('SELECT * FROM media_settings WHERE key=?', (key,))

async def set_setting(k, v):
    await db_run('INSERT OR REPLACE INTO shop_settings(key,value) VALUES(?,?)', (k, v))

async def get_setting(k, default=''):
    r = await db_one('SELECT value FROM shop_settings WHERE key=?', (k,))
    return r['value'] if r else default

async def get_stats():
    uc = (await db_one('SELECT COUNT(*) c FROM users'))['c']
    pc = (await db_one('SELECT COUNT(*) c FROM purchases'))['c']
    rv = (await db_one('SELECT COALESCE(SUM(price),0) s FROM purchases'))['s']
    ac = (await db_one('SELECT COUNT(*) c FROM products WHERE is_active=1'))['c']
    oc = (await db_one(
        "SELECT COUNT(*) c FROM orders WHERE status NOT IN ('delivered','confirmed')"
    ))['c']
    return uc, pc, rv, ac, oc

async def all_user_ids():
    rows = await db_all('SELECT user_id FROM users')
    return [r['user_id'] for r in rows]

# ── Крипто-платежи ─────────────────────────────
async def save_crypto(uid, pid, size, inv_id, amount_kzt, amount_usd):
    await db_run(
        '''INSERT OR IGNORE INTO crypto_payments
           (user_id,product_id,size,invoice_id,amount_kzt,amount_usd,created_at)
           VALUES(?,?,?,?,?,?,?)''',
        (uid, pid, size, inv_id, amount_kzt, amount_usd, datetime.now().isoformat())
    )

async def get_crypto(inv_id):
    return await db_one('SELECT * FROM crypto_payments WHERE invoice_id=?', (inv_id,))

async def set_crypto_paid(inv_id):
    await db_run("UPDATE crypto_payments SET status='paid' WHERE invoice_id=?", (inv_id,))

# ── Kaspi-платежи ──────────────────────────────
async def save_kaspi(uid, pid, size, amount):
    return await db_insert(
        'INSERT INTO kaspi_payments(user_id,product_id,size,amount,created_at) VALUES(?,?,?,?,?)',
        (uid, pid, size, amount, datetime.now().isoformat())
    )

async def get_kaspi(kid):
    return await db_one('SELECT * FROM kaspi_payments WHERE id=?', (kid,))

async def set_kaspi_status(kid, status, mgr_mid=None):
    if mgr_mid is not None:
        await db_run(
            'UPDATE kaspi_payments SET status=?,manager_msg_id=? WHERE id=?',
            (status, mgr_mid, kid)
        )
    else:
        await db_run('UPDATE kaspi_payments SET status=? WHERE id=?', (status, kid))

# ══════════════════════════════════════════════
#  CryptoBot
# ══════════════════════════════════════════════
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

async def get_usd_kzt_rate() -> float:
    """Актуальный курс USD→KZT. При ошибке — фиксированный."""
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_ssl_ctx())
        ) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json()
                return float(data["rates"]["KZT"])
    except Exception:
        return USD_KZT_RATE

def kzt_to_usd(kzt: float, rate: float) -> float:
    return round(kzt / rate, 2)

async def create_invoice(amount_usd: float, desc: str, payload: str):
    url  = "https://pay.crypt.bot/api/createInvoice"
    hdr  = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    me   = await bot.get_me()
    data = {
        "asset": "USDT",
        "amount": str(amount_usd),
        "description": desc,
        "payload": payload,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{me.username}",
    }
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=_ssl_ctx())
    ) as s:
        async with s.post(url, headers=hdr, json=data) as r:
            res = await r.json()
            return res["result"] if res.get("ok") else None

async def check_invoice(inv_id: str):
    url = "https://pay.crypt.bot/api/getInvoices"
    hdr = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=_ssl_ctx())
    ) as s:
        async with s.get(url, headers=hdr, params={"invoice_ids": inv_id}) as r:
            res = await r.json()
            if res.get("ok") and res["result"]["items"]:
                return res["result"]["items"][0]
    return None

# ══════════════════════════════════════════════
#  Форматирование
# ══════════════════════════════════════════════
def fmt_dt() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")

def fmt_price(kzt) -> str:
    """5000 → '5 000 ₸'"""
    try:
        return f"{int(float(kzt)):,}".replace(",", " ") + " ₸"
    except Exception:
        return f"{kzt} ₸"

ORDER_STATUS_LABELS = {
    "processing": "🔄 В обработке",
    "china":      "✈️ Едет из Китая",
    "arrived":    "📦 Прибыло в Шымкент",
    "delivered":  "🚚 Передано покупателю",
    "confirmed":  "✅ Получено покупателем",
}

def order_status_text(status: str) -> str:
    return ORDER_STATUS_LABELS.get(status, status)

# ══════════════════════════════════════════════
#  Клавиатуры
# ══════════════════════════════════════════════
def kb_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛒 Купить"),     KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏬 О магазине"), KeyboardButton(text="❓ Поддержка")],
    ], resize_keyboard=True)

def kb_back(cd="main"):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data=cd)]]
    )

def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика",  callback_data="adm_stats")],
        [InlineKeyboardButton(text="🖼 Медиа",        callback_data="adm_media"),
         InlineKeyboardButton(text="📨 Рассылка",     callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="📦 Товары",       callback_data="adm_products"),
         InlineKeyboardButton(text="📁 Категории",    callback_data="adm_cats")],
        [InlineKeyboardButton(text="📋 Заказы",       callback_data="adm_orders")],
        [InlineKeyboardButton(text="⚙️ Настройки",    callback_data="adm_settings")],
    ])

def kb_admin_back():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text="‹ Админ панель", callback_data="adm_panel"
        )]]
    )

# ══════════════════════════════════════════════
#  Хелперы отправки сообщений
# ══════════════════════════════════════════════
async def send_media(chat_id: int, text: str, key: str, markup=None):
    """Отправить сообщение с медиа (если задано) или без него.
    При ошибке DOCUMENT_INVALID — удаляет битое медиа и шлёт текстом."""
    m = await get_media(key)
    if m:
        mt = m["media_type"]
        try:
            if mt == "photo":
                await bot.send_photo(chat_id, m["file_id"], caption=text,
                                     parse_mode="HTML", reply_markup=markup)
                return
            elif mt == "video":
                await bot.send_video(chat_id, m["file_id"], caption=text,
                                     parse_mode="HTML", reply_markup=markup)
                return
            elif mt == "animation":
                await bot.send_animation(chat_id, m["file_id"], caption=text,
                                         parse_mode="HTML", reply_markup=markup)
                return
        except Exception:
            # Медиа недействительно — убираем из БД
            await db_run('DELETE FROM media_settings WHERE key=?', (key,))
    # Fallback: обычное текстовое сообщение
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

async def set_cmds(uid: int):
    cmds = [BotCommand(command="start", description="🚀 Старт")]
    if uid in ADMIN_IDS:
        cmds.append(BotCommand(command="admin", description="🎩 Панель"))
    await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))

# ══════════════════════════════════════════════
#  /start  /admin
# ══════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    await state.clear()
    await ensure_user(msg.from_user)
    await set_cmds(msg.from_user.id)
    text = (
        f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n"
        f"<blockquote>{ae('down')} Добро пожаловать! Выберите раздел:</blockquote>"
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=kb_main())

@router.message(Command("admin"))
async def cmd_admin(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await msg.answer("🎩 <b>Панель управления</b>",
                     parse_mode="HTML", reply_markup=kb_admin())

@router.callback_query(F.data == "main")
async def cb_main(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    text = (
        f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n"
        f"<blockquote>{ae('down')} Выберите нужный раздел:</blockquote>"
    )
    await bot.send_message(cb.from_user.id, text,
                           parse_mode="HTML", reply_markup=kb_main())
    await cb.answer()

# ══════════════════════════════════════════════
#  Reply-кнопки главного меню
# ══════════════════════════════════════════════
@router.message(F.text == "🛒 Купить")
async def txt_shop(msg: types.Message):
    await show_catalog(msg.chat.id)

@router.message(F.text == "👤 Профиль")
async def txt_profile(msg: types.Message):
    await ensure_user(msg.from_user)
    user = await get_user(msg.from_user.id)
    await _send_profile(msg.from_user, user, send_fn=msg.answer)

@router.message(F.text == "🏬 О магазине")
async def txt_about(msg: types.Message):
    info = await get_setting("shop_info", "Информация о магазине пока не заполнена.")
    text = f"{ae('store')} <b>О магазине</b>\n\n<blockquote>{info}</blockquote>"
    await send_media(msg.chat.id, text, "about_menu")

@router.message(F.text == "❓ Поддержка")
async def txt_support(msg: types.Message):
    text = (
        f"{ae('support')} <b>Поддержка</b>\n\n"
        f"<blockquote>По любым вопросам пишите менеджеру:\n{SUPPORT_USERNAME}</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✉️ Написать",
                             url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")
    ]])
    await send_media(msg.chat.id, text, "support_menu", kb)

# ══════════════════════════════════════════════
#  Профиль — внутренние функции
# ══════════════════════════════════════════════
def _profile_text(tg_user: types.User, user) -> str:
    """Сформировать текст профиля без HTML-тегов в dynamic данных."""
    phone   = user['phone']           if user['phone']           else '— не указан'
    address = user['default_address'] if user['default_address'] else '— не указан'
    return (
        f"👤 <b>Профиль</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{tg_user.id}</code>\n"
        f"👤 <b>Имя:</b> {tg_user.first_name or '—'}\n\n"
        f"📞 <b>Телефон:</b> <code>{phone}</code>\n"
        f"📍 <b>Адрес доставки:</b>\n"
        f"    <i>{address}</i>\n\n"
        f"{ae('cart')} <b>Заказов:</b> {user['total_purchases']}\n"
        f"{ae('money')} <b>Потрачено:</b> {fmt_price(user['total_spent'])}\n"
        f"{ae('gift')} <b>Бонусный баланс:</b> {fmt_price(user['bonus_balance'])}\n"
        f"{ae('cal')} <b>Регистрация:</b> {user['registered_at'][:10]}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

def _profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📞 Телефон",   callback_data="profile_phone"),
            InlineKeyboardButton(text="📍 Адрес",     callback_data="profile_address"),
        ],
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders")],
    ])

async def _send_profile(tg_user: types.User, user, send_fn=None, edit_msg=None):
    text = _profile_text(tg_user, user)
    kb   = _profile_kb()
    if edit_msg:
        try:
            await edit_msg.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    if send_fn:
        await send_fn(text, parse_mode="HTML", reply_markup=kb)
    else:
        await bot.send_message(tg_user.id, text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "profile_view")
async def cb_profile_view(cb: types.CallbackQuery):
    await ensure_user(cb.from_user)
    user = await get_user(cb.from_user.id)
    await _send_profile(cb.from_user, user, edit_msg=cb.message)
    await cb.answer()

# ══════════════════════════════════════════════
#  Профиль — изменение телефона (2 способа)
# ══════════════════════════════════════════════
@router.callback_query(F.data == "profile_phone")
async def cb_profile_phone(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Поделиться через Telegram",
                              callback_data="phone_via_tg")],
        [InlineKeyboardButton(text="⌨️ Ввести вручную",
                              callback_data="phone_manual")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="profile_view")],
    ])
    try:
        await cb.message.edit_text(
            "📞 <b>Укажите номер телефона</b>\n\n"
            "<blockquote>Выберите удобный способ:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "📞 <b>Укажите номер телефона</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.callback_query(F.data == "phone_via_tg")
async def cb_phone_via_tg(cb: types.CallbackQuery):
    """Запросить контакт через встроенную Telegram-кнопку."""
    try:
        await cb.message.delete()
    except Exception:
        pass
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await bot.send_message(
        cb.from_user.id,
        "📲 Нажмите кнопку ниже, чтобы поделиться вашим номером:",
        reply_markup=kb
    )
    await cb.answer()

@router.message(F.contact)
async def handle_contact(msg: types.Message):
    """Принять номер из Telegram-кнопки."""
    if msg.contact.user_id != msg.from_user.id:
        await msg.answer("❌ Это чужой контакт.", reply_markup=kb_main())
        return
    phone = msg.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await update_user_phone(msg.from_user.id, phone)
    await msg.answer(
        f"✅ <b>Телефон сохранён:</b> <code>{phone}</code>\n\n"
        f"Теперь вы можете делать заказы.",
        parse_mode="HTML",
        reply_markup=kb_main()
    )

@router.callback_query(F.data == "phone_manual")
async def cb_phone_manual(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileSt.phone)
    try:
        await cb.message.edit_text(
            "📞 <b>Введите номер телефона вручную</b>\n"
            "<i>Пример: +7 701 234 56 78</i>",
            parse_mode="HTML",
            reply_markup=kb_back("profile_view")
        )
    except Exception:
        await cb.message.answer(
            "📞 <b>Введите номер телефона вручную</b>",
            parse_mode="HTML",
            reply_markup=kb_back("profile_view")
        )
    await cb.answer()

@router.message(ProfileSt.phone)
async def proc_profile_phone(msg: types.Message, state: FSMContext):
    phone = msg.text.strip()
    await update_user_phone(msg.from_user.id, phone)
    await state.clear()
    await msg.answer(
        f"✅ <b>Телефон сохранён:</b> <code>{phone}</code>",
        parse_mode="HTML",
        reply_markup=kb_main()
    )

# ══════════════════════════════════════════════
#  Профиль — изменение адреса
# ══════════════════════════════════════════════
@router.callback_query(F.data == "profile_address")
async def cb_profile_address(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileSt.address)
    try:
        await cb.message.edit_text(
            "📍 <b>Введите адрес доставки по умолчанию</b>\n"
            "<i>Пример: мкр Нурсат, ул. Байтурсынова 12, кв. 5</i>",
            parse_mode="HTML",
            reply_markup=kb_back("profile_view")
        )
    except Exception:
        await cb.message.answer(
            "📍 <b>Введите адрес доставки</b>",
            parse_mode="HTML",
            reply_markup=kb_back("profile_view")
        )
    await cb.answer()

@router.message(ProfileSt.address)
async def proc_profile_address(msg: types.Message, state: FSMContext):
    address = msg.text.strip()
    await update_user_address(msg.from_user.id, address)
    await state.clear()
    await msg.answer(
        f"✅ <b>Адрес сохранён:</b>\n<i>{address}</i>",
        parse_mode="HTML",
        reply_markup=kb_main()
    )

# ══════════════════════════════════════════════
#  История заказов (из профиля)
# ══════════════════════════════════════════════
@router.callback_query(F.data == "my_orders")
async def cb_my_orders(cb: types.CallbackQuery):
    orders = await get_user_orders(cb.from_user.id)
    if not orders:
        await cb.answer("Заказов пока нет", show_alert=True)
        return
    text = f"{ae('archive')} <b>Мои заказы</b>\n\n━━━━━━━━━━━━━━━━━\n"
    for o in orders:
        text += (
            f"📦 <b>{o['pname']}</b>  ({o['size']})\n"
            f"   {fmt_price(o['price'])}  —  {order_status_text(o['status'])}\n"
            f"   <i>{o['created_at'][:10]}</i>\n\n"
        )
    text += "━━━━━━━━━━━━━━━━━"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=kb_back("profile_view"))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=kb_back("profile_view"))
    await cb.answer()

# ══════════════════════════════════════════════
#  Каталог
# ══════════════════════════════════════════════
async def show_catalog(chat_id: int):
    cats = await get_categories()
    if not cats:
        await bot.send_message(chat_id, "📭 Категории пока не добавлены.")
        return
    kb   = [[InlineKeyboardButton(text=f"🗂 {c['name']}",
                                  callback_data=f"cat_{c['id']}")] for c in cats]
    text = (
        f"{ae('cart')} <b>Каталог</b>\n\n"
        f"<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    )
    await send_media(chat_id, text, "shop_menu",
                     InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "shop")
async def cb_shop(cb: types.CallbackQuery):
    cats = await get_categories()
    if not cats:
        await cb.answer("Категории пока не добавлены", show_alert=True)
        return
    kb   = [[InlineKeyboardButton(text=f"🗂 {c['name']}",
                                  callback_data=f"cat_{c['id']}")] for c in cats]
    text = (
        f"{ae('cart')} <b>Каталог</b>\n\n"
        f"<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()

@router.callback_query(F.data.startswith("cat_"))
async def cb_cat(cb: types.CallbackQuery):
    cid   = int(cb.data.split("_")[1])
    prods = await get_products(cid)
    if not prods:
        await cb.answer("В этой категории пока нет товаров", show_alert=True)
        return
    kb_rows = []
    for p in prods:
        icon = "✅" if p['stock'] > 0 else "❌"
        kb_rows.append([InlineKeyboardButton(
            text=f"{icon} {p['name']}  ·  {fmt_price(p['price'])}",
            callback_data=f"prod_{p['id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="shop")])
    text = f"<blockquote>{ae('down')} Выберите товар:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

# ══════════════════════════════════════════════
#  Карточка товара
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("prod_"))
async def cb_prod(cb: types.CallbackQuery):
    pid = int(cb.data.split("_")[1])
    p   = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    sizes   = parse_sizes(p)
    sizes_s = "  ".join(sizes) if sizes else "—"
    stock   = p['stock']
    stock_s = f"✅ В наличии ({stock} шт.)" if stock > 0 else "❌ Нет в наличии"

    # Контакты продавца
    seller_block = ""
    if p['seller_phone'] or p['seller_username']:
        seller_block = "━━━━━━━━━━━━━━━━━\n"
        if p['seller_phone']:
            seller_block += f"📞 <b>Продавец:</b> <code>{p['seller_phone']}</code>\n"
        if p['seller_username']:
            un = p['seller_username'].lstrip('@')
            seller_block += f"💬 <b>Telegram:</b> @{un}\n"

    text = (
        f"╔═══════════════════╗\n"
        f"║ {ae('tag')} <b>{p['name']}</b>\n"
        f"╚═══════════════════╝\n\n"
        f"<blockquote>{p['description']}</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('money')} <b>Цена:</b>  <code>{fmt_price(p['price'])}</code>\n"
        f"📐 <b>Размеры:</b>  {sizes_s}\n"
        f"📦 <b>Статус:</b>  {stock_s}\n"
        f"{seller_block}"
        f"━━━━━━━━━━━━━━━━━"
    )

    kb_rows = []
    if stock > 0:
        kb_rows.append([InlineKeyboardButton(
            text="🛒 Купить", callback_data=f"buy_{pid}"
        )])
    kb_rows.append([InlineKeyboardButton(
        text="⭐ Отзывы", callback_data=f"reviews_{pid}"
    )])
    kb_rows.append([InlineKeyboardButton(
        text="‹ Назад", callback_data=f"cat_{p['category_id']}"
    )])

    try:
        await cb.message.delete()
    except Exception:
        pass
    await send_media(cb.from_user.id, text, f"product_{pid}",
                     InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

# ══════════════════════════════════════════════
#  Отзывы
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("reviews_"))
async def cb_reviews(cb: types.CallbackQuery):
    pid     = int(cb.data.split("_")[1])
    reviews = await get_reviews(pid, limit=10)
    if not reviews:
        await cb.answer("Отзывов пока нет. Станьте первым! 🙌", show_alert=True)
        return
    stars_map = {1: "★☆☆☆☆", 2: "★★☆☆☆", 3: "★★★☆☆", 4: "★★★★☆", 5: "★★★★★"}
    text = f"{ae('star')} <b>Отзывы о товаре</b>\n\n━━━━━━━━━━━━━━━━━\n"
    for rv in reviews:
        stars = stars_map.get(rv['rating'], "")
        dt    = rv['created_at'][:10]
        text += f"<b>{stars}</b>  <i>{dt}</i>\n{rv['comment']}\n\n"
    text += "━━━━━━━━━━━━━━━━━"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ К товару", callback_data=f"prod_{pid}")
    ]])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ══════════════════════════════════════════════
#  Выбор размера
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("buy_"))
async def cb_buy(cb: types.CallbackQuery):
    pid = int(cb.data.split("_")[1])
    p   = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    if p['stock'] <= 0:
        await cb.answer("😔 Товар закончился", show_alert=True)
        return

    sizes = parse_sizes(p)
    if not sizes:
        await _show_payment_confirm(cb, pid, "ONE_SIZE")
        return

    kb_rows = [[
        InlineKeyboardButton(text=f"📐 {s}", callback_data=f"size_{pid}_{s}")
    ] for s in sizes]
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=f"prod_{pid}")])

    text = (
        f"📐 <b>Выберите размер</b>\n\n"
        f"<blockquote>Товар: <b>{p['name']}</b></blockquote>"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("size_"))
async def cb_size(cb: types.CallbackQuery):
    parts     = cb.data.split("_", 2)
    pid, size = int(parts[1]), parts[2]
    await _show_payment_confirm(cb, pid, size)

# ══════════════════════════════════════════════
#  Подтверждение данных доставки из профиля
# ══════════════════════════════════════════════
async def _show_payment_confirm(cb: types.CallbackQuery, pid: int, size: str):
    """
    Показываем телефон + адрес из профиля.
    Если не заполнены — предлагаем заполнить.
    Если заполнены — предлагаем выбрать способ оплаты.
    """
    p    = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    user = await get_user(cb.from_user.id)

    rate    = await get_usd_kzt_rate()
    usd_amt = kzt_to_usd(p['price'], rate)

    phone   = user['phone']           if user['phone']           else None
    address = user['default_address'] if user['default_address'] else None

    phone_s   = f"<code>{phone}</code>" if phone else "<i>не указан ❗</i>"
    address_s = f"<i>{address}</i>"     if address else "<i>не указан ❗</i>"

    text = (
        f"🛍 <b>Оформление заказа</b>\n\n"
        f"📦 {p['name']}  ({size})\n"
        f"{ae('money')} <b>Цена:</b> <code>{fmt_price(p['price'])}</code> "
        f"(~{usd_amt} USDT)\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📞 <b>Телефон:</b> {phone_s}\n"
        f"📍 <b>Адрес:</b> {address_s}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

    kb_rows = []
    if phone and address:
        # Всё заполнено — показываем кнопки оплаты
        kb_rows.append([InlineKeyboardButton(
            text="🔐 CryptoBot (USDT)",
            callback_data=f"pcrypto_{pid}_{size}"
        )])
        kb_rows.append([InlineKeyboardButton(
            text="🏦 Kaspi",
            callback_data=f"pkaspi_{pid}_{size}"
        )])
        kb_rows.append([InlineKeyboardButton(
            text="✏️ Изменить данные доставки",
            callback_data="profile_phone"
        )])
    else:
        # Профиль не заполнен — направляем
        kb_rows.append([InlineKeyboardButton(
            text="👤 Заполнить профиль (телефон + адрес)",
            callback_data="profile_phone"
        )])
        text += "\n\n⚠️ <b>Заполните профиль для оформления заказа.</b>"

    kb_rows.append([InlineKeyboardButton(
        text="‹ Назад", callback_data=f"buy_{pid}"
    )])

    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

# ══════════════════════════════════════════════
#  Оплата через CryptoBot
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("pcrypto_"))
async def cb_pcrypto(cb: types.CallbackQuery):
    parts     = cb.data.split("_", 2)
    pid, size = int(parts[1]), parts[2]
    p         = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    rate    = await get_usd_kzt_rate()
    usd_amt = kzt_to_usd(p['price'], rate)

    inv = await create_invoice(
        usd_amt,
        f"Покупка: {p['name']} ({size})",
        f"{cb.from_user.id}:{pid}:{size}"
    )
    if not inv:
        await cb.answer("⚠️ Ошибка создания счёта. Попробуйте позже.", show_alert=True)
        return

    await save_crypto(cb.from_user.id, pid, size,
                      str(inv['invoice_id']), p['price'], usd_amt)

    text = (
        f"🔐 <b>Оплата через CryptoBot</b>\n\n"
        f"📦 <b>Товар:</b> {p['name']}\n"
        f"📐 <b>Размер:</b> {size}\n"
        f"{ae('money')} <b>Сумма:</b> <code>{fmt_price(p['price'])}</code> "
        f"(~<b>{usd_amt} USDT</b>)\n\n"
        f"<blockquote>1. Нажмите «Оплатить»\n"
        f"2. Вернитесь в бот\n"
        f"3. Нажмите «Проверить оплату»</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=inv['pay_url'])],
        [InlineKeyboardButton(text="✅ Проверить оплату",
                              callback_data=f"chk_{inv['invoice_id']}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data=f"buy_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("chk_"))
async def cb_chk(cb: types.CallbackQuery):
    """Проверить оплату CryptoBot и оформить заказ."""
    inv_id  = cb.data[4:]
    inv     = await check_invoice(inv_id)
    if not inv:
        await cb.answer("⚠️ Ошибка проверки. Попробуйте позже.", show_alert=True)
        return
    if inv['status'] != 'paid':
        await cb.answer("⏳ Оплата ещё не поступила.", show_alert=True)
        return

    payment = await get_crypto(inv_id)
    if not payment or payment['status'] == 'paid':
        await cb.answer("Счёт уже обработан!", show_alert=True)
        return

    await set_crypto_paid(inv_id)

    uid     = cb.from_user.id
    pid     = payment['product_id']
    size    = payment['size']
    price   = payment['amount_kzt']
    product = await get_product(pid)
    user    = await get_user(uid)

    oid   = await create_order(uid, pid, size, price, 'crypto',
                               user['phone'], user['default_address'])
    await add_purchase(uid, pid, price, 'crypto')
    await reduce_stock(pid)
    bonus = await add_bonus(uid, price)

    try:
        await cb.message.delete()
    except Exception:
        pass

    await _notify_manager_new_order(
        oid, uid, cb.from_user.username, product, size, price,
        'CryptoBot', user['phone'], user['default_address']
    )

    await bot.send_message(
        uid,
        f"🎉 <b>Оплата подтверждена! Заказ #{oid} оформлен.</b>\n\n"
        f"📦 {product['name']}  ({size})\n"
        f"{ae('money')} {fmt_price(price)}\n"
        f"📞 {user['phone']}\n"
        f"📍 {user['default_address']}\n\n"
        f"{ae('gift')} Кэшбэк: <b>{fmt_price(bonus)}</b> на бонусный счёт\n\n"
        f"<blockquote>Мы свяжемся с вами для согласования доставки.</blockquote>",
        parse_mode="HTML",
        reply_markup=kb_main()
    )
    await cb.answer("✅ Готово!")

# ══════════════════════════════════════════════
#  Оплата через Kaspi
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("pkaspi_"))
async def cb_pkaspi(cb: types.CallbackQuery):
    parts     = cb.data.split("_", 2)
    pid, size = int(parts[1]), parts[2]
    p         = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    kid = await save_kaspi(cb.from_user.id, pid, size, p['price'])

    text = (
        f"🏦 <b>Оплата через Kaspi</b>\n\n"
        f"📦 <b>Товар:</b> {p['name']}  ({size})\n"
        f"{ae('money')} <b>Сумма:</b> <code>{fmt_price(p['price'])}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📱 Номер для перевода:\n"
        f"<code>{KASPI_PHONE}</code>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>После перевода нажмите «Я оплатил» — "
        f"менеджер проверит и подтвердит вручную.</blockquote>"
    )
    # Кодируем kid_pid_size в callback_data
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил",
                              callback_data=f"kpaid_{kid}_{pid}_{size}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data=f"buy_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("kpaid_"))
async def cb_kpaid(cb: types.CallbackQuery):
    """Пользователь нажал 'Я оплатил' — уведомляем менеджера."""
    parts = cb.data.split("_")
    kid   = int(parts[1])
    pid   = int(parts[2])
    size  = parts[3] if len(parts) > 3 else "?"

    kp = await get_kaspi(kid)
    if not kp:
        await cb.answer("Платёж не найден", show_alert=True)
        return
    if kp['status'] != 'pending':
        await cb.answer("Этот платёж уже обработан", show_alert=True)
        return

    product = await get_product(pid)
    user    = await get_user(cb.from_user.id)
    uname   = cb.from_user.username or "—"

    # Карточка менеджеру с кнопками подтвердить / отклонить
    mgr_text = (
        f"🏦 <b>ЗАЯВКА KASPI #{kid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 @{uname} (<code>{kp['user_id']}</code>)\n"
        f"📦 <b>Товар:</b> {product['name']}  ({size})\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(kp['amount'])}\n"
        f"📞 <b>Телефон:</b> {user['phone'] if user else '—'}\n"
        f"📍 <b>Адрес:</b> {user['default_address'] if user else '—'}\n"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>Проверьте поступление перевода:</blockquote>"
    )
    mgr_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить",
                             callback_data=f"kapprove_{kid}"),
        InlineKeyboardButton(text="❌ Отклонить",
                             callback_data=f"kreject_{kid}"),
    ]])
    try:
        mgr_msg = await bot.send_message(
            MANAGER_ID, mgr_text, parse_mode="HTML", reply_markup=mgr_kb
        )
        await set_kaspi_status(kid, 'waiting', mgr_msg.message_id)
    except Exception:
        await cb.answer(
            "⚠️ Не удалось уведомить менеджера. Обратитесь в поддержку.",
            show_alert=True
        )
        return

    try:
        await cb.message.edit_text(
            f"⏳ <b>Ожидаем подтверждения менеджера</b>\n\n"
            f"<blockquote>Обычно это занимает несколько минут.</blockquote>",
            parse_mode="HTML",
            reply_markup=kb_back("shop")
        )
    except Exception:
        pass
    await cb.answer("✅ Заявка отправлена!")

@router.callback_query(F.data.startswith("kapprove_"))
async def cb_kapprove(cb: types.CallbackQuery):
    """Менеджер подтверждает Kaspi-оплату."""
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    kid = int(cb.data.split("_")[1])
    kp  = await get_kaspi(kid)
    if not kp:
        await cb.answer("Платёж не найден", show_alert=True)
        return
    if kp['status'] == 'paid':
        await cb.answer("Уже подтверждено!", show_alert=True)
        return
    if kp['status'] == 'rejected':
        await cb.answer("Платёж отклонён — нельзя подтвердить", show_alert=True)
        return

    product = await get_product(kp['product_id'])
    user    = await get_user(kp['user_id'])
    size    = kp['size']

    await set_kaspi_status(kid, 'paid')

    oid = await create_order(
        kp['user_id'], kp['product_id'], size, kp['amount'], 'kaspi',
        user['phone']           if user else '',
        user['default_address'] if user else ''
    )
    await add_purchase(kp['user_id'], kp['product_id'], kp['amount'], 'kaspi')
    await reduce_stock(kp['product_id'])
    bonus = await add_bonus(kp['user_id'], kp['amount'])

    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n✅ <b>ПОДТВЕРЖДЕНО</b> — @{who}",
            parse_mode="HTML"
        )
    except Exception:
        pass

    # Уведомляем менеджера об автоматически созданном заказе
    await _notify_manager_new_order(
        oid, kp['user_id'], None, product, size, kp['amount'],
        'Kaspi', user['phone'] if user else '', user['default_address'] if user else ''
    )

    try:
        await bot.send_message(
            kp['user_id'],
            f"✅ <b>Оплата подтверждена! Заказ #{oid} оформлен.</b>\n\n"
            f"📦 {product['name']}  ({size})\n"
            f"{ae('money')} {fmt_price(kp['amount'])}\n"
            f"{ae('gift')} Кэшбэк: <b>{fmt_price(bonus)}</b>\n\n"
            f"<blockquote>Ожидайте уведомлений о статусе доставки!</blockquote>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer("✅ Подтверждено!")

@router.callback_query(F.data.startswith("kreject_"))
async def cb_kreject(cb: types.CallbackQuery):
    """Менеджер отклоняет Kaspi-оплату."""
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    kid = int(cb.data.split("_")[1])
    kp  = await get_kaspi(kid)
    if not kp or kp['status'] in ('paid', 'rejected'):
        await cb.answer("Платёж уже обработан", show_alert=True)
        return

    product = await get_product(kp['product_id'])
    await set_kaspi_status(kid, 'rejected')
    try:
        await bot.send_message(
            kp['user_id'],
            f"❌ <b>Оплата отклонена</b>\n\n"
            f"📦 {product['name']} — {fmt_price(kp['amount'])}\n\n"
            f"<blockquote>Менеджер не нашёл перевод. "
            f"Если уверены в оплате — напишите: {SUPPORT_USERNAME}</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="❓ Поддержка",
                    url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"
                )
            ]])
        )
    except Exception:
        pass
    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n❌ <b>ОТКЛОНЕНО</b> — @{who}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer("❌ Отклонено, пользователь уведомлён")

# ══════════════════════════════════════════════
#  Уведомление менеджера о новом заказе
# ══════════════════════════════════════════════
async def _notify_manager_new_order(oid, uid, uname, product,
                                    size, price, method, phone, address):
    text = (
        f"🛍 <b>НОВЫЙ ЗАКАЗ #{oid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 @{uname or '—'} (<code>{uid}</code>)\n"
        f"📦 <b>Товар:</b> {product['name']}\n"
        f"📐 <b>Размер:</b> {size}\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(price)}\n"
        f"💳 <b>Оплата:</b> {method}\n"
        f"📞 <b>Телефон:</b> {phone or '—'}\n"
        f"📍 <b>Адрес:</b> {address or '—'}\n"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Управление статусом",
                             callback_data=f"ordstatus_{oid}")
    ]])
    try:
        await bot.send_message(MANAGER_ID, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

# ══════════════════════════════════════════════
#  Управление статусами заказов
# ══════════════════════════════════════════════
ORDER_STATUSES = ["processing", "china", "arrived", "delivered"]

@router.callback_query(F.data.startswith("ordstatus_"))
async def cb_ordstatus(cb: types.CallbackQuery):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    oid   = int(cb.data.split("_")[1])
    order = await get_order(oid)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True)
        return

    kb_rows = []
    for s in ORDER_STATUSES:
        mark = "✓ " if order['status'] == s else ""
        kb_rows.append([InlineKeyboardButton(
            text=f"{mark}{order_status_text(s)}",
            callback_data=f"setordst_{oid}_{s}"
        )])
    text = (
        f"📋 <b>Заказ #{oid}</b>\n"
        f"Статус: {order_status_text(order['status'])}\n\n"
        f"<blockquote>Выберите новый статус:</blockquote>"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("setordst_"))
async def cb_setordst(cb: types.CallbackQuery):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts  = cb.data.split("_", 2)
    oid    = int(parts[1])
    status = parts[2]
    order  = await get_order(oid)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True)
        return

    await set_order_status(oid, status)
    product = await get_product(order['product_id'])

    try:
        if status == "delivered":
            await bot.send_message(
                order['user_id'],
                f"🚚 <b>Ваш заказ #{oid} доставлен!</b>\n\n"
                f"📦 {product['name']}  ({order['size']})\n\n"
                f"<blockquote>Пожалуйста, подтвердите получение:</blockquote>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="✅ Подтверждаю получение",
                        callback_data=f"confirm_order_{oid}"
                    )
                ]])
            )
        else:
            await bot.send_message(
                order['user_id'],
                f"{ae('truck')} <b>Статус заказа #{oid} обновлён</b>\n\n"
                f"📦 {product['name']}  ({order['size']})\n"
                f"🔄 <b>Новый статус:</b> {order_status_text(status)}",
                parse_mode="HTML"
            )
    except Exception:
        pass

    await cb.answer(f"✅ {order_status_text(status)}", show_alert=True)
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n→ {order_status_text(status)}",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ══════════════════════════════════════════════
#  Подтверждение получения → отзыв
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("confirm_order_"))
async def cb_confirm_order(cb: types.CallbackQuery, state: FSMContext):
    oid   = int(cb.data.split("_")[-1])
    order = await get_order(oid)
    if not order or order['user_id'] != cb.from_user.id:
        await cb.answer("Заказ не найден", show_alert=True)
        return

    await set_order_status(oid, 'confirmed')
    try:
        await bot.send_message(
            MANAGER_ID,
            f"✅ <b>Заказ #{oid} подтверждён покупателем.</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await state.update_data(review_oid=oid, review_pid=order['product_id'])
    await state.set_state(ReviewSt.rating)

    try:
        await cb.message.edit_text(
            f"🎉 <b>Спасибо за подтверждение!</b>\n\n"
            f"<blockquote>Оцените товар от 1 до 5 звёзд:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=str(i), callback_data=f"rating_{i}")
                for i in range(1, 6)
            ]])
        )
    except Exception:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("rating_"), ReviewSt.rating)
async def cb_rating(cb: types.CallbackQuery, state: FSMContext):
    rating    = int(cb.data.split("_")[1])
    stars_map = {1: "★☆☆☆☆", 2: "★★☆☆☆", 3: "★★★☆☆", 4: "★★★★☆", 5: "★★★★★"}
    await state.update_data(rating=rating)
    await state.set_state(ReviewSt.comment)
    try:
        await cb.message.edit_text(
            f"Оценка: <b>{stars_map[rating]}</b>\n\n"
            f"<blockquote>Напишите ваш отзыв о товаре:</blockquote>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer()

@router.message(ReviewSt.comment)
async def review_comment(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await add_review(
        msg.from_user.id,
        d['review_pid'],
        d['review_oid'],
        d['rating'],
        msg.text,
    )
    await msg.answer(
        f"⭐ <b>Спасибо за отзыв!</b>\n\n"
        f"<blockquote>Ваш отзыв поможет другим покупателям.</blockquote>",
        parse_mode="HTML",
        reply_markup=kb_main()
    )

# ══════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════
def admin_guard(uid: int) -> bool:
    return uid in ADMIN_IDS

@router.callback_query(F.data == "adm_panel")
async def cb_adm_panel(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.clear()
    try:
        await cb.message.edit_text("🎩 <b>Панель управления</b>",
                                   parse_mode="HTML", reply_markup=kb_admin())
    except Exception:
        await cb.message.answer("🎩 <b>Панель управления</b>",
                                parse_mode="HTML", reply_markup=kb_admin())
    await cb.answer()

@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uc, pc, rv, ac, oc = await get_stats()
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👥 Пользователей: <b>{uc}</b>\n"
        f"{ae('cart')} Заказов: <b>{pc}</b>\n"
        f"{ae('money')} Выручка: <b>{fmt_price(rv)}</b>\n"
        f"📦 Товаров: <b>{ac}</b>\n"
        f"🔄 В работе: <b>{oc}</b>\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb_admin_back())
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb_admin_back())
    await cb.answer()

@router.callback_query(F.data == "adm_orders")
async def cb_adm_orders(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    orders = await db_all(
        '''SELECT o.*, p.name AS pname
           FROM orders o JOIN products p ON o.product_id=p.id
           ORDER BY o.created_at DESC LIMIT 20'''
    )
    if not orders:
        await cb.answer("Заказов пока нет", show_alert=True)
        return
    kb_rows = []
    for o in orders:
        label = (
            f"#{o['id']} {o['pname'][:10]} ({o['size']}) "
            f"— {order_status_text(o['status'])}"
        )
        kb_rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"ordstatus_{o['id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "📋 <b>Заказы</b>\n<blockquote>Нажмите для управления:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer(
            "📋 <b>Заказы</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    await cb.answer()

# ── Медиа ──────────────────────────────────────
@router.callback_query(F.data == "adm_media")
async def cb_adm_media(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главная",   callback_data="smedia_main_menu")],
        [InlineKeyboardButton(text="🛒 Магазин",   callback_data="smedia_shop_menu")],
        [InlineKeyboardButton(text="🏬 О нас",     callback_data="smedia_about_menu")],
        [InlineKeyboardButton(text="❓ Поддержка", callback_data="smedia_support_menu")],
        [InlineKeyboardButton(text="‹ Назад",      callback_data="adm_panel")],
    ])
    try:
        await cb.message.edit_text(
            "🖼 <b>Настройка медиа</b>\n\n<blockquote>Выберите раздел:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "🖼 <b>Настройка медиа</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("smedia_"))
async def cb_smedia(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    key = cb.data[7:]
    await state.update_data(media_key=key)
    await state.set_state(AdminSt.set_media_file)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить медиа", callback_data=f"delmedia_{key}")],
        [InlineKeyboardButton(text="‹ Назад",          callback_data="adm_media")],
    ])
    try:
        await cb.message.edit_text(
            "🖼 <b>Отправьте фото, видео (9:16 / 5:9) или GIF:</b>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "🖼 <b>Отправьте фото, видео или GIF:</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("delmedia_"))
async def cb_delmedia(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    key = cb.data[9:]
    await db_run('DELETE FROM media_settings WHERE key=?', (key,))
    await state.clear()
    await cb.answer("✅ Медиа удалено", show_alert=True)
    await cb_adm_media(cb)

@router.message(AdminSt.set_media_file,
                F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO,
                                    ContentType.ANIMATION]))
async def proc_media_file(msg: types.Message, state: FSMContext):
    d   = await state.get_data()
    key = d.get("media_key")
    if msg.photo:
        fid, mt = msg.photo[-1].file_id, "photo"
    elif msg.video:
        fid, mt = msg.video.file_id, "video"
    elif msg.animation:
        fid, mt = msg.animation.file_id, "animation"
    else:
        await msg.answer("❌ Неподдерживаемый формат", reply_markup=kb_admin_back())
        return
    await set_media(key, mt, fid)
    await state.clear()
    await msg.answer("✅ Медиа установлено!", reply_markup=kb_admin_back())

# ── Рассылка ───────────────────────────────────
@router.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.set_state(AdminSt.broadcast)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")
    ]])
    try:
        await cb.message.edit_text(
            "📨 <b>Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "📨 <b>Рассылка</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.broadcast)
async def proc_broadcast(msg: types.Message, state: FSMContext):
    await state.clear()
    users  = await all_user_ids()
    ok = fail = 0
    status = await msg.answer("📤 Рассылка началась...")
    for uid in users:
        try:
            if msg.photo:
                await bot.send_photo(uid, msg.photo[-1].file_id,
                                     caption=msg.caption, parse_mode="HTML")
            elif msg.video:
                await bot.send_video(uid, msg.video.file_id,
                                     caption=msg.caption, parse_mode="HTML")
            elif msg.animation:
                await bot.send_animation(uid, msg.animation.file_id,
                                         caption=msg.caption, parse_mode="HTML")
            else:
                await bot.send_message(uid, msg.text, parse_mode="HTML")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await status.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n📤 Отправлено: {ok}\n❌ Ошибок: {fail}",
        parse_mode="HTML", reply_markup=kb_admin_back()
    )

# ── Категории ──────────────────────────────────
@router.callback_query(F.data == "adm_cats")
async def cb_adm_cats(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cats    = await get_categories()
    kb_rows = []
    for c in cats:
        kb_rows.append([
            InlineKeyboardButton(text=f"📂 {c['name']}",
                                 callback_data=f"ecat_{c['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"dcat_{c['id']}"),
        ])
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="addcat")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад",    callback_data="adm_panel")])
    text = f"{ae('folder')} <b>Категории</b>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data == "addcat")
async def cb_addcat(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.set_state(AdminSt.add_cat_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Назад", callback_data="adm_cats")
    ]])
    try:
        await cb.message.edit_text(
            f"{ae('folder')} <b>Новая категория</b>\n\n"
            f"<blockquote>Введите название:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            f"{ae('folder')} <b>Новая категория</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.add_cat_name)
async def proc_cat_name(msg: types.Message, state: FSMContext):
    await add_category(msg.text)
    await state.clear()
    await msg.answer("✅ Категория добавлена!", reply_markup=kb_admin_back())

@router.callback_query(F.data.startswith("dcat_"))
async def cb_dcat(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[1])
    await del_category(cid)
    await cb.answer("✅ Категория удалена", show_alert=True)
    await cb_adm_cats(cb)

# ── Товары ─────────────────────────────────────
@router.callback_query(F.data == "adm_products")
async def cb_adm_products(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cats    = await get_categories()
    kb_rows = [[InlineKeyboardButton(
        text=f"📂 {c['name']}", callback_data=f"apcat_{c['id']}"
    )] for c in cats]
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="addprod")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "📦 <b>Товары</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer(
            "📦 <b>Товары</b>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("apcat_"))
async def cb_apcat(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cid     = int(cb.data.split("_")[1])
    prods   = await get_products(cid)
    kb_rows = []
    for p in prods:
        kb_rows.append([
            InlineKeyboardButton(
                text=f"📦 {p['name']} — {fmt_price(p['price'])} (x{p['stock']})",
                callback_data=f"vprod_{p['id']}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"dprod_{p['id']}"),
        ])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")])
    try:
        await cb.message.edit_text(
            "<blockquote>📦 Товары категории:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer(
            "<blockquote>📦 Товары:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("vprod_"))
async def cb_vprod(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    pid = int(cb.data.split("_")[1])
    p   = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    sizes = parse_sizes(p)
    text  = (
        f"📦 <b>{p['name']}</b>\n\n"
        f"{p['description']}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('money')} <b>Цена:</b> {fmt_price(p['price'])}\n"
        f"📐 <b>Размеры:</b> {', '.join(sizes) or '—'}\n"
        f"📦 <b>Остаток:</b> {p['stock']} шт.\n"
        f"📞 <b>Тел. продавца:</b> {p['seller_phone'] or '—'}\n"
        f"💬 <b>TG продавца:</b> "
        f"{'@' + p['seller_username'] if p['seller_username'] else '—'}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    try:
        await cb.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")
            ]])
        )
    except Exception:
        await cb.message.answer(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")
            ]])
        )
    await cb.answer()

@router.callback_query(F.data.startswith("dprod_"))
async def cb_dprod(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    pid = int(cb.data.split("_")[1])
    await del_product(pid)
    await cb.answer("✅ Товар удалён", show_alert=True)
    try:
        await cb.message.edit_text(
            "✅ Товар удалён",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")
            ]])
        )
    except Exception:
        pass

# ── Добавление товара (7 шагов) ────────────────
@router.callback_query(F.data == "addprod")
async def cb_addprod(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    cats = await get_categories()
    if not cats:
        await cb.answer("Сначала создайте категорию!", show_alert=True)
        return
    kb = [[InlineKeyboardButton(
        text=f"📂 {c['name']}", callback_data=f"npcat_{c['id']}"
    )] for c in cats]
    kb.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")])
    try:
        await cb.message.edit_text(
            "📦 <b>Новый товар</b>\n\n"
            "<blockquote>Шаг 1/7 — Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    except Exception:
        await cb.message.answer(
            "📦 <b>Новый товар</b>\n\n"
            "<blockquote>Шаг 1/7 — Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("npcat_"))
async def cb_npcat(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[1])
    await state.update_data(cid=cid)
    await state.set_state(AdminSt.add_prod_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Назад", callback_data="addprod")
    ]])
    try:
        await cb.message.edit_text(
            "📦 <b>Шаг 2/7 — Название товара</b>\n\n"
            "<blockquote>Введите название (можно с эмодзи):</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "📦 <b>Шаг 2/7 — Название</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.add_prod_name)
async def proc_prod_name(msg: types.Message, state: FSMContext):
    name = msg.html_text if msg.entities else msg.text
    await state.update_data(name=name)
    await state.set_state(AdminSt.add_prod_desc)
    await msg.answer(
        "📦 <b>Шаг 3/7 — Описание товара</b>\n\n"
        "<blockquote>Введите описание:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="‹ Назад", callback_data="addprod")
        ]])
    )

@router.message(AdminSt.add_prod_desc)
async def proc_prod_desc(msg: types.Message, state: FSMContext):
    desc = msg.html_text if msg.entities else msg.text
    await state.update_data(desc=desc)
    await state.set_state(AdminSt.add_prod_price)
    await msg.answer(
        "📦 <b>Шаг 4/7 — Цена в тенге ₸</b>\n\n"
        "<blockquote>Введите цену (например: 5000):</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="‹ Назад", callback_data="addprod")
        ]])
    )

@router.message(AdminSt.add_prod_price)
async def proc_prod_price(msg: types.Message, state: FSMContext):
    try:
        price = float(msg.text.replace(",", ".").replace(" ", ""))
    except ValueError:
        await msg.answer("❌ Введите число, например: <code>5000</code>",
                         parse_mode="HTML")
        return
    await state.update_data(price=price)
    await state.set_state(AdminSt.add_prod_sizes)
    await msg.answer(
        "📦 <b>Шаг 5/7 — Размеры</b>\n\n"
        "<blockquote>Введите размеры через запятую:\n"
        "<i>Например: S, M, L, XL</i>\n\n"
        "Нет размеров — напишите <b>нет</b></blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="‹ Назад", callback_data="addprod")
        ]])
    )

@router.message(AdminSt.add_prod_sizes)
async def proc_prod_sizes(msg: types.Message, state: FSMContext):
    raw = msg.text.strip()
    if raw.lower() in ("нет", "no", "-", "—"):
        sizes_list = []
    else:
        sizes_list = [s.strip().upper() for s in raw.split(",") if s.strip()]
    await state.update_data(sizes=sizes_list)
    await state.set_state(AdminSt.add_prod_stock)
    await msg.answer(
        "📦 <b>Шаг 6/7 — Остаток на складе</b>\n\n"
        "<blockquote>Введите количество (например: 10):</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="‹ Назад", callback_data="addprod")
        ]])
    )

@router.message(AdminSt.add_prod_stock)
async def proc_prod_stock(msg: types.Message, state: FSMContext):
    try:
        stock = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите целое число, например: <code>10</code>",
                         parse_mode="HTML")
        return
    await state.update_data(stock=stock)
    await state.set_state(AdminSt.add_prod_seller_ph)
    await msg.answer(
        "📦 <b>Шаг 7/7 — Телефон продавца</b>\n\n"
        "<blockquote>Введите номер телефона продавца:\n"
        "<i>Пример: +7 701 234 56 78</i></blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="‹ Назад", callback_data="addprod")
        ]])
    )

@router.message(AdminSt.add_prod_seller_ph)
async def proc_prod_seller_ph(msg: types.Message, state: FSMContext):
    await state.update_data(seller_phone=msg.text.strip())
    await state.set_state(AdminSt.add_prod_seller_un)
    await msg.answer(
        "📦 <b>Telegram-юзернейм продавца (необязательно)</b>\n\n"
        "<blockquote>Введите @username или напишите <b>нет</b>:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="‹ Назад", callback_data="addprod")
        ]])
    )

@router.message(AdminSt.add_prod_seller_un)
async def proc_prod_seller_un(msg: types.Message, state: FSMContext):
    raw       = msg.text.strip()
    seller_un = "" if raw.lower() in ("нет", "no", "-", "—") else raw.lstrip("@")
    d         = await state.get_data()
    await add_product(
        d['cid'], d['name'], d['desc'],
        d['price'], d.get('sizes', []), d['stock'],
        seller_username=seller_un,
        seller_phone=d.get('seller_phone', '')
    )
    await state.clear()
    sizes_str = ', '.join(d.get('sizes', [])) or '—'
    await msg.answer(
        f"✅ <b>Товар добавлен!</b>\n\n"
        f"📐 Размеры: {sizes_str}\n"
        f"📦 Остаток: {d['stock']} шт.\n"
        f"{ae('money')} Цена: {fmt_price(d['price'])}\n"
        f"📞 Продавец: {d.get('seller_phone', '—')}\n"
        f"💬 TG: {'@' + seller_un if seller_un else '—'}",
        parse_mode="HTML",
        reply_markup=kb_admin_back()
    )

# ── Настройки ──────────────────────────────────
@router.callback_query(F.data == "adm_settings")
async def cb_adm_settings(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Описание магазина", callback_data="edit_shop_info")],
        [InlineKeyboardButton(text="‹ Назад",              callback_data="adm_panel")],
    ])
    try:
        await cb.message.edit_text("⚙️ <b>Настройки</b>",
                                   parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer("⚙️ <b>Настройки</b>",
                                parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "edit_shop_info")
async def cb_edit_shop(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.set_state(AdminSt.edit_shop_info)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Назад", callback_data="adm_settings")
    ]])
    try:
        await cb.message.edit_text(
            "📝 <b>Описание магазина</b>\n\n"
            "<blockquote>Введите новое описание:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "📝 <b>Описание магазина</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.edit_shop_info)
async def proc_shop_info(msg: types.Message, state: FSMContext):
    await set_setting("shop_info", msg.text)
    await state.clear()
    await msg.answer("✅ Описание обновлено!", reply_markup=kb_admin_back())

# ══════════════════════════════════════════════
#  Сброс FSM при навигации
# ══════════════════════════════════════════════
NAV_CALLBACKS = {
    "adm_panel", "adm_media", "adm_cats",
    "adm_products", "addprod", "adm_settings"
}

@router.callback_query(F.data.in_(NAV_CALLBACKS))
async def nav_clear_state(cb: types.CallbackQuery, state: FSMContext):
    if await state.get_state():
        await state.clear()

# ══════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    print("\033[35m" + "═" * 46)
    print("  🛍  SHOPBOT — Шымкент, Казахстан")
    print("═" * 46 + "\033[0m")
    print(f"  💱 Курс USD/KZT: {USD_KZT_RATE} (фикс.)")
    print(f"  🎁 Кэшбэк: {CASHBACK_PERCENT}%")
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
