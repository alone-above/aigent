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
    InputMediaPhoto, InputMediaVideo,
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

# Фиксированный курс USD/KZT (можно заменить на live-запрос)
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
#  Анимированные эмодзи (только в тексте сообщений)
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
    "star":    '<tg-emoji emoji-id="5368324170671202286">⭐</tg-emoji>',
    "gift":    '<tg-emoji emoji-id="5431456208487708461">🎁</tg-emoji>',
    "truck":   '<tg-emoji emoji-id="5431736674147114227">🚚</tg-emoji>',
    "check":   '<tg-emoji emoji-id="5368324170671202286">✅</tg-emoji>',
    "tag":     '<tg-emoji emoji-id="5467666648263564704">🏷</tg-emoji>',
}
def ae(k): return AE.get(k, "")

# ══════════════════════════════════════════════
#  FSM-состояния
# ══════════════════════════════════════════════
class AdminSt(StatesGroup):
    broadcast       = State()
    set_media_file  = State()
    add_cat_name    = State()
    add_prod_cat    = State()
    add_prod_name   = State()
    add_prod_desc   = State()
    add_prod_price  = State()
    add_prod_sizes  = State()   # новый шаг: размеры через запятую
    add_prod_stock  = State()   # новый шаг: остаток
    edit_shop_info  = State()

class OrderSt(StatesGroup):
    """Сбор данных для доставки после оплаты."""
    phone   = State()
    address = State()

class ReviewSt(StatesGroup):
    """Сбор отзыва после подтверждения получения."""
    rating  = State()
    comment = State()

# ══════════════════════════════════════════════
#  База данных
# ══════════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            -- Пользователи
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT,
                first_name      TEXT,
                total_purchases INTEGER DEFAULT 0,
                total_spent     REAL    DEFAULT 0,
                bonus_balance   REAL    DEFAULT 0,  -- бонусный баланс (кэшбэк)
                registered_at   TEXT
            );

            -- Категории
            CREATE TABLE IF NOT EXISTS categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );

            -- Товары (физические: одежда)
            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name        TEXT NOT NULL,
                description TEXT,
                price       REAL NOT NULL,       -- цена в KZT
                sizes       TEXT DEFAULT '',     -- JSON-список размеров: ["S","M","L","XL"]
                stock       INTEGER DEFAULT 0,   -- общий остаток на складе
                is_active   INTEGER DEFAULT 1,
                created_at  TEXT,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );

            -- Заказы (физические товары)
            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                product_id  INTEGER,
                size        TEXT,
                price       REAL,              -- в KZT
                method      TEXT DEFAULT 'crypto',
                phone       TEXT,
                address     TEXT,
                status      TEXT DEFAULT 'processing',
                -- Статусы: processing | china | arrived | delivered | confirmed
                created_at  TEXT
            );

            -- Покупки (совместимость + статистика)
            CREATE TABLE IF NOT EXISTS purchases (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
                product_id   INTEGER,
                price        REAL,
                method       TEXT DEFAULT 'crypto',
                purchased_at TEXT
            );

            -- Медиа-настройки разделов
            CREATE TABLE IF NOT EXISTS media_settings (
                key        TEXT PRIMARY KEY,
                media_type TEXT,
                file_id    TEXT
            );

            -- Настройки магазина
            CREATE TABLE IF NOT EXISTS shop_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            -- Крипто-платежи (CryptoBot)
            CREATE TABLE IF NOT EXISTS crypto_payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                product_id INTEGER,
                size       TEXT,
                invoice_id TEXT UNIQUE,
                amount_kzt REAL,
                amount_usd REAL,
                status     TEXT DEFAULT 'pending',
                created_at TEXT
            );

            -- Kaspi-платежи
            CREATE TABLE IF NOT EXISTS kaspi_payments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                product_id     INTEGER,
                size           TEXT,
                amount         REAL,
                status         TEXT DEFAULT 'pending',
                manager_msg_id INTEGER DEFAULT 0,
                created_at     TEXT
            );

            -- Отзывы (только после подтверждения получения)
            CREATE TABLE IF NOT EXISTS reviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                product_id INTEGER,
                order_id   INTEGER,
                rating     INTEGER,    -- 1-5
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
        'INSERT OR IGNORE INTO users (user_id,username,first_name,registered_at) VALUES(?,?,?,?)',
        (u.id, u.username, u.first_name, datetime.now().isoformat())
    )

async def get_user(uid):
    return await db_one('SELECT * FROM users WHERE user_id=?', (uid,))

async def add_bonus(uid, amount_kzt):
    """Начислить кэшбэк на бонусный баланс."""
    bonus = round(amount_kzt * CASHBACK_PERCENT / 100, 0)
    await db_run(
        'UPDATE users SET bonus_balance=bonus_balance+? WHERE user_id=?',
        (bonus, uid)
    )
    return bonus

# ── Категории ──────────────────────────────────
async def get_categories():  return await db_all('SELECT * FROM categories ORDER BY id')
async def add_category(n):   await db_run('INSERT INTO categories(name) VALUES(?)', (n,))
async def del_category(cid):
    await db_run('UPDATE products SET is_active=0 WHERE category_id=?', (cid,))
    await db_run('DELETE FROM categories WHERE id=?', (cid,))

# ── Товары ─────────────────────────────────────
async def get_products(cid):
    return await db_all(
        'SELECT * FROM products WHERE category_id=? AND is_active=1',
        (cid,)
    )

async def get_product(pid):
    return await db_one('SELECT * FROM products WHERE id=?', (pid,))

async def add_product(cid, name, desc, price, sizes_list, stock):
    """Добавить физический товар (одежда)."""
    sizes_json = json.dumps(sizes_list, ensure_ascii=False)
    await db_run(
        '''INSERT INTO products
           (category_id,name,description,price,sizes,stock,created_at)
           VALUES(?,?,?,?,?,?,?)''',
        (cid, name, desc, price, sizes_json, stock, datetime.now().isoformat())
    )

async def del_product(pid):
    await db_run('UPDATE products SET is_active=0 WHERE id=?', (pid,))

async def reduce_stock(pid):
    """Уменьшить остаток на 1 после покупки."""
    await db_run(
        'UPDATE products SET stock=MAX(0,stock-1) WHERE id=?',
        (pid,)
    )

def parse_sizes(product) -> list:
    """Получить список размеров из товара."""
    try:
        return json.loads(product['sizes'] or '[]')
    except Exception:
        return []

# ── Заказы ─────────────────────────────────────
async def create_order(uid, pid, size, price, method, phone='', address=''):
    return await db_insert(
        '''INSERT INTO orders
           (user_id,product_id,size,price,method,phone,address,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)''',
        (uid, pid, size, price, method, phone, address, 'processing', datetime.now().isoformat())
    )

async def get_order(oid):
    return await db_one('SELECT * FROM orders WHERE id=?', (oid,))

async def set_order_status(oid, status):
    await db_run('UPDATE orders SET status=? WHERE id=?', (status, oid))

async def get_user_orders(uid):
    return await db_all(
        '''SELECT o.*, p.name AS pname
           FROM orders o JOIN products p ON o.product_id=p.id
           WHERE o.user_id=? ORDER BY o.created_at DESC LIMIT 10''',
        (uid,)
    )

# ── Покупки (статистика) ────────────────────────
async def add_purchase(uid, pid, price, method='crypto'):
    await db_run(
        'INSERT INTO purchases(user_id,product_id,price,method,purchased_at) VALUES(?,?,?,?,?)',
        (uid, pid, price, method, datetime.now().isoformat())
    )
    await db_run(
        'UPDATE users SET total_purchases=total_purchases+1, total_spent=total_spent+? WHERE user_id=?',
        (price, uid)
    )

async def get_purchases(uid):
    return await db_all(
        '''SELECT p.*, pr.name AS pname FROM purchases p
           JOIN products pr ON p.product_id=pr.id
           WHERE p.user_id=? ORDER BY p.purchased_at DESC LIMIT 10''',
        (uid,)
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

# ── Статистика ─────────────────────────────────
async def get_stats():
    uc  = (await db_one('SELECT COUNT(*) c FROM users'))['c']
    pc  = (await db_one('SELECT COUNT(*) c FROM purchases'))['c']
    rv  = (await db_one('SELECT COALESCE(SUM(price),0) s FROM purchases'))['s']
    ac  = (await db_one('SELECT COUNT(*) c FROM products WHERE is_active=1'))['c']
    oc  = (await db_one("SELECT COUNT(*) c FROM orders WHERE status NOT IN ('delivered','confirmed')"))['c']
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
    await db_run('UPDATE crypto_payments SET status=? WHERE invoice_id=?', ('paid', inv_id))

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
        await db_run('UPDATE kaspi_payments SET status=?,manager_msg_id=? WHERE id=?',
                     (status, mgr_mid, kid))
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
    """
    Получить актуальный курс USD→KZT через открытое API.
    При ошибке используется фиксированное значение USD_KZT_RATE.
    """
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

def kzt_to_usd(kzt_amount: float, rate: float) -> float:
    """Конвертировать тенге в доллары (2 знака после запятой)."""
    return round(kzt_amount / rate, 2)

async def create_invoice(amount_usd: float, desc: str, payload: str):
    """Создать счёт в CryptoBot на сумму в USDT."""
    url = "https://pay.crypt.bot/api/createInvoice"
    hdr = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
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
        [InlineKeyboardButton(text="📊 Статистика",    callback_data="adm_stats")],
        [InlineKeyboardButton(text="🖼 Медиа",         callback_data="adm_media"),
         InlineKeyboardButton(text="📨 Рассылка",      callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="📦 Товары",        callback_data="adm_products"),
         InlineKeyboardButton(text="📁 Категории",     callback_data="adm_cats")],
        [InlineKeyboardButton(text="📋 Заказы",        callback_data="adm_orders")],
        [InlineKeyboardButton(text="⚙️ Настройки",     callback_data="adm_settings")],
    ])

def kb_admin_back():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Админ панель", callback_data="adm_panel")]]
    )

# ══════════════════════════════════════════════
#  Хелперы
# ══════════════════════════════════════════════
async def send_media(chat_id, text, key, markup=None):
    """Отправить сообщение с медиа (если назначено) или без него."""
    m = await get_media(key)
    if m:
        mt = m["media_type"]
        if mt == "photo":
            await bot.send_photo(chat_id, m["file_id"], caption=text,
                                 parse_mode="HTML", reply_markup=markup)
        elif mt == "video":
            await bot.send_video(chat_id, m["file_id"], caption=text,
                                 parse_mode="HTML", reply_markup=markup)
        elif mt == "animation":
            await bot.send_animation(chat_id, m["file_id"], caption=text,
                                     parse_mode="HTML", reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

async def set_cmds(uid):
    cmds = [BotCommand(command="start", description="🚀 Старт")]
    if uid in ADMIN_IDS:
        cmds.append(BotCommand(command="admin", description="🎩 Панель"))
    await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))

def fmt_dt():
    return datetime.now().strftime("%d.%m.%Y %H:%M")

def fmt_price(kzt: float) -> str:
    """Форматировать сумму в тенге: 5 000 ₸"""
    return f"{kzt:,.0f} ₸".replace(",", " ")

# Текстовые описания статусов заказа
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
    await msg.answer("🎩 <b>Панель управления</b>", parse_mode="HTML", reply_markup=kb_admin())

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
    await bot.send_message(cb.from_user.id, text, parse_mode="HTML", reply_markup=kb_main())
    await cb.answer()

# ══════════════════════════════════════════════
#  Reply-кнопки главного меню
# ══════════════════════════════════════════════
@router.message(F.text == "🛒 Купить")
async def txt_shop(msg: types.Message):
    await show_catalog(msg.chat.id)

@router.message(F.text == "👤 Профиль")
async def txt_profile(msg: types.Message):
    user = await get_user(msg.from_user.id)
    if not user:
        await ensure_user(msg.from_user)
        user = await get_user(msg.from_user.id)
    orders = await get_user_orders(msg.from_user.id)
    text = (
        f"👤 <b>Мой профиль</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{msg.from_user.id}</code>\n"
        f"{ae('cart')} <b>Заказов:</b> {user['total_purchases']}\n"
        f"{ae('money')} <b>Потрачено:</b> {fmt_price(user['total_spent'])}\n"
        f"{ae('gift')} <b>Бонусы:</b> {fmt_price(user['bonus_balance'])}\n"
        f"{ae('cal')} <b>Регистрация:</b> {user['registered_at'][:10]}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    if orders:
        text += "\n\n📋 <b>Последние заказы:</b>\n"
        for o in orders[:5]:
            status = order_status_text(o['status'])
            text += f"  • {o['pname']} ({o['size']}) — <b>{fmt_price(o['price'])}</b>  <i>{status}</i>\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Все заказы", callback_data="my_orders")]
    ])
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)

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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать",
                              url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")]
    ])
    await send_media(msg.chat.id, text, "support_menu", kb)

# ══════════════════════════════════════════════
#  Каталог
# ══════════════════════════════════════════════
async def show_catalog(chat_id):
    cats = await get_categories()
    if not cats:
        await bot.send_message(chat_id, "📭 Категории пока не добавлены.")
        return
    kb = [[InlineKeyboardButton(text=f"🗂 {c['name']}",
                                callback_data=f"cat_{c['id']}")] for c in cats]
    text = f"{ae('cart')} <b>Каталог</b>\n\n<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    await send_media(chat_id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "shop")
async def cb_shop(cb: types.CallbackQuery):
    cats = await get_categories()
    if not cats:
        await cb.answer("Категории пока не добавлены", show_alert=True)
        return
    kb = [[InlineKeyboardButton(text=f"🗂 {c['name']}",
                                callback_data=f"cat_{c['id']}")] for c in cats]
    text = f"{ae('cart')} <b>Каталог</b>\n\n<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()

@router.callback_query(F.data.startswith("cat_"))
async def cb_cat(cb: types.CallbackQuery):
    cid = int(cb.data.split("_")[1])
    prods = await get_products(cid)
    if not prods:
        await cb.answer("В этой категории пока нет товаров", show_alert=True)
        return
    kb = []
    for p in prods:
        # Показываем статус наличия прямо в кнопке
        stock_icon = "✅" if p['stock'] > 0 else "❌"
        kb.append([InlineKeyboardButton(
            text=f"{stock_icon} {p['name']}  ·  {fmt_price(p['price'])}",
            callback_data=f"prod_{p['id']}"
        )])
    kb.append([InlineKeyboardButton(text="‹ Назад", callback_data="shop")])
    text = f"<blockquote>{ae('down')} Выберите товар:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
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

    # ── Карточка товара ──────────────────────
    text = (
        f"╔══════════════════════╗\n"
        f"║  {ae('tag')} <b>{p['name']}</b>\n"
        f"╚══════════════════════╝\n\n"
        f"<blockquote>{p['description']}</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('money')} <b>Цена:</b>  <code>{fmt_price(p['price'])}</code>\n"
        f"📐 <b>Размеры:</b>  {sizes_s}\n"
        f"📦 <b>Статус:</b>  {stock_s}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

    buy_btn = []
    if stock > 0:
        buy_btn = [[InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy_{pid}")]]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        *buy_btn,
        [InlineKeyboardButton(text="⭐ Отзывы", callback_data=f"reviews_{pid}")],
        [InlineKeyboardButton(text="‹ Назад",   callback_data=f"cat_{p['category_id']}")],
    ])

    try:
        await cb.message.delete()
    except Exception:
        pass

    # Попытка отправить медиа товара
    await send_media(cb.from_user.id, text, f"product_{pid}", kb)
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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ К товару", callback_data=f"prod_{pid}")]
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ══════════════════════════════════════════════
#  Выбор размера → выбор метода оплаты
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("buy_"))
async def cb_buy(cb: types.CallbackQuery):
    """Показать доступные размеры для выбора."""
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
        # Если размеры не заданы — сразу к оплате
        await _show_payment(cb, pid, "ONE_SIZE")
        return

    kb_rows = []
    for s in sizes:
        kb_rows.append([InlineKeyboardButton(
            text=f"📐 {s}",
            callback_data=f"size_{pid}_{s}"
        )])
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
    """Размер выбран — переходим к выбору метода оплаты."""
    parts = cb.data.split("_", 2)
    pid, size = int(parts[1]), parts[2]
    await _show_payment(cb, pid, size)

async def _show_payment(cb: types.CallbackQuery, pid: int, size: str):
    """Показать выбор метода оплаты."""
    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    # Получаем актуальный курс и показываем сумму в тенге и USDT
    rate      = await get_usd_kzt_rate()
    usd_amt   = kzt_to_usd(p['price'], rate)

    text = (
        f"💳 <b>Способ оплаты</b>\n\n"
        f"📦 <b>Товар:</b> {p['name']}\n"
        f"📐 <b>Размер:</b> {size}\n"
        f"{ae('money')} <b>Цена:</b> <code>{fmt_price(p['price'])}</code> "
        f"(<i>~{usd_amt} USDT</i>)\n\n"
        f"<blockquote>{ae('down')} Выберите удобный способ оплаты:</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 CryptoBot (USDT)",
                              callback_data=f"pcrypto_{pid}_{size}")],
        [InlineKeyboardButton(text="🏦 Kaspi",
                              callback_data=f"pkaspi_{pid}_{size}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data=f"buy_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ══════════════════════════════════════════════
#  Оплата через CryptoBot
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("pcrypto_"))
async def cb_pcrypto(cb: types.CallbackQuery, state: FSMContext):
    parts     = cb.data.split("_", 2)
    pid, size = int(parts[1]), parts[2]
    p         = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    rate    = await get_usd_kzt_rate()
    usd_amt = kzt_to_usd(p['price'], rate)

    inv = await create_invoice(usd_amt, f"Покупка: {p['name']} ({size})",
                               f"{cb.from_user.id}:{pid}:{size}")
    if not inv:
        await cb.answer("⚠️ Ошибка создания счёта. Попробуйте позже.", show_alert=True)
        return

    await save_crypto(cb.from_user.id, pid, size, str(inv['invoice_id']),
                      p['price'], usd_amt)

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
async def cb_chk(cb: types.CallbackQuery, state: FSMContext):
    """Проверить статус оплаты и запустить сбор адреса доставки."""
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
        await cb.answer("Этот счёт уже обработан!", show_alert=True)
        return

    await set_crypto_paid(inv_id)

    # Сохраняем данные оплаты в FSM и запрашиваем адрес
    await state.update_data(
        pid=payment['product_id'],
        size=payment['size'],
        price_kzt=payment['amount_kzt'],
        method='crypto',
        inv_id=inv_id,
    )
    await state.set_state(OrderSt.phone)
    try:
        await cb.message.delete()
    except Exception:
        pass
    await bot.send_message(
        cb.from_user.id,
        "✅ <b>Оплата подтверждена!</b>\n\n"
        "📞 <b>Введите ваш контактный телефон</b> для доставки:\n"
        "<i>Пример: +7 701 234 56 78</i>",
        parse_mode="HTML",
    )
    await cb.answer()

# ══════════════════════════════════════════════
#  Оплата через Kaspi
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("pkaspi_"))
async def cb_pkaspi(cb: types.CallbackQuery, state: FSMContext):
    parts     = cb.data.split("_", 2)
    pid, size = int(parts[1]), parts[2]
    p         = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    # Сохраняем в FSM для сбора адреса ДО подтверждения оплаты
    await state.update_data(pid=pid, size=size, price_kzt=p['price'], method='kaspi')

    kid = await save_kaspi(cb.from_user.id, pid, size, p['price'])
    await state.update_data(kaspi_id=kid)
    await state.set_state(OrderSt.phone)

    text = (
        f"🏦 <b>Оплата через Kaspi</b>\n\n"
        f"📦 <b>Товар:</b> {p['name']}  ({size})\n"
        f"{ae('money')} <b>Сумма:</b> <code>{fmt_price(p['price'])}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📱 Номер для перевода:\n"
        f"<code>{KASPI_PHONE}</code>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>После перевода укажите ваш телефон и адрес для доставки.</blockquote>"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=None)
    except Exception:
        pass

    await bot.send_message(
        cb.from_user.id,
        "📞 <b>Введите ваш контактный телефон</b>:\n<i>Пример: +7 701 234 56 78</i>",
        parse_mode="HTML",
    )
    await cb.answer()

# ══════════════════════════════════════════════
#  FSM: сбор данных для доставки
# ══════════════════════════════════════════════
@router.message(OrderSt.phone)
async def order_phone(msg: types.Message, state: FSMContext):
    """Шаг 1: получить телефон, запросить адрес."""
    await state.update_data(phone=msg.text)
    await state.set_state(OrderSt.address)
    await msg.answer(
        "📍 <b>Введите район или точный адрес в Шымкенте:</b>\n"
        "<i>Пример: мкр Нурсат, ул. Байтурсынова 12, кв. 5</i>",
        parse_mode="HTML",
    )

@router.message(OrderSt.address)
async def order_address(msg: types.Message, state: FSMContext):
    """Шаг 2: получить адрес, создать заказ и уведомить менеджера."""
    d = await state.get_data()
    await state.clear()

    uid       = msg.from_user.id
    pid       = d['pid']
    size      = d['size']
    price_kzt = d['price_kzt']
    method    = d['method']
    phone     = d['phone']
    address   = msg.text
    product   = await get_product(pid)

    # Создаём заказ
    oid = await create_order(uid, pid, size, price_kzt, method, phone, address)

    # Если крипто — обновляем статистику сразу
    if method == 'crypto':
        await add_purchase(uid, pid, price_kzt, 'crypto')
        await reduce_stock(pid)
        bonus = await add_bonus(uid, price_kzt)

        # Если Kaspi — только после подтверждения менеджера
    elif method == 'kaspi':
        kid = d.get('kaspi_id')
        if kid:
            await set_kaspi_status(kid, 'waiting')

    # Уведомляем менеджера
    uname    = msg.from_user.username or "—"
    mgr_text = (
        f"🛍 <b>НОВЫЙ ЗАКАЗ #{oid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 @{uname} (<code>{uid}</code>)\n"
        f"📦 <b>Товар:</b> {product['name']}\n"
        f"📐 <b>Размер:</b> {size}\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(price_kzt)}\n"
        f"💳 <b>Оплата:</b> {'CryptoBot' if method == 'crypto' else 'Kaspi'}\n"
        f"📞 <b>Телефон:</b> {phone}\n"
        f"📍 <b>Адрес:</b> {address}\n"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

    mgr_kb_rows = []
    if method == 'kaspi' and d.get('kaspi_id'):
        kid = d['kaspi_id']
        mgr_kb_rows.append([
            InlineKeyboardButton(text="✅ Подтвердить оплату",
                                 callback_data=f"kapprove_{kid}_{oid}"),
            InlineKeyboardButton(text="❌ Отклонить",
                                 callback_data=f"kreject_{kid}"),
        ])

    # Кнопки смены статуса заказа для менеджера
    mgr_kb_rows.append([
        InlineKeyboardButton(text="📋 Статус заказа",
                             callback_data=f"ordstatus_{oid}")
    ])

    mgr_kb = InlineKeyboardMarkup(inline_keyboard=mgr_kb_rows)
    try:
        await bot.send_message(MANAGER_ID, mgr_text, parse_mode="HTML", reply_markup=mgr_kb)
    except Exception:
        pass

    # Подтверждение пользователю
    bonus_line = ""
    if method == 'crypto':
        bonus_line = f"\n{ae('gift')} На ваш бонусный счёт начислено <b>{fmt_price(bonus)}</b>!"

    await msg.answer(
        f"🎉 <b>Заказ #{oid} оформлен!</b>\n\n"
        f"📦 {product['name']}  ({size})\n"
        f"{ae('money')} {fmt_price(price_kzt)}\n"
        f"📞 {phone}\n"
        f"📍 {address}\n"
        f"{bonus_line}\n\n"
        f"<blockquote>Мы свяжемся с вами для согласования доставки. "
        f"Статус заказа можно отслеживать в разделе «👤 Профиль».</blockquote>",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )

# ══════════════════════════════════════════════
#  Менеджер: подтверждение/отклонение Kaspi
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("kapprove_"))
async def cb_kapprove(cb: types.CallbackQuery):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split("_")
    kid = int(parts[1])
    oid = int(parts[2]) if len(parts) > 2 else None

    kp = await get_kaspi(kid)
    if not kp:
        await cb.answer("Платёж не найден", show_alert=True)
        return
    if kp['status'] == 'paid':
        await cb.answer("Уже подтверждено!", show_alert=True)
        return

    await set_kaspi_status(kid, 'paid')
    await add_purchase(kp['user_id'], kp['product_id'], kp['amount'], 'kaspi')
    await reduce_stock(kp['product_id'])
    bonus = await add_bonus(kp['user_id'], kp['amount'])

    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n✅ <b>ОПЛАТА ПОДТВЕРЖДЕНА</b> — @{who}",
            parse_mode="HTML"
        )
    except Exception:
        pass

    # Уведомляем покупателя
    try:
        product = await get_product(kp['product_id'])
        await bot.send_message(
            kp['user_id'],
            f"✅ <b>Оплата подтверждена!</b>\n\n"
            f"📦 {product['name']}  ({kp['size']})\n"
            f"{ae('money')} {fmt_price(kp['amount'])}\n"
            f"{ae('gift')} Кэшбэк: <b>{fmt_price(bonus)}</b> на бонусный счёт\n\n"
            f"<blockquote>Ваш заказ принят в обработку. "
            f"Ожидайте уведомлений о статусе!</blockquote>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await cb.answer("✅ Подтверждено!")

@router.callback_query(F.data.startswith("kreject_"))
async def cb_kreject(cb: types.CallbackQuery):
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
            f"Если вы уверены — напишите в поддержку: {SUPPORT_USERNAME}</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❓ Поддержка",
                                     url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")
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
#  Управление статусами заказов (менеджер/админ)
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
        f"Текущий статус: {order_status_text(order['status'])}\n\n"
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

    # Уведомляем покупателя об изменении статуса
    product = await get_product(order['product_id'])
    try:
        if status == "delivered":
            # Статус «Доставлено» — просим подтвердить получение
            await bot.send_message(
                order['user_id'],
                f"🚚 <b>Ваш заказ доставлен!</b>\n\n"
                f"📦 {product['name']}  ({order['size']})\n\n"
                f"<blockquote>Пожалуйста, подтвердите получение:</blockquote>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Подтверждаю получение",
                                         callback_data=f"confirm_order_{oid}")
                ]])
            )
        else:
            await bot.send_message(
                order['user_id'],
                f"{ae('truck')} <b>Статус вашего заказа #{oid} обновлён</b>\n\n"
                f"📦 {product['name']}  ({order['size']})\n"
                f"🔄 <b>Новый статус:</b> {order_status_text(status)}",
                parse_mode="HTML",
            )
    except Exception:
        pass

    await cb.answer(f"✅ Статус обновлён: {order_status_text(status)}", show_alert=True)
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n✅ Статус → {order_status_text(status)}",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ══════════════════════════════════════════════
#  Покупатель: подтверждение получения → отзыв
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("confirm_order_"))
async def cb_confirm_order(cb: types.CallbackQuery, state: FSMContext):
    oid   = int(cb.data.split("_")[-1])
    order = await get_order(oid)
    if not order or order['user_id'] != cb.from_user.id:
        await cb.answer("Заказ не найден", show_alert=True)
        return

    await set_order_status(oid, 'confirmed')

    # Уведомляем менеджера
    try:
        await bot.send_message(
            MANAGER_ID,
            f"✅ <b>Заказ #{oid} подтверждён покупателем.</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Предлагаем оставить отзыв
    await state.update_data(review_oid=oid, review_pid=order['product_id'])
    await state.set_state(ReviewSt.rating)

    try:
        await cb.message.edit_text(
            f"🎉 <b>Отлично! Вы подтвердили получение.</b>\n\n"
            f"<blockquote>Оцените товар от 1 до 5:</blockquote>",
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
    rating = int(cb.data.split("_")[1])
    await state.update_data(rating=rating)
    await state.set_state(ReviewSt.comment)
    stars_map = {1: "★☆☆☆☆", 2: "★★☆☆☆", 3: "★★★☆☆", 4: "★★★★☆", 5: "★★★★★"}
    try:
        await cb.message.edit_text(
            f"Оценка: <b>{stars_map[rating]}</b>\n\n"
            f"<blockquote>Напишите ваш отзыв о товаре:</blockquote>",
            parse_mode="HTML",
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
        f"⭐ <b>Спасибо за ваш отзыв!</b>\n\n"
        f"<blockquote>Ваш отзыв поможет другим покупателям сделать правильный выбор.</blockquote>",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )

# ══════════════════════════════════════════════
#  История заказов
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
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb_back("main"))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb_back("main"))
    await cb.answer()

# ══════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════
def admin_guard(uid): return uid in ADMIN_IDS

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

# ── Статистика ─────────────────────────────────
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

# ── Заказы (панель менеджера) ──────────────────
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
        label = f"#{o['id']} {o['pname'][:12]} ({o['size']}) — {order_status_text(o['status'])}"
        kb_rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"ordstatus_{o['id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])

    try:
        await cb.message.edit_text(
            "📋 <b>Все заказы</b>\n<blockquote>Нажмите на заказ для управления:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer(
            "📋 <b>Все заказы</b>",
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
            "🖼 <b>Настройка медиа</b>\n\n<blockquote>Выберите раздел:</blockquote>",
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
            "🖼 <b>Отправьте фото, видео (9:16 / 5:9) или GIF:</b>\n"
            "<i>Вертикальные видео поддерживаются.</i>",
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
                F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION]))
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")]
    ])
    try:
        await cb.message.edit_text(
            "📨 <b>Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "📨 <b>Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.broadcast)
async def proc_broadcast(msg: types.Message, state: FSMContext):
    await state.clear()
    users  = await all_user_ids()
    ok     = fail = 0
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
    cats   = await get_categories()
    kb_rows = []
    for c in cats:
        kb_rows.append([
            InlineKeyboardButton(text=f"📂 {c['name']}", callback_data=f"ecat_{c['id']}"),
            InlineKeyboardButton(text="🗑",               callback_data=f"dcat_{c['id']}"),
        ])
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="addcat")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад",     callback_data="adm_panel")])
    text = f"{ae('folder')} <b>Категории</b>\n\n<blockquote>Управление категориями:</blockquote>"
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_cats")]
    ])
    try:
        await cb.message.edit_text(
            f"{ae('folder')} <b>Новая категория</b>\n\n<blockquote>Введите название:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            f"{ae('folder')} <b>Новая категория</b>\n\n<blockquote>Введите название:</blockquote>",
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

# ── Просмотр / удаление товаров ────────────────
@router.callback_query(F.data == "adm_products")
async def cb_adm_products(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cats   = await get_categories()
    kb_rows = [[InlineKeyboardButton(text=f"📂 {c['name']}",
                                     callback_data=f"apcat_{c['id']}")] for c in cats]
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="addprod")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад",           callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "📦 <b>Товары</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer(
            "📦 <b>Товары</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("apcat_"))
async def cb_apcat(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cid   = int(cb.data.split("_")[1])
    prods = await get_products(cid)
    kb_rows = []
    for p in prods:
        kb_rows.append([
            InlineKeyboardButton(text=f"📦 {p['name']} — {fmt_price(p['price'])} (x{p['stock']})",
                                 callback_data=f"vprod_{p['id']}"),
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
            "<blockquote>📦 Товары категории:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("vprod_"))
async def cb_vprod(cb: types.CallbackQuery):
    """Просмотр товара из админки."""
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
        f"━━━━━━━━━━━━━━━━━"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                       [InlineKeyboardButton(text="‹ Назад",
                                                             callback_data="adm_products")]
                                   ]))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="‹ Назад",
                                                          callback_data="adm_products")]
                                ]))
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

# ── Добавление товара (FSM) ────────────────────
@router.callback_query(F.data == "addprod")
async def cb_addprod(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    cats = await get_categories()
    if not cats:
        await cb.answer("Сначала создайте категорию!", show_alert=True)
        return
    kb = [[InlineKeyboardButton(text=f"📂 {c['name']}",
                                callback_data=f"npcat_{c['id']}")] for c in cats]
    kb.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")])
    try:
        await cb.message.edit_text(
            "📦 <b>Новый товар</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    except Exception:
        await cb.message.answer(
            "📦 <b>Новый товар</b>\n\n<blockquote>Выберите категорию:</blockquote>",
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]
    ])
    try:
        await cb.message.edit_text(
            "📦 <b>Название товара</b>\n\n"
            "<blockquote>Введите название.\n💡 Можно использовать анимированные эмодзи.</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "📦 <b>Название товара</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.add_prod_name)
async def proc_prod_name(msg: types.Message, state: FSMContext):
    name = msg.html_text if msg.entities else msg.text
    await state.update_data(name=name)
    await state.set_state(AdminSt.add_prod_desc)
    await msg.answer(
        "📦 <b>Описание товара</b>\n\n<blockquote>Введите описание:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]
        ])
    )

@router.message(AdminSt.add_prod_desc)
async def proc_prod_desc(msg: types.Message, state: FSMContext):
    desc = msg.html_text if msg.entities else msg.text
    await state.update_data(desc=desc)
    await state.set_state(AdminSt.add_prod_price)
    await msg.answer(
        "📦 <b>Цена товара</b>\n\n<blockquote>Введите цену в тенге ₸ (например: 5000):</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]
        ])
    )

@router.message(AdminSt.add_prod_price)
async def proc_prod_price(msg: types.Message, state: FSMContext):
    try:
        price = float(msg.text.replace(",", ".").replace(" ", ""))
    except ValueError:
        await msg.answer("❌ Введите корректную цену, например: <code>5000</code>",
                         parse_mode="HTML")
        return
    await state.update_data(price=price)
    await state.set_state(AdminSt.add_prod_sizes)
    await msg.answer(
        "📐 <b>Размеры</b>\n\n"
        "<blockquote>Введите доступные размеры через запятую:\n"
        "<i>Например: S, M, L, XL</i>\n\n"
        "Если размеры не нужны — напишите <b>нет</b></blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]
        ])
    )

@router.message(AdminSt.add_prod_sizes)
async def proc_prod_sizes(msg: types.Message, state: FSMContext):
    raw = msg.text.strip()
    if raw.lower() in ("нет", "no", "-"):
        sizes_list = []
    else:
        sizes_list = [s.strip().upper() for s in raw.split(",") if s.strip()]
    await state.update_data(sizes=sizes_list)
    await state.set_state(AdminSt.add_prod_stock)
    await msg.answer(
        "📦 <b>Остаток на складе</b>\n\n"
        "<blockquote>Введите количество единиц в наличии (например: 10):</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]
        ])
    )

@router.message(AdminSt.add_prod_stock)
async def proc_prod_stock(msg: types.Message, state: FSMContext):
    try:
        stock = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите целое число, например: <code>10</code>",
                         parse_mode="HTML")
        return
    d = await state.get_data()
    await add_product(
        d['cid'], d['name'], d['desc'],
        d['price'], d.get('sizes', []), stock
    )
    await state.clear()
    await msg.answer(
        f"✅ <b>Товар добавлен!</b>\n\n"
        f"📐 Размеры: {', '.join(d.get('sizes', [])) or '—'}\n"
        f"📦 Остаток: {stock} шт.\n"
        f"{ae('money')} Цена: {fmt_price(d['price'])}",
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
        await cb.message.edit_text("⚙️ <b>Настройки</b>", parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer("⚙️ <b>Настройки</b>", parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "edit_shop_info")
async def cb_edit_shop(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.set_state(AdminSt.edit_shop_info)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_settings")]
    ])
    try:
        await cb.message.edit_text(
            "📝 <b>Описание магазина</b>\n\n<blockquote>Введите новое описание:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(
            "📝 <b>Описание магазина</b>\n\n<blockquote>Введите новое описание:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.edit_shop_info)
async def proc_shop_info(msg: types.Message, state: FSMContext):
    await set_setting("shop_info", msg.text)
    await state.clear()
    await msg.answer("✅ Описание обновлено!", reply_markup=kb_admin_back())

# ══════════════════════════════════════════════
#  Сброс FSM при навигации по меню
# ══════════════════════════════════════════════
NAV = {"adm_panel", "adm_media", "adm_cats", "adm_products", "addprod", "adm_settings"}

@router.callback_query(F.data.in_(NAV))
async def nav_clear_state(cb: types.CallbackQuery, state: FSMContext):
    if await state.get_state():
        await state.clear()

# ══════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    print("\033[35m" + "═" * 44)
    print("  🛍  SHOPBOT — Шымкент, Казахстан")
    print("═" * 44 + "\033[0m")
    print(f"  💱 Курс USD/KZT: {USD_KZT_RATE} (фикс.)")
    print(f"  🎁 Кэшбэк: {CASHBACK_PERCENT}%")
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
