"""
╔══════════════════════════════════════════════════════╗
║  SHOPBOT — Магазин одежды / Шымкент, Казахстан      ║
║  aiogram 3.x  |  asyncpg (PostgreSQL)  |  CryptoPay ║
╚══════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import json
import io
import zipfile
import aiohttp
import ssl
from datetime import datetime, timedelta
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

DATABASE_URL = os.getenv(
    "DATABASE_PUBLIC_URL",
    "postgresql://postgres:hbDPoVFnfBPweyFjjmWYdmOtgRBrtzyn@yamabiko.proxy.rlwy.net:26709/railway"
)

USD_KZT_RATE: float    = 494.0
CASHBACK_PERCENT: float = 5.0

# ══════════════════════════════════════════════
#  База данных — asyncpg (PostgreSQL, aio)
# ══════════════════════════════════════════════
import asyncpg
import time as _time

_pool: asyncpg.Pool = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool

# ── Лёгкий кэш ─────────────────────────────────
_CACHE: dict = {}
CACHE_TTL = 8

def _cache_get(key):
    e = _CACHE.get(key)
    if e and _time.monotonic() < e[1]:
        return e[0], True
    return None, False

def _cache_set(key, value):
    _CACHE[key] = (value, _time.monotonic() + CACHE_TTL)
    return value

def _cache_invalidate(*prefixes):
    dead = [k for k in _CACHE for p in prefixes
            if k == p or k.startswith(p + ":")]
    for k in dead:
        _CACHE.pop(k, None)

# ── Обёртки asyncpg ───────────────────────────
async def db_one(sql, params=()):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *params)
        return dict(row) if row else None

async def db_all(sql, params=()):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

async def db_run(sql, params=()):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql, *params)

async def db_insert(sql, params=()):
    """INSERT ... RETURNING id"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *params)
        return row['id'] if row else None

async def cached_db_one(cache_key, sql, params=()):
    v, hit = _cache_get(cache_key)
    if hit:
        return v
    v = await db_one(sql, params)
    return _cache_set(cache_key, v)

async def cached_db_all(cache_key, sql, params=()):
    v, hit = _cache_get(cache_key)
    if hit:
        return v
    v = await db_all(sql, params)
    return _cache_set(cache_key, v)

bot     = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)
router  = Router()
dp.include_router(router)

# ══════════════════════════════════════════════
#  Анимированные эмодзи
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
    "truck":   '<tg-emoji emoji-id="5431736674147114227">🚚</tg-emoji>',
    "box":     '<tg-emoji emoji-id="5298487770510020895">💤</tg-emoji>',
    "size":    '<tg-emoji emoji-id="5400250414929041085">⚖️</tg-emoji>',
    "phone":   '<tg-emoji emoji-id="5467539229468793355">📞</tg-emoji>',
    "tag":     '<tg-emoji emoji-id="5890883384057533697">🏷</tg-emoji>',
    "gift":    '<tg-emoji emoji-id="5199749070830197566">🎁</tg-emoji>',
    "pin":     '<tg-emoji emoji-id="5983099415689171511">📍</tg-emoji>',
    "user":    '<tg-emoji emoji-id="5373012449597335010">👤</tg-emoji>',
    "promo":   '<tg-emoji emoji-id="5368324170671202286">🎟</tg-emoji>',
}
def ae(k): return AE.get(k, "")

# ══════════════════════════════════════════════
#  FSM-состояния
# ══════════════════════════════════════════════
class AdminSt(StatesGroup):
    broadcast          = State()
    set_media_file     = State()
    add_cat_name       = State()
    add_cat_parent     = State()
    add_prod_name      = State()
    add_prod_desc      = State()
    add_prod_price     = State()
    add_prod_sizes     = State()
    add_prod_stock     = State()
    add_prod_seller_ph = State()
    add_prod_seller_un = State()
    add_prod_card      = State()
    add_prod_gallery   = State()
    add_drop_cat       = State()
    add_drop_name      = State()
    add_drop_desc      = State()
    add_drop_price     = State()
    add_drop_sizes     = State()
    add_drop_stock     = State()
    add_drop_start_at  = State()
    add_drop_card      = State()
    edit_shop_info     = State()
    set_custom_status  = State()
    # Промокоды
    promo_code         = State()
    promo_type         = State()
    promo_value        = State()
    promo_description  = State()
    promo_max_uses     = State()
    # Новые: бан / сообщение пользователю
    ban_user_id        = State()
    msg_user_id        = State()
    msg_user_text      = State()
    # Редактирование товара
    edit_prod_field    = State()
    edit_prod_value    = State()
    # Роли
    role_user_id       = State()
    # Партнёрская программа (настройка от имени админа)
    partner_bonus_new  = State()
    partner_bonus_rep  = State()
    # Редактирование сообщений бота
    bot_msg_key        = State()
    bot_msg_text       = State()
    # Подкатегория
    subcat_parent      = State()
    subcat_name        = State()

class AdSt(StatesGroup):
    description = State()

class ProfileSt(StatesGroup):
    phone   = State()
    address = State()

class ReviewSt(StatesGroup):
    rating  = State()
    comment = State()

class PromoApplySt(StatesGroup):
    entering = State()

class ComplaintSt(StatesGroup):
    order_id    = State()
    description = State()
    file_attach = State()  # ожидание файла/документа

class PartnerSt(StatesGroup):
    choose_ref   = State()
    custom_ref   = State()
    bonus_type   = State()

class OrderNoteSt(StatesGroup):
    entering = State()

# ══════════════════════════════════════════════
#  Инициализация PostgreSQL
# ══════════════════════════════════════════════
async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id         BIGINT PRIMARY KEY,
            username        TEXT DEFAULT '',
            first_name      TEXT DEFAULT '',
            phone           TEXT DEFAULT '',
            default_address TEXT DEFAULT '',
            total_purchases INTEGER DEFAULT 0,
            total_spent     REAL DEFAULT 0,
            bonus_balance   REAL DEFAULT 0,
            registered_at   TEXT,
            agreed_terms    INTEGER DEFAULT 0,
            is_banned       INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS categories (
            id   SERIAL PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id              SERIAL PRIMARY KEY,
            category_id     INTEGER,
            name            TEXT NOT NULL,
            description     TEXT DEFAULT '',
            price           REAL NOT NULL,
            sizes           TEXT DEFAULT '[]',
            stock           INTEGER DEFAULT 0,
            seller_username TEXT DEFAULT '',
            seller_phone    TEXT DEFAULT '',
            card_file_id    TEXT DEFAULT '',
            card_media_type TEXT DEFAULT '',
            gallery         TEXT DEFAULT '[]',
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            username    TEXT DEFAULT '',
            first_name  TEXT DEFAULT '',
            product_id  INTEGER,
            size        TEXT DEFAULT '',
            price       REAL,
            method      TEXT DEFAULT 'crypto',
            phone       TEXT DEFAULT '',
            address     TEXT DEFAULT '',
            promo_code  TEXT DEFAULT '',
            discount    REAL DEFAULT 0,
            status      TEXT DEFAULT 'processing',
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id           SERIAL PRIMARY KEY,
            user_id      BIGINT,
            product_id   INTEGER,
            price        REAL,
            method       TEXT DEFAULT 'crypto',
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
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            product_id  INTEGER,
            size        TEXT DEFAULT '',
            invoice_id  TEXT UNIQUE,
            amount_kzt  REAL,
            amount_usd  REAL,
            promo_code  TEXT DEFAULT '',
            discount    REAL DEFAULT 0,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS kaspi_payments (
            id             SERIAL PRIMARY KEY,
            user_id        BIGINT,
            product_id     INTEGER,
            size           TEXT DEFAULT '',
            amount         REAL,
            promo_code     TEXT DEFAULT '',
            discount       REAL DEFAULT 0,
            status         TEXT DEFAULT 'pending',
            manager_msg_id BIGINT DEFAULT 0,
            created_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            product_id  INTEGER,
            order_id    INTEGER,
            rating      INTEGER,
            comment     TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS ad_requests (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            description TEXT,
            method      TEXT DEFAULT 'crypto',
            amount      REAL DEFAULT 500,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS promocodes (
            id          SERIAL PRIMARY KEY,
            code        TEXT UNIQUE NOT NULL,
            promo_type  TEXT NOT NULL,
            value       REAL DEFAULT 0,
            description TEXT DEFAULT '',
            max_uses    INTEGER DEFAULT 0,
            used_count  INTEGER DEFAULT 0,
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS promo_usage (
            id         SERIAL PRIMARY KEY,
            user_id    BIGINT,
            promo_id   INTEGER,
            order_id   INTEGER DEFAULT 0,
            used_at    TEXT,
            UNIQUE(user_id, promo_id)
        );
        CREATE TABLE IF NOT EXISTS complaints (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            order_id    INTEGER DEFAULT 0,
            description TEXT,
            status      TEXT DEFAULT 'open',
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS event_log (
            id         SERIAL PRIMARY KEY,
            event_type TEXT,
            user_id    BIGINT DEFAULT 0,
            data       TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id    BIGINT PRIMARY KEY,
            role       TEXT DEFAULT 'buyer',
            granted_by BIGINT DEFAULT 0,
            granted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS partners (
            user_id       BIGINT PRIMARY KEY,
            ref_code      TEXT UNIQUE NOT NULL,
            bonus_new     TEXT DEFAULT '{"type":"percent","value":5}',
            bonus_repeat  TEXT DEFAULT '{"type":"percent","value":3}',
            total_invited INTEGER DEFAULT 0,
            total_earned  REAL DEFAULT 0,
            created_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS partner_referrals (
            id           SERIAL PRIMARY KEY,
            partner_id   BIGINT,
            referred_uid BIGINT,
            is_new_buyer INTEGER DEFAULT 1,
            bonus_amount REAL DEFAULT 0,
            order_id     INTEGER DEFAULT 0,
            created_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS order_history (
            id         SERIAL PRIMARY KEY,
            order_id   INTEGER,
            status     TEXT,
            changed_by BIGINT DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS order_notes (
            id         SERIAL PRIMARY KEY,
            order_id   INTEGER UNIQUE,
            note       TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS drops (
            id          SERIAL PRIMARY KEY,
            category_id INTEGER,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            price       REAL NOT NULL,
            sizes       TEXT DEFAULT '[]',
            stock       INTEGER DEFAULT 0,
            start_at    TEXT NOT NULL,
            card_file_id TEXT DEFAULT '',
            card_media_type TEXT DEFAULT '',
            gallery     TEXT DEFAULT '[]',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS bot_messages (
            key        TEXT PRIMARY KEY,
            text       TEXT,
            media_type TEXT DEFAULT '',
            file_id    TEXT DEFAULT ''
        );
        """)
        # Миграции — добавить колонки если нет
        for col_sql in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_code TEXT DEFAULT ''",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS short_id TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''",
            "ALTER TABLE categories ADD COLUMN IF NOT EXISTS parent_id INTEGER DEFAULT 0",
            "ALTER TABLE kaspi_payments ADD COLUMN IF NOT EXISTS buyer_note TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN IF NOT EXISTS file_id TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN IF NOT EXISTS file_type TEXT DEFAULT ''",
        ]:
            try:
                await conn.execute(col_sql)
            except Exception:
                pass
    logging.info('✅ PostgreSQL БД инициализирована')

# ══════════════════════════════════════════════
#  Логирование событий
# ══════════════════════════════════════════════
async def log_event(event_type: str, user_id: int = 0, data: str = ""):
    await db_run(
        "INSERT INTO event_log(event_type,user_id,data,created_at) VALUES($1,$2,$3,$4)",
        (event_type, user_id, data, datetime.now().isoformat())
    )

# ══════════════════════════════════════════════
#  Пользователи
# ══════════════════════════════════════════════
async def ensure_user(u: types.User):
    await db_run(
        '''INSERT INTO users(user_id, username, first_name, registered_at)
           VALUES($1, $2, $3, $4)
           ON CONFLICT(user_id) DO UPDATE SET username=$2, first_name=$3''',
        (u.id, u.username or '', u.first_name or '', datetime.now().isoformat())
    )
    _cache_invalidate(f"user:{u.id}")

async def get_user(uid):
    return await cached_db_one(f"user:{uid}",
                               'SELECT * FROM users WHERE user_id=$1', (uid,))

async def set_agreed_terms(uid: int):
    await db_run('UPDATE users SET agreed_terms=1 WHERE user_id=$1', (uid,))
    _cache_invalidate(f"user:{uid}")

async def has_agreed_terms(uid: int) -> bool:
    u = await get_user(uid)
    return bool(u and u.get('agreed_terms', 0))

async def update_user_phone(uid, phone: str):
    await db_run('UPDATE users SET phone=$1 WHERE user_id=$2', (phone, uid))
    _cache_invalidate(f"user:{uid}")

async def update_user_address(uid, address: str):
    await db_run('UPDATE users SET default_address=$1 WHERE user_id=$2', (address, uid))
    _cache_invalidate(f"user:{uid}")

async def add_bonus(uid, amount_kzt: float) -> float:
    bonus = round(amount_kzt * CASHBACK_PERCENT / 100, 0)
    await db_run('UPDATE users SET bonus_balance=bonus_balance+$1 WHERE user_id=$2', (bonus, uid))
    _cache_invalidate(f"user:{uid}")
    return bonus

async def ban_user(uid: int):
    await db_run('UPDATE users SET is_banned=1 WHERE user_id=$1', (uid,))
    _cache_invalidate(f"user:{uid}")

async def unban_user(uid: int):
    await db_run('UPDATE users SET is_banned=0 WHERE user_id=$1', (uid,))
    _cache_invalidate(f"user:{uid}")

async def is_banned(uid: int) -> bool:
    u = await get_user(uid)
    return bool(u and u.get('is_banned', 0))

async def all_user_ids():
    rows = await db_all('SELECT user_id FROM users WHERE is_banned=0')
    return [r['user_id'] for r in rows]

async def get_all_users(limit=50, offset=0):
    return await db_all(
        'SELECT * FROM users ORDER BY registered_at DESC LIMIT $1 OFFSET $2',
        (limit, offset)
    )

# ══════════════════════════════════════════════
#  Категории
# ══════════════════════════════════════════════
async def get_categories(parent_id: int = 0):
    return await cached_db_all(
        f"categories:p{parent_id}",
        'SELECT * FROM categories WHERE parent_id=$1 ORDER BY id',
        (parent_id,)
    )

async def get_all_categories():
    return await cached_db_all("categories:all", 'SELECT * FROM categories ORDER BY id')

async def get_category(cid: int):
    return await db_one('SELECT * FROM categories WHERE id=$1', (cid,))

async def add_category(name: str, parent_id: int = 0):
    await db_run('INSERT INTO categories(name, parent_id) VALUES($1, $2)', (name, parent_id))
    _cache_invalidate("categories")

async def del_category(cid: int):
    await db_run('UPDATE products SET is_active=0 WHERE category_id=$1', (cid,))
    # Also delete subcategories
    subcats = await db_all('SELECT id FROM categories WHERE parent_id=$1', (cid,))
    for sc in subcats:
        await db_run('UPDATE products SET is_active=0 WHERE category_id=$1', (sc['id'],))
        await db_run('DELETE FROM categories WHERE id=$1', (sc['id'],))
    await db_run('DELETE FROM categories WHERE id=$1', (cid,))
    _cache_invalidate("categories", "products")

# ══════════════════════════════════════════════
#  Товары
# ══════════════════════════════════════════════
import random
import string

def gen_short_id() -> str:
    return ''.join(random.choices(string.digits, k=5))

async def get_products(cid: int):
    return await cached_db_all(
        f"products:{cid}",
        'SELECT * FROM products WHERE category_id=$1 AND is_active=1', (cid,)
    )

async def get_product(pid: int):
    return await cached_db_one(f"product:{pid}",
                               'SELECT * FROM products WHERE id=$1', (pid,))

async def add_product(cid, name, desc, price, sizes_list,
                      stock, seller_username='', seller_phone='',
                      card_file_id='', card_media_type='', gallery=None):
    sizes_json   = json.dumps(sizes_list, ensure_ascii=False)
    gallery_json = json.dumps(gallery or [], ensure_ascii=False)
    short_id = gen_short_id()
    pid = await db_insert(
        '''INSERT INTO products
           (category_id, name, description, price, sizes, stock,
            seller_username, seller_phone,
            card_file_id, card_media_type, gallery, is_active, short_id, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,1,$12,$13)
           RETURNING id''',
        (cid, name, desc, price, sizes_json, stock,
         seller_username, seller_phone,
         card_file_id, card_media_type, gallery_json,
         short_id, datetime.now().isoformat())
    )
    _cache_invalidate("products", "categories")
    return pid

async def update_product_field(pid: int, field: str, value):
    allowed = {'name','description','price','sizes','stock',
               'seller_username','seller_phone','card_file_id',
               'card_media_type','gallery'}
    if field not in allowed:
        return
    await db_run(f'UPDATE products SET {field}=$1 WHERE id=$2', (value, pid))
    _cache_invalidate(f"product:{pid}", "products")

async def del_product(pid: int):
    await db_run('UPDATE products SET is_active=0 WHERE id=$1', (pid,))
    _cache_invalidate("products", f"product:{pid}")

async def reduce_stock(pid: int):
    await db_run('UPDATE products SET stock=GREATEST(0, stock-1) WHERE id=$1', (pid,))
    _cache_invalidate(f"product:{pid}")

def parse_sizes(product) -> list:
    try:
        return json.loads(product['sizes'] or '[]')
    except Exception:
        return []

# ══════════════════════════════════════════════
#  Заказы
# ══════════════════════════════════════════════
async def create_order(uid, username, first_name, pid, size, price,
                       method, phone='', address='', promo_code='', discount=0):
    oid = await db_insert(
        '''INSERT INTO orders
           (user_id, username, first_name, product_id, size, price,
            method, phone, address, promo_code, discount, status, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'processing',$12)
           RETURNING id''',
        (uid, username or '', first_name or '', pid, size, price,
         method, phone, address, promo_code, discount,
         datetime.now().isoformat())
    )
    # Log initial status
    if oid:
        await db_run(
            'INSERT INTO order_history(order_id, status, changed_by, created_at) VALUES($1,$2,$3,$4)',
            (oid, 'processing', uid, datetime.now().isoformat())
        )
    return oid

async def get_order(oid: int):
    return await db_one('SELECT * FROM orders WHERE id=$1', (oid,))

async def set_order_status(oid: int, status: str, changed_by: int = 0):
    await db_run('UPDATE orders SET status=$1 WHERE id=$2', (status, oid))
    await db_run(
        'INSERT INTO order_history(order_id, status, changed_by, created_at) VALUES($1,$2,$3,$4)',
        (oid, status, changed_by, datetime.now().isoformat())
    )

async def get_user_orders(uid: int):
    return await db_all(
        '''SELECT o.*, p.name AS pname
           FROM orders o JOIN products p ON o.product_id=p.id
           WHERE o.user_id=$1 ORDER BY o.created_at DESC LIMIT 10''',
        (uid,)
    )

async def get_order_history(oid: int):
    return await db_all(
        'SELECT * FROM order_history WHERE order_id=$1 ORDER BY created_at ASC',
        (oid,)
    )

async def set_order_note(oid: int, note: str):
    await db_run(
        '''INSERT INTO order_notes(order_id, note, created_at) VALUES($1,$2,$3)
           ON CONFLICT(order_id) DO UPDATE SET note=$2''',
        (oid, note, datetime.now().isoformat())
    )

async def get_order_note(oid: int):
    r = await db_one('SELECT note FROM order_notes WHERE order_id=$1', (oid,))
    return r['note'] if r else ''

# ══════════════════════════════════════════════
#  Роли пользователей
# ══════════════════════════════════════════════
ROLES = {
    'buyer':   '🛒 Покупатель',
    'seller':  '🏪 Продавец',
    'owner':   '👑 Владелец',
    'manager': '🗂 Менеджер',
    'partner': '🤝 Партнёр',
    'support': '🎧 Поддержка',
}

async def get_user_role(uid: int) -> str:
    if uid in ADMIN_IDS:
        return 'owner'
    r = await db_one('SELECT role FROM user_roles WHERE user_id=$1', (uid,))
    return r['role'] if r else 'buyer'

async def set_user_role(uid: int, role: str, granted_by: int = 0):
    await db_run(
        '''INSERT INTO user_roles(user_id, role, granted_by, granted_at)
           VALUES($1,$2,$3,$4)
           ON CONFLICT(user_id) DO UPDATE SET role=$2, granted_by=$3, granted_at=$4''',
        (uid, role, granted_by, datetime.now().isoformat())
    )
    _cache_invalidate(f"user:{uid}")

async def get_users_by_role(role: str):
    return await db_all(
        '''SELECT u.*, ur.role FROM users u
           JOIN user_roles ur ON u.user_id=ur.user_id
           WHERE ur.role=$1 ORDER BY ur.granted_at DESC''',
        (role,)
    )

# ══════════════════════════════════════════════
#  Партнёрская программа
# ══════════════════════════════════════════════
async def get_partner(uid: int):
    return await db_one('SELECT * FROM partners WHERE user_id=$1', (uid,))

async def create_partner(uid: int, ref_code: str):
    existing = await db_one('SELECT user_id FROM partners WHERE ref_code=$1', (ref_code,))
    if existing:
        return False
    await db_run(
        '''INSERT INTO partners(user_id, ref_code, created_at) VALUES($1,$2,$3)
           ON CONFLICT(user_id) DO NOTHING''',
        (uid, ref_code.upper(), datetime.now().isoformat())
    )
    await set_user_role(uid, 'partner')
    return True

async def update_partner_bonuses(uid: int, bonus_new: dict, bonus_repeat: dict):
    await db_run(
        'UPDATE partners SET bonus_new=$1, bonus_repeat=$2 WHERE user_id=$3',
        (json.dumps(bonus_new), json.dumps(bonus_repeat), uid)
    )

async def get_partner_by_ref(ref_code: str):
    return await db_one('SELECT * FROM partners WHERE ref_code=$1', (ref_code.upper(),))

async def record_partner_referral(partner_id: int, referred_uid: int,
                                  is_new: bool, bonus: float, order_id: int = 0):
    await db_run(
        '''INSERT INTO partner_referrals(partner_id, referred_uid, is_new_buyer, bonus_amount, order_id, created_at)
           VALUES($1,$2,$3,$4,$5,$6)''',
        (partner_id, referred_uid, 1 if is_new else 0, bonus, order_id,
         datetime.now().isoformat())
    )
    await db_run(
        'UPDATE partners SET total_invited=total_invited+1, total_earned=total_earned+$1 WHERE user_id=$2',
        (bonus, partner_id)
    )
    await db_run(
        'UPDATE users SET bonus_balance=bonus_balance+$1 WHERE user_id=$2',
        (bonus, partner_id)
    )
    _cache_invalidate(f"user:{partner_id}")

async def get_partner_referrals(partner_id: int, limit=20):
    return await db_all(
        '''SELECT pr.*, u.username, u.first_name
           FROM partner_referrals pr LEFT JOIN users u ON pr.referred_uid=u.user_id
           WHERE pr.partner_id=$1 ORDER BY pr.created_at DESC LIMIT $2''',
        (partner_id, limit)
    )

def calc_partner_bonus(price: float, bonus_cfg: dict) -> float:
    btype = bonus_cfg.get('type', 'percent')
    val   = float(bonus_cfg.get('value', 0))
    if btype == 'percent':
        return round(price * val / 100, 0)
    elif btype == 'fixed':
        return round(val, 0)
    return 0.0

# ══════════════════════════════════════════════
#  Дропы
# ══════════════════════════════════════════════
async def get_active_drops():
    now = datetime.now().isoformat()
    return await db_all(
        "SELECT * FROM drops WHERE is_active=1 AND start_at <= $1 ORDER BY start_at DESC",
        (now,)
    )

async def get_upcoming_drops():
    now = datetime.now().isoformat()
    return await db_all(
        "SELECT * FROM drops WHERE is_active=1 AND start_at > $1 ORDER BY start_at ASC",
        (now,)
    )

async def get_all_drops_admin():
    return await db_all("SELECT * FROM drops ORDER BY created_at DESC")

async def add_drop(cid, name, desc, price, sizes_list, stock, start_at,
                   card_file_id='', card_media_type='', gallery=None):
    sizes_json   = json.dumps(sizes_list, ensure_ascii=False)
    gallery_json = json.dumps(gallery or [], ensure_ascii=False)
    return await db_insert(
        '''INSERT INTO drops
           (category_id, name, description, price, sizes, stock, start_at,
            card_file_id, card_media_type, gallery, is_active, created_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,1,$11) RETURNING id''',
        (cid, name, desc, price, sizes_json, stock, start_at,
         card_file_id, card_media_type, gallery_json,
         datetime.now().isoformat())
    )

async def del_drop(did: int):
    await db_run('UPDATE drops SET is_active=0 WHERE id=$1', (did,))

# ══════════════════════════════════════════════
#  Сообщения бота (редактируемые)
# ══════════════════════════════════════════════
# Дефолтные сообщения
BOT_MSG_DEFAULTS = {
    'welcome':        '👋 Добро пожаловать в {shop_name}!\n\nВыберите раздел ниже:',
    'catalog_header': '🛒 <b>Каталог</b>\n\n<blockquote>👇 Выберите категорию:</blockquote>',
    'profile_header': '👤 <b>Мой профиль</b>',
    'support_header': '❓ <b>Поддержка</b>\n\n<blockquote>По любым вопросам пишите нашему менеджеру.</blockquote>',
    'about_header':   '🏬 <b>О магазине</b>',
    'order_confirm':  '🎉 <b>Заказ #{order_id} оформлен!</b>\n\nОжидайте уведомлений о статусе.',
    'payment_wait':   '⏳ <b>Ожидаем подтверждения менеджера</b>\n\n<blockquote>Обычно это занимает несколько минут.</blockquote>',
    'drops_header':   '🔥 <b>Дропы</b>\n\n<blockquote>Скоро в продаже!</blockquote>',
    'partner_header': '🤝 <b>Партнёрская программа</b>\n\nПриглашай друзей и получай бонусы!',
}

async def get_bot_msg(key: str) -> str:
    r = await db_one('SELECT text FROM bot_messages WHERE key=$1', (key,))
    if r and r['text']:
        return r['text']
    return BOT_MSG_DEFAULTS.get(key, key)

async def set_bot_msg(key: str, text: str, media_type: str = '', file_id: str = ''):
    await db_run(
        '''INSERT INTO bot_messages(key, text, media_type, file_id) VALUES($1,$2,$3,$4)
           ON CONFLICT(key) DO UPDATE SET text=$2, media_type=$3, file_id=$4''',
        (key, text, media_type, file_id)
    )
    _cache_invalidate(f"botmsg:{key}")

async def get_bot_msg_media(key: str):
    return await db_one('SELECT * FROM bot_messages WHERE key=$1', (key,))

# Среднее значение рейтинга для товара
async def get_avg_rating(pid: int) -> float:
    r = await db_one('SELECT AVG(rating) AS avg FROM reviews WHERE product_id=$1', (pid,))
    if r and r['avg']:
        return round(float(r['avg']), 1)
    return 0.0

async def get_review_count(pid: int) -> int:
    r = await db_one('SELECT COUNT(*) AS cnt FROM reviews WHERE product_id=$1', (pid,))
    return r['cnt'] if r else 0

async def add_purchase(uid, pid, price, method='crypto'):
    await db_run(
        'INSERT INTO purchases(user_id,product_id,price,method,purchased_at) VALUES($1,$2,$3,$4,$5)',
        (uid, pid, price, method, datetime.now().isoformat())
    )
    await db_run(
        'UPDATE users SET total_purchases=total_purchases+1, total_spent=total_spent+$1 WHERE user_id=$2',
        (price, uid)
    )
    _cache_invalidate(f"user:{uid}")

# ══════════════════════════════════════════════
#  Жалобы
# ══════════════════════════════════════════════
async def create_complaint(uid: int, order_id: int, description: str):
    return await db_insert(
        "INSERT INTO complaints(user_id,order_id,description,created_at) VALUES($1,$2,$3,$4) RETURNING id",
        (uid, order_id, description, datetime.now().isoformat())
    )

# ══════════════════════════════════════════════
#  Отзывы
# ══════════════════════════════════════════════
async def add_review(uid, pid, oid, rating, comment):
    await db_run(
        'INSERT INTO reviews(user_id,product_id,order_id,rating,comment,created_at) VALUES($1,$2,$3,$4,$5,$6)',
        (uid, pid, oid, rating, comment, datetime.now().isoformat())
    )

async def get_reviews(pid, limit=10):
    return await db_all(
        'SELECT * FROM reviews WHERE product_id=$1 ORDER BY created_at DESC LIMIT $2',
        (pid, limit)
    )

# ══════════════════════════════════════════════
#  Промокоды
# ══════════════════════════════════════════════
PROMO_TYPES = {
    "discount_percent": "Скидка %",
    "discount_fixed":   "Скидка ₸",
    "gift":             "Подарок",
    "cashback_bonus":   "Бонус на счёт",
    "free_delivery":    "Бесплатная доставка",
    "special_offer":    "Спецпредложение",
}

async def get_all_promos(active_only=True):
    if active_only:
        return await db_all('SELECT * FROM promocodes WHERE is_active=1 ORDER BY created_at DESC')
    return await db_all('SELECT * FROM promocodes ORDER BY created_at DESC')

async def get_promo_by_code(code: str):
    return await db_one('SELECT * FROM promocodes WHERE code=$1 AND is_active=1', (code.upper(),))

async def get_promo_by_id(pid: int):
    return await db_one('SELECT * FROM promocodes WHERE id=$1', (pid,))

async def create_promo(code, promo_type, value, description, max_uses):
    return await db_insert(
        'INSERT INTO promocodes(code,promo_type,value,description,max_uses,created_at) VALUES($1,$2,$3,$4,$5,$6) RETURNING id',
        (code.upper(), promo_type, value, description, max_uses, datetime.now().isoformat())
    )

async def delete_promo(promo_id: int):
    await db_run('UPDATE promocodes SET is_active=0 WHERE id=$1', (promo_id,))

async def check_promo_usage(user_id: int, promo_id: int) -> bool:
    r = await db_one('SELECT id FROM promo_usage WHERE user_id=$1 AND promo_id=$2', (user_id, promo_id))
    return r is not None

async def use_promo(user_id: int, promo_id: int, order_id: int = 0):
    try:
        await db_run(
            'INSERT INTO promo_usage(user_id,promo_id,order_id,used_at) VALUES($1,$2,$3,$4)',
            (user_id, promo_id, order_id, datetime.now().isoformat())
        )
    except Exception:
        pass
    await db_run('UPDATE promocodes SET used_count=used_count+1 WHERE id=$1', (promo_id,))

def apply_promo_to_price(price: float, promo) -> tuple:
    if not promo:
        return price, 0, ""
    pt  = promo['promo_type']
    val = promo['value']
    if pt == "discount_percent":
        disc = round(price * val / 100, 0)
        return max(price - disc, 0), disc, f"Скидка {int(val)}%: -{fmt_price(disc)}"
    elif pt == "discount_fixed":
        disc = min(val, price)
        return max(price - disc, 0), disc, f"Скидка: -{fmt_price(disc)}"
    elif pt == "cashback_bonus":
        return price, 0, f"Бонус {fmt_price(val)} на счёт после покупки"
    elif pt == "gift":
        return price, 0, f"🎁 Подарок: {promo['description']}"
    elif pt == "free_delivery":
        return price, 0, "🚚 Бесплатная доставка"
    elif pt == "special_offer":
        return price, 0, f"✨ {promo['description']}"
    return price, 0, ""

async def validate_promo(code: str, user_id: int):
    promo = await get_promo_by_code(code)
    if not promo:
        return None, "❌ Промокод не найден или неактивен."
    if promo['max_uses'] > 0 and promo['used_count'] >= promo['max_uses']:
        return None, "❌ Промокод исчерпал лимит использований."
    used = await check_promo_usage(user_id, promo['id'])
    if used:
        return None, "❌ Вы уже использовали этот промокод."
    return promo, ""

# ══════════════════════════════════════════════
#  Реклама
# ══════════════════════════════════════════════
AD_PRICE_KZT: float = 500.0

async def create_ad_request(uid, description, method):
    return await db_insert(
        'INSERT INTO ad_requests(user_id,description,method,amount,created_at) VALUES($1,$2,$3,$4,$5) RETURNING id',
        (uid, description, method, AD_PRICE_KZT, datetime.now().isoformat())
    )

async def get_ad_request(aid):
    return await db_one('SELECT * FROM ad_requests WHERE id=$1', (aid,))

async def set_ad_status(aid, status):
    await db_run('UPDATE ad_requests SET status=$1 WHERE id=$2', (status, aid))

# ══════════════════════════════════════════════
#  Медиа / настройки
# ══════════════════════════════════════════════
async def set_media(key, mtype, fid):
    await db_run(
        'INSERT INTO media_settings(key,media_type,file_id) VALUES($1,$2,$3) ON CONFLICT(key) DO UPDATE SET media_type=$2, file_id=$3',
        (key, mtype, fid)
    )
    _cache_invalidate(f"media:{key}")

async def get_media(key):
    return await cached_db_one(f"media:{key}", 'SELECT * FROM media_settings WHERE key=$1', (key,))

async def set_setting(k, v):
    await db_run('INSERT INTO shop_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=$2', (k, v))
    _cache_invalidate(f"setting:{k}")

async def get_setting(k, default=''):
    r = await cached_db_one(f"setting:{k}", 'SELECT value FROM shop_settings WHERE key=$1', (k,))
    return r['value'] if r else default

async def get_stats():
    uc  = (await db_one('SELECT COUNT(*) AS c FROM users'))['c']
    pc  = (await db_one('SELECT COUNT(*) AS c FROM purchases'))['c']
    rv  = (await db_one('SELECT COALESCE(SUM(price),0) AS s FROM purchases'))['s']
    ac  = (await db_one('SELECT COUNT(*) AS c FROM products WHERE is_active=1'))['c']
    oc  = (await db_one("SELECT COUNT(*) AS c FROM orders WHERE status NOT IN ('delivered','confirmed')"))['c']
    prc = (await db_one('SELECT COUNT(*) AS c FROM promocodes WHERE is_active=1'))['c']
    bc  = (await db_one('SELECT COUNT(*) AS c FROM users WHERE is_banned=1'))['c']
    cmp = (await db_one("SELECT COUNT(*) AS c FROM complaints WHERE status='open'"))['c']
    return uc, pc, rv, ac, oc, prc, bc, cmp

# ══════════════════════════════════════════════
#  Крипто-платежи
# ══════════════════════════════════════════════
async def save_crypto(uid, pid, size, inv_id, amount_kzt, amount_usd,
                      promo_code='', discount=0):
    try:
        await db_run(
            '''INSERT INTO crypto_payments
               (user_id,product_id,size,invoice_id,amount_kzt,amount_usd,promo_code,discount,created_at)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)''',
            (uid, pid, size, inv_id, amount_kzt, amount_usd,
             promo_code, discount, datetime.now().isoformat())
        )
    except Exception:
        pass

async def get_crypto(inv_id):
    return await db_one('SELECT * FROM crypto_payments WHERE invoice_id=$1', (inv_id,))

async def set_crypto_paid(inv_id):
    await db_run("UPDATE crypto_payments SET status='paid' WHERE invoice_id=$1", (inv_id,))

# ══════════════════════════════════════════════
#  Kaspi-платежи
# ══════════════════════════════════════════════
async def save_kaspi(uid, pid, size, amount, promo_code='', discount=0, buyer_note=''):
    return await db_insert(
        'INSERT INTO kaspi_payments(user_id,product_id,size,amount,promo_code,discount,buyer_note,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id',
        (uid, pid, size, amount, promo_code, discount, buyer_note, datetime.now().isoformat())
    )

async def get_kaspi(kid):
    return await db_one('SELECT * FROM kaspi_payments WHERE id=$1', (kid,))

async def set_kaspi_status(kid, status, mgr_mid=None):
    if mgr_mid is not None:
        await db_run('UPDATE kaspi_payments SET status=$1, manager_msg_id=$2 WHERE id=$3', (status, mgr_mid, kid))
    else:
        await db_run('UPDATE kaspi_payments SET status=$1 WHERE id=$2', (status, kid))

# ══════════════════════════════════════════════
#  CryptoBot
# ══════════════════════════════════════════════
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

async def get_usd_kzt_rate() -> float:
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_ctx())) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json()
                return float(data["rates"]["KZT"])
    except Exception:
        return USD_KZT_RATE

def kzt_to_usd(kzt: float, rate: float) -> float:
    return round(kzt / rate, 2)

async def create_invoice(amount_usd: float, desc: str, payload: str):
    url = "https://pay.crypt.bot/api/createInvoice"
    hdr = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    me = await bot.get_me()
    data = {
        "asset": "USDT", "amount": str(amount_usd),
        "description": desc, "payload": payload,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{me.username}",
    }
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_ctx())) as s:
        async with s.post(url, headers=hdr, json=data) as r:
            res = await r.json()
            return res["result"] if res.get("ok") else None

async def check_invoice(inv_id: str):
    url = "https://pay.crypt.bot/api/getInvoices"
    hdr = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_ctx())) as s:
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
        [KeyboardButton(text="🛒 Купить"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏬 О магазине"), KeyboardButton(text="❓ Поддержка")],
    ], resize_keyboard=True)

def kb_back(cd="main"):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Назад", callback_data=cd)
    ]])

def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="🖼 Медиа", callback_data="adm_media"),
         InlineKeyboardButton(text="📨 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="📦 Товары", callback_data="adm_products"),
         InlineKeyboardButton(text="📁 Категории", callback_data="adm_cats")],
        [InlineKeyboardButton(text="📋 Заказы", callback_data="adm_orders")],
        [InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm_promos")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm_users")],
        [InlineKeyboardButton(text="🤝 Партнёры", callback_data="adm_partners")],
        [InlineKeyboardButton(text="🔥 Дропы", callback_data="adm_drops")],
        [InlineKeyboardButton(text="💬 Сообщения бота", callback_data="adm_botmsgs")],
        [InlineKeyboardButton(text="📊 Лог (HTML)", callback_data="adm_log")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm_settings")],
    ])

def kb_admin_back():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Админ панель", callback_data="adm_panel")
    ]])

# ══════════════════════════════════════════════
#  Хелперы
# ══════════════════════════════════════════════
async def send_media(chat_id: int, text: str, key: str, markup=None):
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
            await db_run('DELETE FROM media_settings WHERE key=$1', (key,))
            _cache_invalidate(f"media:{key}")
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

async def set_cmds(uid: int):
    cmds = [BotCommand(command="start", description="🚀 Старт")]
    if uid in ADMIN_IDS:
        cmds.append(BotCommand(command="admin", description="🎩 Панель"))
    await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))

def admin_guard(uid: int) -> bool:
    return uid in ADMIN_IDS

async def ban_check(uid: int, answer_fn) -> bool:
    """Returns True if user is banned (and sends a message). Use to guard handlers."""
    if await is_banned(uid):
        try:
            await answer_fn("🚫 Вы заблокированы в этом боте.")
        except Exception:
            pass
        return True
    return False

# ══════════════════════════════════════════════
#  /start  /admin
# ══════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    await state.clear()
    await ensure_user(msg.from_user)
    await set_cmds(msg.from_user.id)

    if await is_banned(msg.from_user.id):
        await msg.answer("🚫 Вы заблокированы в этом боте.")
        return

    args = msg.text.split(maxsplit=1)
    ref_arg = args[1].strip() if len(args) > 1 else ''

    if ref_arg.lower() == "support":
        await _show_support(msg.chat.id)
        return

    # Обработка реф-ссылки
    if ref_arg.startswith("ref_"):
        ref_code = ref_arg[4:].upper()
        partner = await get_partner_by_ref(ref_code)
        if partner and partner['user_id'] != msg.from_user.id:
            # Сохраняем реф в данных пользователя
            await db_run('UPDATE users SET ref_code=$1 WHERE user_id=$2',
                         (ref_code, msg.from_user.id))
            _cache_invalidate(f"user:{msg.from_user.id}")

    if not await has_agreed_terms(msg.from_user.id):
        await _show_agreement(msg.chat.id)
        return

    await log_event("start", msg.from_user.id)
    welcome_text = await get_bot_msg('welcome')
    text = welcome_text.replace('{shop_name}', SHOP_NAME)
    full_text = f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n<blockquote>{text}</blockquote>"
    await send_media(msg.chat.id, full_text, "main_menu", kb_main())

@router.message(Command("admin"))
async def cmd_admin(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await send_media(msg.chat.id, "🎩 <b>Панель управления</b>", "admin_panel", kb_admin())

@router.callback_query(F.data == "main")
async def cb_main(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    text = (f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n"
            f"<blockquote>{ae('down')} Выберите нужный раздел:</blockquote>")
    await bot.send_message(cb.from_user.id, text, parse_mode="HTML", reply_markup=kb_main())
    await cb.answer()

# ══════════════════════════════════════════════
#  Соглашение
# ══════════════════════════════════════════════
async def _show_agreement(chat_id: int):
    text = (
        f"👋 <b>Добро пожаловать в {SHOP_NAME}!</b>\n\n"
        f"<blockquote>Перед тем как начать, ознакомьтесь с документами и подтвердите согласие:\n\n"
        f"📄 <b>Публичная оферта</b>\n"
        f"📋 <b>Политика конфиденциальности</b>\n"
        f"📝 <b>Пользовательское соглашение</b>\n\n"
        f"Нажимая <b>«Принять и продолжить»</b>, вы подтверждаете согласие.</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Публичная оферта", url="https://teletype.in/@aloneabove/R6n3kZPT77z")],
        [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url="https://teletype.in/@aloneabove/cC0sM1BcefC")],
        [InlineKeyboardButton(text="📝 Пользовательское соглашение", url="https://teletype.in/@aloneabove/L8aD4zXVy6W")],
        [InlineKeyboardButton(text="✅ Принять и продолжить", callback_data="agree_terms")],
    ])
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "agree_terms")
async def cb_agree_terms(cb: types.CallbackQuery):
    await ensure_user(cb.from_user)
    await set_agreed_terms(cb.from_user.id)
    await set_cmds(cb.from_user.id)
    try:
        await cb.message.delete()
    except Exception:
        pass
    text = (f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n"
            f"<blockquote>✅ Спасибо! Вы приняли условия.\n\n{ae('down')} Выберите раздел:</blockquote>")
    await send_media(cb.from_user.id, text, "main_menu", kb_main())
    await cb.answer("✅ Добро пожаловать!")

# ══════════════════════════════════════════════
#  Поддержка
# ══════════════════════════════════════════════
async def _show_support(chat_id: int):
    text = (f"{ae('support')} <b>Поддержка</b>\n\n"
            f"<blockquote>По любым вопросам пишите нашему менеджеру или в службу поддержки.</blockquote>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать в поддержку",
                              url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="support_contacts")],
        [InlineKeyboardButton(text="⚠️ Пожаловаться на товар", callback_data="complaint_start")],
        [InlineKeyboardButton(text="📄 Публичная оферта", url="https://teletype.in/@aloneabove/R6n3kZPT77z")],
        [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url="https://teletype.in/@aloneabove/cC0sM1BcefC")],
        [InlineKeyboardButton(text="📝 Пользовательское соглашение", url="https://teletype.in/@aloneabove/L8aD4zXVy6W")],
        [InlineKeyboardButton(text="🌐 Наш сайт / магазин", url="https://t.me/alone_above_bot/shop")],
    ])
    await send_media(chat_id, text, "support_menu", kb)

@router.callback_query(F.data == "support_contacts")
async def cb_support_contacts(cb: types.CallbackQuery):
    text = (
        f"📞 <b>Контакты</b>\n\n<blockquote>"
        f"📱 <b>Номер:</b> <a href='tel:+77078115621'>+7 707 811 5621</a>\n"
        f"🌍 <b>Страна:</b> Казахстан\n\n"
        f"🛍 <b>Telegram Магазина:</b> @aloneaboveshop\n"
        f"👤 <b>Telegram Владельца:</b> @AloneAbove\n"
        f"🤝 <b>Telegram Менеджера:</b> @AloneAboveManager\n"
        f"❓ <b>Telegram Поддержки:</b> @AloneAboveSupport\n\n"
        f"👑 <b>Владелец:</b> Кахраман Айбек\n"
        f"📧 <b>Email:</b> Alone.Above.0000@gmail.com\n"
        f"🌐 <b>Сайт:</b> <a href='https://t.me/alone_above_bot/shop'>t.me/alone_above_bot/shop</a>"
        f"</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать менеджеру", url="https://t.me/AloneAboveManager")],
        [InlineKeyboardButton(text="🆘 Написать в поддержку", url="https://t.me/AloneAboveSupport")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="support_back")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb,
                                   disable_web_page_preview=True)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb,
                                disable_web_page_preview=True)
    await cb.answer()

@router.callback_query(F.data == "support_back")
async def cb_support_back(cb: types.CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass
    await _show_support(cb.from_user.id)
    await cb.answer()

# ══════════════════════════════════════════════
#  ЖАЛОБЫ НА ТОВАР
# ══════════════════════════════════════════════
async def _complaint_ask_desc(target, oid: int, state: FSMContext, back_cd="support_back"):
    """Общая функция: перевести в состояние описания жалобы."""
    await state.update_data(complaint_oid=oid)
    await state.set_state(ComplaintSt.description)
    text = (
        "⚠️ <b>Жалоба на товар</b>\n\n"
        "<blockquote>Опишите проблему подробно:\n\n"
        "• Что именно не так?\n"
        "• Когда заметили проблему?\n\n"
        "Ваше сообщение поможет нам решить ситуацию быстрее!</blockquote>"
    )
    kb = kb_back(back_cd)
    if hasattr(target, 'edit_text'):
        try:
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
        await target.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "complaint_start")
async def cb_complaint_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ComplaintSt.order_id)
    try:
        await cb.message.edit_text(
            "⚠️ <b>Жалоба на товар</b>\n\n"
            "<blockquote>Шаг 1/2 — Укажите номер вашего заказа:\n"
            "<i>Например: 42</i>\n\n"
            "Если не помните номер — напишите <b>0</b></blockquote>",
            parse_mode="HTML",
            reply_markup=kb_back("support_back")
        )
    except Exception:
        await cb.message.answer("⚠️ <b>Жалоба</b>", parse_mode="HTML",
                                reply_markup=kb_back("support_back"))
    await cb.answer()

@router.callback_query(F.data.startswith("complaint_order_"))
async def cb_complaint_from_order(cb: types.CallbackQuery, state: FSMContext):
    """Жалоба прямо из карточки заказа — номер уже известен."""
    oid = int(cb.data.split("_")[2])
    await _complaint_ask_desc(cb.message, oid, state, back_cd=f"myorder_{oid}")
    await cb.answer()

@router.message(ComplaintSt.order_id)
async def proc_complaint_order(msg: types.Message, state: FSMContext):
    try:
        oid = int(msg.text.strip())
    except ValueError:
        oid = 0
    await _complaint_ask_desc(msg.answer, oid, state, back_cd="support_back")

@router.message(ComplaintSt.description)
async def proc_complaint_desc(msg: types.Message, state: FSMContext):
    d    = await state.get_data()
    oid  = d.get('complaint_oid', 0)
    desc = msg.text.strip() if msg.text else (msg.caption or "[медиа без текста]")

    # Сохраняем описание, переходим к шагу прикрепления файла
    await state.update_data(complaint_desc=desc)
    await state.set_state(ComplaintSt.file_attach)
    await msg.answer(
        "📎 <b>Прикрепите файл (необязательно)</b>\n\n"
        "<blockquote>Отправьте фото, видео, документ или ZIP-архив.\n\n"
        "Или нажмите <b>«Пропустить»</b> если файл не нужен.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏭ Пропустить", callback_data="complaint_skip_file")
        ]])
    )

@router.callback_query(F.data == "complaint_skip_file", ComplaintSt.file_attach)
async def cb_complaint_skip_file(cb: types.CallbackQuery, state: FSMContext):
    await _finish_complaint(cb.from_user, state, file_id=None, file_type=None,
                            send_fn=cb.message.answer)
    await cb.answer()

@router.message(ComplaintSt.file_attach,
                F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO,
                                    ContentType.DOCUMENT, ContentType.ANIMATION]))
async def proc_complaint_file(msg: types.Message, state: FSMContext):
    if msg.photo:
        file_id, file_type = msg.photo[-1].file_id, 'photo'
    elif msg.video:
        file_id, file_type = msg.video.file_id, 'video'
    elif msg.document:
        file_id, file_type = msg.document.file_id, 'document'
    elif msg.animation:
        file_id, file_type = msg.animation.file_id, 'animation'
    else:
        file_id, file_type = None, None
    await _finish_complaint(msg.from_user, state, file_id=file_id, file_type=file_type,
                            send_fn=msg.answer)

async def _finish_complaint(tg_user: types.User, state: FSMContext,
                             file_id, file_type, send_fn):
    d    = await state.get_data()
    oid  = d.get('complaint_oid', 0)
    desc = d.get('complaint_desc', '—')
    await state.clear()

    user = await get_user(tg_user.id)
    cid  = await create_complaint(tg_user.id, oid, desc)

    # Сохраняем файл если есть
    if file_id and file_type:
        await db_run('UPDATE complaints SET file_id=$1, file_type=$2 WHERE id=$3',
                     (file_id, file_type, cid))

    uname = f"@{tg_user.username}" if tg_user.username else "—"
    fname = tg_user.first_name or "—"

    order_info = ""
    if oid > 0:
        order = await get_order(oid)
        if order:
            product = await get_product(order['product_id'])
            pname   = product['name'] if product else '—'
            order_info = (
                f"\n🛍 <b>Заказ #{oid}:</b> {pname} ({order['size']})\n"
                f"💰 {fmt_price(order['price'])} | {order_status_text(order['status'])}"
            )

    file_mark = f"\n📎 <b>Файл:</b> прикреплён ({file_type})" if file_id else ""
    notify_text = (
        f"⚠️ <b>ЖАЛОБА #{cid} НА ТОВАР</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Покупатель:</b> {uname} ({fname})\n"
        f"🆔 <b>TG ID:</b> <code>{tg_user.id}</code>\n"
        f"📱 <b>Телефон:</b> {user['phone'] if user else '—'}"
        f"{order_info}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Суть жалобы:</b>\n"
        f"<blockquote>{desc[:800]}</blockquote>"
        f"{file_mark}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📞 <b>Связаться:</b> tg://user?id={tg_user.id}\n"
        f"{ae('cal')} {fmt_dt()}"
    )

    kb_notify = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Написать покупателю",
                              url=f"tg://user?id={tg_user.id}")
    ]])

    for notify_id in set([*ADMIN_IDS, MANAGER_ID]):
        try:
            if file_id and file_type == 'photo':
                await bot.send_photo(notify_id, file_id, caption=notify_text,
                                     parse_mode="HTML", reply_markup=kb_notify)
            elif file_id and file_type == 'video':
                await bot.send_video(notify_id, file_id, caption=notify_text,
                                     parse_mode="HTML", reply_markup=kb_notify)
            elif file_id and file_type in ('document', 'animation'):
                await bot.send_document(notify_id, file_id, caption=notify_text,
                                        parse_mode="HTML", reply_markup=kb_notify)
            else:
                await bot.send_message(notify_id, notify_text,
                                       parse_mode="HTML", reply_markup=kb_notify)
        except Exception:
            pass

    await send_fn(
        f"✅ <b>Жалоба #{cid} принята!</b>\n\n"
        f"<blockquote>Мы получили ваше обращение и рассмотрим его в ближайшее время.\n\n"
        f"Обычно ответ поступает в течение 24 часов.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Написать продавцу",
                                  url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")],
            [InlineKeyboardButton(text="‹ В главное меню", callback_data="main")],
        ])
    )


# ══════════════════════════════════════════════
#  Reply-кнопки
# ══════════════════════════════════════════════
@router.message(F.text == "🛒 Купить")
async def txt_shop(msg: types.Message):
    if await ban_check(msg.from_user.id, msg.answer): return
    await show_catalog(msg.chat.id)

@router.message(F.text == "👤 Профиль")
async def txt_profile(msg: types.Message):
    if await ban_check(msg.from_user.id, msg.answer): return
    await ensure_user(msg.from_user)
    user = await get_user(msg.from_user.id)
    await _send_profile(msg.from_user, user, send_fn=msg.answer)

@router.message(F.text == "🏬 О магазине")
async def txt_about(msg: types.Message):
    if await ban_check(msg.from_user.id, msg.answer): return
    info = await get_setting("shop_info", "Информация о магазине пока не заполнена.")
    text = (f"{ae('store')} <b>О магазине</b>\n\n<blockquote>{info}</blockquote>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🤝 Партнёрство", callback_data="partnership")
    ]])
    await send_media(msg.chat.id, text, "about_menu", kb)

@router.callback_query(F.data == "partnership")
async def cb_partnership(cb: types.CallbackQuery):
    text = (
        f"{ae('store')} <b>Партнёрство с нами</b>\n\n<blockquote>"
        f"Мы открыты для взаимовыгодного сотрудничества!\n\n"
        f"🤝 <b>Что мы предлагаем:</b>\n"
        f"• Размещение вашего товара в нашем каталоге\n"
        f"• Рекламные интеграции в боте\n"
        f"• Совместные акции и распродажи\n"
        f"• Кросс-промо между магазинами\n\n"
        f"📈 <b>Почему мы?</b>\n"
        f"• Активная аудитория покупателей Шымкента\n"
        f"• Прозрачные условия сотрудничества\n"
        f"• Быстрая обратная связь\n\n"
        f"Если интересует сотрудничество — напишите нашему менеджеру!</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Связаться", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton(text="📢 Разместить рекламу", callback_data="ad_warning")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="about_back")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "about_back")
async def cb_about_back(cb: types.CallbackQuery):
    info = await get_setting("shop_info", "Информация о магазине пока не заполнена.")
    text = (f"{ae('store')} <b>О магазине</b>\n\n<blockquote>{info}</blockquote>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🤝 Партнёрство", callback_data="partnership")
    ]])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.message(F.text == "❓ Поддержка")
async def txt_support(msg: types.Message):
    if await ban_check(msg.from_user.id, msg.answer): return
    await _show_support(msg.chat.id)

# ══════════════════════════════════════════════
#  Профиль
# ══════════════════════════════════════════════
def _profile_text(tg_user: types.User, user, role: str = 'buyer') -> str:
    phone   = user['phone'] if user['phone'] else '— не указан'
    address = user['default_address'] if user['default_address'] else '— не указан'
    uname   = f"@{tg_user.username}" if tg_user.username else "— не указан"
    role_label = ROLES.get(role, role)
    return (
        f"{ae('user')} <b>Профиль</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{tg_user.id}</code>\n"
        f"{ae('user')} <b>Имя:</b> {tg_user.first_name or '—'}\n"
        f"💬 <b>Username:</b> {uname}\n"
        f"👑 <b>Роль:</b> {role_label}\n\n"
        f"{ae('phone')} <b>Телефон:</b> <code>{phone}</code>\n"
        f"{ae('pin')} <b>Адрес доставки:</b>\n  <i>{address}</i>\n\n"
        f"{ae('cart')} <b>Заказов:</b> {user['total_purchases']}\n"
        f"{ae('money')} <b>Потрачено:</b> {fmt_price(user['total_spent'])}\n"
        f"{ae('gift')} <b>Бонусный баланс:</b> {fmt_price(user['bonus_balance'])}\n"
        f"{ae('cal')} <b>Регистрация:</b> {user['registered_at'][:10]}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

def _profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Телефон", callback_data="profile_phone"),
         InlineKeyboardButton(text="📍 Адрес", callback_data="profile_address")],
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton(text="🤝 Партнёрская программа", callback_data="partner_program")],
    ])

async def _send_profile(tg_user: types.User, user, send_fn=None, edit_msg=None):
    if user is None:
        if send_fn:
            await send_fn("⏳ Профиль создаётся, попробуйте снова.", parse_mode="HTML")
        return
    role = await get_user_role(tg_user.id)
    text = _profile_text(tg_user, user, role)
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
        await send_media(tg_user.id, text, "profile_menu", kb)

@router.callback_query(F.data == "profile_view")
async def cb_profile_view(cb: types.CallbackQuery):
    await ensure_user(cb.from_user)
    user = await get_user(cb.from_user.id)
    await _send_profile(cb.from_user, user, edit_msg=cb.message)
    await cb.answer()

@router.callback_query(F.data == "profile_phone")
async def cb_profile_phone(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Поделиться через Telegram", callback_data="phone_via_tg")],
        [InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data="phone_manual")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="profile_view")],
    ])
    try:
        await cb.message.edit_text("📞 <b>Укажите номер телефона</b>\n\n"
                                   "<blockquote>Выберите удобный способ:</blockquote>",
                                   parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer("📞 <b>Укажите номер телефона</b>",
                                parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "phone_via_tg")
async def cb_phone_via_tg(cb: types.CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await bot.send_message(cb.from_user.id,
                           "📲 Нажмите кнопку ниже, чтобы поделиться вашим номером:", reply_markup=kb)
    await cb.answer()

@router.message(F.contact)
async def handle_contact(msg: types.Message):
    if msg.contact.user_id != msg.from_user.id:
        await msg.answer("❌ Это чужой контакт.", reply_markup=kb_main())
        return
    phone = msg.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await update_user_phone(msg.from_user.id, phone)
    await msg.answer(f"✅ <b>Телефон сохранён:</b> <code>{phone}</code>\n\nТеперь вы можете делать заказы.",
                     parse_mode="HTML", reply_markup=kb_main())

@router.callback_query(F.data == "phone_manual")
async def cb_phone_manual(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileSt.phone)
    try:
        await cb.message.edit_text("📞 <b>Введите номер телефона вручную</b>\n"
                                   "<i>Пример: +7 701 234 56 78</i>",
                                   parse_mode="HTML", reply_markup=kb_back("profile_view"))
    except Exception:
        await cb.message.answer("📞 <b>Введите номер телефона вручную</b>",
                                parse_mode="HTML", reply_markup=kb_back("profile_view"))
    await cb.answer()

@router.message(ProfileSt.phone)
async def proc_profile_phone(msg: types.Message, state: FSMContext):
    phone = msg.text.strip()
    await update_user_phone(msg.from_user.id, phone)
    await state.clear()
    await msg.answer(f"✅ <b>Телефон сохранён:</b> <code>{phone}</code>",
                     parse_mode="HTML", reply_markup=kb_main())

@router.callback_query(F.data == "profile_address")
async def cb_profile_address(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileSt.address)
    try:
        await cb.message.edit_text("📍 <b>Введите адрес доставки по умолчанию</b>\n"
                                   "<i>Пример: мкр Нурсат, ул. Байтурсынова 12, кв. 5</i>",
                                   parse_mode="HTML", reply_markup=kb_back("profile_view"))
    except Exception:
        await cb.message.answer("📍 <b>Введите адрес доставки</b>",
                                parse_mode="HTML", reply_markup=kb_back("profile_view"))
    await cb.answer()

@router.message(ProfileSt.address)
async def proc_profile_address(msg: types.Message, state: FSMContext):
    address = msg.text.strip()
    await update_user_address(msg.from_user.id, address)
    await state.clear()
    await msg.answer(f"✅ <b>Адрес сохранён:</b>\n<i>{address}</i>",
                     parse_mode="HTML", reply_markup=kb_main())

@router.callback_query(F.data == "my_orders")
async def cb_my_orders(cb: types.CallbackQuery):
    orders = await get_user_orders(cb.from_user.id)
    if not orders:
        await cb.answer("Заказов пока нет", show_alert=True)
        return
    kb_rows = []
    for o in orders:
        status_icon = {
            'processing': '🔄', 'china': '✈️', 'arrived': '📦',
            'delivered': '🚚', 'confirmed': '✅'
        }.get(o['status'], '❓')
        label = f"{status_icon} #{o['id']} {o['pname'][:15]} ({o['size']}) — {o['created_at'][:10]}"
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"myorder_{o['id']}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="profile_view")])
    text = f"{ae('archive')} <b>Мои заказы</b>\n\n<blockquote>Нажмите на заказ для подробностей:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("myorder_"))
async def cb_myorder_detail(cb: types.CallbackQuery):
    oid   = int(cb.data.split("_")[1])
    order = await get_order(oid)
    if not order or order['user_id'] != cb.from_user.id:
        await cb.answer("Заказ не найден", show_alert=True)
        return
    product = await get_product(order['product_id'])
    history = await get_order_history(oid)
    note    = await get_order_note(oid)

    promo_line = ""
    if order.get('promo_code'):
        promo_line = f"🎟 <b>Промокод:</b> <code>{order['promo_code']}</code>\n"

    note_line = f"\n📝 <b>Ваше примечание:</b>\n<i>{note}</i>\n" if note else ""

    history_lines = "\n📋 <b>История статусов:</b>\n"
    status_labels = {
        'processing': '🔄 В обработке',
        'china':      '✈️ Едет из Китая',
        'arrived':    '📦 Прибыло',
        'delivered':  '🚚 Передано',
        'confirmed':  '✅ Подтверждено покупателем',
    }
    if history:
        for h in history:
            dt = h['created_at'][:16] if h.get('created_at') else ''
            st = status_labels.get(h['status'], h['status'])
            history_lines += f"  • {st} — <i>{dt}</i>\n"
    else:
        history_lines += "  <i>История пуста</i>\n"

    text = (
        f"📋 <b>Заказ #{oid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('box')} <b>Товар:</b> {product['name'] if product else '—'}\n"
        f"{ae('size')} <b>Размер:</b> {order['size']}\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(order['price'])}\n"
        f"{promo_line}"
        f"💳 <b>Оплата:</b> {order['method']}\n"
        f"{ae('phone')} <b>Телефон:</b> {order['phone'] or '—'}\n"
        f"{ae('pin')} <b>Адрес:</b> {order['address'] or '—'}\n"
        f"🔄 <b>Статус:</b> {order_status_text(order['status'])}\n"
        f"{ae('cal')} <b>Оформлен:</b> {order['created_at'][:16]}\n"
        f"{note_line}"
        f"━━━━━━━━━━━━━━━━━"
        f"{history_lines}"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Пожаловаться", callback_data=f"complaint_order_{oid}")],
        [InlineKeyboardButton(text="‹ К заказам", callback_data="my_orders")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ══════════════════════════════════════════════
#  Каталог
# ══════════════════════════════════════════════
async def show_catalog(chat_id: int):
    cats = await get_categories(parent_id=0)
    drops = await get_active_drops()
    upcoming = await get_upcoming_drops()
    kb_rows = []
    for c in cats:
        # Check if has subcategories
        subcats = await get_categories(parent_id=c['id'])
        icon = "📂" if subcats else "🗂"
        kb_rows.append([InlineKeyboardButton(text=f"{icon} {c['name']}", callback_data=f"cat_{c['id']}")])
    # Дропы видны только если есть активные или предстоящие
    if drops or upcoming:
        kb_rows.append([InlineKeyboardButton(text="🔥 Дропы", callback_data="drops_menu")])
    if not kb_rows:
        await bot.send_message(chat_id, "📭 Категории пока не добавлены.")
        return
    cat_text = await get_bot_msg('catalog_header')
    await send_media(chat_id, cat_text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.callback_query(F.data == "shop")
async def cb_shop(cb: types.CallbackQuery):
    cats = await get_categories(parent_id=0)
    drops = await get_active_drops()
    upcoming = await get_upcoming_drops()
    kb_rows = []
    for c in cats:
        subcats = await get_categories(parent_id=c['id'])
        icon = "📂" if subcats else "🗂"
        kb_rows.append([InlineKeyboardButton(text=f"{icon} {c['name']}", callback_data=f"cat_{c['id']}")])
    if drops or upcoming:
        kb_rows.append([InlineKeyboardButton(text="🔥 Дропы", callback_data="drops_menu")])
    if not kb_rows:
        await cb.answer("Категории пока не добавлены", show_alert=True)
        return
    cat_text = await get_bot_msg('catalog_header')
    try:
        await cb.message.edit_text(cat_text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(cat_text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("cat_"))
async def cb_cat(cb: types.CallbackQuery):
    cid = int(cb.data.split("_")[1])
    cat = await get_category(cid)
    # Check subcategories first
    subcats = await get_categories(parent_id=cid)
    if subcats:
        kb_rows = []
        for sc in subcats:
            kb_rows.append([InlineKeyboardButton(text=f"🗂 {sc['name']}", callback_data=f"cat_{sc['id']}")])
        # Also show products directly in this category
        prods = await get_products(cid)
        for p in prods:
            icon = "✅" if p['stock'] > 0 else "❌"
            kb_rows.append([InlineKeyboardButton(
                text=f"{icon} {p['name']} · {fmt_price(p['price'])}",
                callback_data=f"prod_{p['id']}"
            )])
        kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="shop")])
        cat_name = cat['name'] if cat else 'Категория'
        text = f"📂 <b>{cat_name}</b>\n\n<blockquote>👇 Выберите подкатегорию или товар:</blockquote>"
        try:
            await cb.message.edit_text(text, parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        except Exception:
            await cb.message.answer(text, parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        await cb.answer()
        return

    prods = await get_products(cid)
    if not prods:
        await cb.answer("В этой категории пока нет товаров", show_alert=True)
        return
    kb_rows = []
    for p in prods:
        icon = "✅" if p['stock'] > 0 else "❌"
        sid  = f" #{p.get('short_id','')}" if p.get('short_id') else ""
        kb_rows.append([InlineKeyboardButton(
            text=f"{icon} {p['name']}{sid} · {fmt_price(p['price'])}",
            callback_data=f"prod_{p['id']}"
        )])
    # Back: if subcategory → go to parent, else → shop
    parent_id = cat['parent_id'] if cat else 0
    back_cd = f"cat_{parent_id}" if parent_id else "shop"
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=back_cd)])
    kb_rows.append([InlineKeyboardButton(text="📢 Подключить рекламу", callback_data="ad_warning")])
    text = f"<blockquote>{ae('down')} Выберите товар:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

# ══════════════════════════════════════════════
#  Карточка товара — ИСПРАВЛЕН БАГ с фото
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
    stock_s = (f"✅ В наличии ({stock} шт.)" if stock > 0 else "❌ Нет в наличии")

    # Средний рейтинг
    avg_rating  = await get_avg_rating(pid)
    rev_count   = await get_review_count(pid)
    stars_full  = int(avg_rating)
    stars_empty = 5 - stars_full
    rating_s = ""
    if rev_count > 0:
        rating_s = "⭐" * stars_full + "☆" * stars_empty + f"  {avg_rating}/5 ({rev_count} отзывов)"
    else:
        rating_s = "☆☆☆☆☆  Нет отзывов"

    short_id_s = f"  <code>#{p.get('short_id','')}</code>" if p.get('short_id') else ""

    seller_block = ""
    if p['seller_phone'] or p['seller_username']:
        seller_block = "━━━━━━━━━━━━━━━━━\n"
        if p['seller_phone']:
            seller_block += f"{ae('phone')} <b>Продавец:</b> <code>{p['seller_phone']}</code>\n"
        if p['seller_username']:
            un = p['seller_username'].lstrip('@')
            seller_block += f"💬 <b>Telegram:</b> @{un}\n"

    text = (
        f"╔═══════════════════╗\n"
        f"║ {ae('tag')} <b>{p['name']}</b>{short_id_s}\n"
        f"╚═══════════════════╝\n\n"
        f"<blockquote>{p['description']}</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('star')} {rating_s}\n"
        f"{ae('money')} <b>Цена:</b>  <code>{fmt_price(p['price'])}</code>\n"
        f"{ae('size')} <b>Размеры:</b>  {sizes_s}\n"
        f"{ae('box')} <b>Статус:</b>  {stock_s}\n"
        f"{seller_block}"
        f"━━━━━━━━━━━━━━━━━"
    )

    try:
        gallery = json.loads(p['gallery'] or '[]')
    except Exception:
        gallery = []

    kb_rows = []
    if stock > 0:
        kb_rows.append([InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy_{pid}")])
    if gallery:
        kb_rows.append([InlineKeyboardButton(
            text=f"🖼 Галерея ({len(gallery)})", callback_data=f"gallery_{pid}_0"
        )])
    kb_rows.append([InlineKeyboardButton(text="⭐ Отзывы", callback_data=f"reviews_{pid}")])
    # Back button: go to category
    cat = await get_category(p['category_id']) if p.get('category_id') else None
    back_cd = f"cat_{p['category_id']}" if p.get('category_id') else "shop"
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=back_cd)])

    markup   = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    card_fid = p.get('card_file_id', '')
    card_mt  = p.get('card_media_type', '')

    try:
        await cb.message.delete()
    except Exception:
        pass

    if card_fid and card_mt:
        try:
            if card_mt == 'photo':
                await bot.send_photo(cb.from_user.id, card_fid,
                                     caption=text, parse_mode="HTML", reply_markup=markup)
            elif card_mt == 'video':
                await bot.send_video(cb.from_user.id, card_fid,
                                     caption=text, parse_mode="HTML", reply_markup=markup)
            await cb.answer()
            return
        except Exception:
            pass
    await send_media(cb.from_user.id, text, f"product_{pid}", markup)
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
        text += f"<b>{stars}</b> <i>{dt}</i>\n{rv['comment']}\n\n"
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
#  Галерея товара — ИСПРАВЛЕН БАГ с фото
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("gallery_"))
async def cb_gallery(cb: types.CallbackQuery):
    parts = cb.data.split("_")
    pid   = int(parts[1])
    idx   = int(parts[2])
    p     = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    try:
        gallery = json.loads(p['gallery'] or '[]')
    except Exception:
        gallery = []
    if not gallery:
        await cb.answer("Галерея пуста", show_alert=True)
        return

    idx   = max(0, min(idx, len(gallery) - 1))
    item  = gallery[idx]
    fid   = item['file_id']
    mt    = item['media_type']
    total = len(gallery)

    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"gallery_{pid}_{idx-1}"))
    nav.append(InlineKeyboardButton(text=f"{idx+1}/{total}", callback_data="noop"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"gallery_{pid}_{idx+1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text="‹ К товару", callback_data=f"prod_{pid}")]
    ])
    caption = f"🖼 <b>Галерея</b>  {idx+1}/{total}  —  {p['name']}"

    # ИСПРАВЛЕНИЕ: удаляем и отправляем заново, чтобы медиа не пропадало
    try:
        await cb.message.delete()
    except Exception:
        pass
    try:
        if mt == 'photo':
            await bot.send_photo(cb.from_user.id, fid, caption=caption,
                                 parse_mode="HTML", reply_markup=kb)
        elif mt == 'video':
            await bot.send_video(cb.from_user.id, fid, caption=caption,
                                 parse_mode="HTML", reply_markup=kb)
        else:
            await bot.send_document(cb.from_user.id, fid, caption=caption,
                                    parse_mode="HTML", reply_markup=kb)
    except Exception:
        await bot.send_message(cb.from_user.id, "⚠️ Не удалось загрузить медиа галереи.", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "noop")
async def cb_noop(cb: types.CallbackQuery):
    await cb.answer()

# ══════════════════════════════════════════════
#  Покупка — выбор размера
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

    kb_rows = [[InlineKeyboardButton(text=f"📐 {s}", callback_data=f"size_{pid}_{s}")] for s in sizes]
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=f"prod_{pid}")])

    text = (f"{ae('size')} <b>Выберите размер</b>\n\n"
            f"<blockquote>Товар: <b>{p['name']}</b></blockquote>")
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("size_"))
async def cb_size(cb: types.CallbackQuery):
    parts = cb.data.split("_", 2)
    pid, size = int(parts[1]), parts[2]
    await _show_payment_confirm(cb, pid, size)

# ══════════════════════════════════════════════
#  Оформление заказа + промокод
# ══════════════════════════════════════════════
async def _show_payment_confirm(cb: types.CallbackQuery, pid: int, size: str,
                                promo=None, promo_error=''):
    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    await ensure_user(cb.from_user)
    user = await get_user(cb.from_user.id)

    rate    = await get_usd_kzt_rate()
    price   = p['price']
    discount = 0
    promo_line = ""
    if promo:
        price, discount, info = apply_promo_to_price(p['price'], promo)
        promo_line = (f"\n🎟 <b>Промокод:</b> <code>{promo['code']}</code>\n  ✅ {info}\n")
    usd_amt = kzt_to_usd(price, rate)

    phone   = user['phone'] if user['phone'] else None
    address = user['default_address'] if user['default_address'] else None
    phone_s   = (f"<code>{phone}</code>" if phone else "<i>не указан ❗</i>")
    address_s = (f"<i>{address}</i>" if address else "<i>не указан ❗</i>")

    error_line = f"\n⚠️ {promo_error}\n" if promo_error else ""

    text = (
        f"🛍 <b>Оформление заказа</b>\n\n"
        f"{ae('box')} {p['name']}  ({size})\n"
        f"{ae('money')} <b>Цена:</b> <code>{fmt_price(p['price'])}</code>"
    )
    if discount > 0:
        text += f" → <code>{fmt_price(price)}</code>"
    text += (
        f" (~{usd_amt} USDT)\n"
        f"{promo_line}{error_line}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('phone')} <b>Телефон:</b> {phone_s}\n"
        f"{ae('pin')} <b>Адрес:</b> {address_s}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>Выберите способ оплаты:</blockquote>"
    )

    promo_code = promo['code'] if promo else ''
    kb_rows = [
        [InlineKeyboardButton(text="🔐 CryptoBot (USDT)",
                              callback_data=f"pcrypto_{pid}_{size}_{promo_code}")],
        [InlineKeyboardButton(text="🏦 Kaspi",
                              callback_data=f"pkaspi_{pid}_{size}_{promo_code}")],
    ]
    if not promo:
        kb_rows.append([InlineKeyboardButton(
            text="🎟 Применить промокод",
            callback_data=f"enterpromo_{pid}_{size}"
        )])
    kb_rows.append([InlineKeyboardButton(text="📝 Добавить примечание продавцу",
                                         callback_data=f"addnote_{pid}_{size}_{promo_code}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=f"buy_{pid}")])

    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("enterpromo_"))
async def cb_enter_promo(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split("_", 2)
    pid   = int(parts[1])
    size  = parts[2]
    await state.update_data(promo_pid=pid, promo_size=size)
    await state.set_state(PromoApplySt.entering)
    try:
        await cb.message.edit_text(
            f"🎟 <b>Введите промокод:</b>\n\n"
            f"<blockquote>Введите код и мы применим скидку/бонус.</blockquote>",
            parse_mode="HTML", reply_markup=kb_back(f"buy_{pid}")
        )
    except Exception:
        await cb.message.answer("🎟 <b>Введите промокод:</b>",
                                parse_mode="HTML", reply_markup=kb_back(f"buy_{pid}"))
    await cb.answer()

@router.message(PromoApplySt.entering)
async def proc_promo_enter(msg: types.Message, state: FSMContext):
    d    = await state.get_data()
    pid  = d.get('promo_pid')
    size = d.get('promo_size', 'ONE_SIZE')
    code = msg.text.strip().upper()
    await state.clear()

    promo, error = await validate_promo(code, msg.from_user.id)

    class FakeCB:
        from_user = msg.from_user
        message   = msg
        data      = f"size_{pid}_{size}"
        async def answer(self, *a, **kw): pass

    await _show_payment_confirm(FakeCB(), pid, size, promo=promo, promo_error=error)

# ══════════════════════════════════════════════
#  Примечание к заказу (необязательно)
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("addnote_"))
async def cb_addnote(cb: types.CallbackQuery, state: FSMContext):
    parts      = cb.data.split("_", 3)
    pid        = int(parts[1])
    size       = parts[2]
    promo_code = parts[3] if len(parts) > 3 else ''
    await state.update_data(note_pid=pid, note_size=size, note_promo=promo_code)
    await state.set_state(OrderNoteSt.entering)
    try:
        await cb.message.edit_text(
            "📝 <b>Примечание продавцу</b>\n\n"
            "<blockquote>Напишите ваше пожелание (необязательно).\n\n"
            "Например: «Пожалуйста, оставьте посылку у подруги»\n\n"
            "Или нажмите /skip чтобы пропустить.</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skipnote_{pid}_{size}_{promo_code}")
            ]])
        )
    except Exception:
        await cb.message.answer("📝 <b>Введите примечание или нажмите Пропустить:</b>",
                                parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("skipnote_"))
async def cb_skipnote(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    parts      = cb.data.split("_", 3)
    pid        = int(parts[1])
    size       = parts[2]
    promo_code = parts[3] if len(parts) > 3 else ''
    promo = None
    if promo_code:
        promo, _ = await validate_promo(promo_code, cb.from_user.id)
    await _show_payment_confirm(cb, pid, size, promo=promo)

@router.message(OrderNoteSt.entering)
async def proc_order_note(msg: types.Message, state: FSMContext):
    d          = await state.get_data()
    pid        = d.get('note_pid')
    size       = d.get('note_size', 'ONE_SIZE')
    promo_code = d.get('note_promo', '')
    note       = msg.text.strip()[:500]
    await state.update_data(pending_note=note)
    await state.clear()
    # Store note temporarily in state isn't persistent — we store it per pid/size/user combo in memory
    # We'll attach it to order after payment via a side-channel cache
    _cache_set(f"ordernote:{msg.from_user.id}:{pid}:{size}", note)
    promo = None
    if promo_code:
        promo, _ = await validate_promo(promo_code, msg.from_user.id)

    class FakeCB:
        from_user = msg.from_user
        message   = msg
        data      = ''
        async def answer(self, *a, **kw): pass

    await msg.answer(f"✅ Примечание сохранено:\n<i>{note}</i>\n\nТеперь выберите способ оплаты:",
                     parse_mode="HTML")
    await _show_payment_confirm(FakeCB(), pid, size, promo=promo)

# ══════════════════════════════════════════════
#  Оплата CryptoBot
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("pcrypto_"))
async def cb_pcrypto(cb: types.CallbackQuery):
    parts = cb.data.split("_", 3)
    pid   = int(parts[1])
    size  = parts[2]
    promo_code = parts[3] if len(parts) > 3 and parts[3] else ''

    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    price    = p['price']
    discount = 0
    promo    = None
    if promo_code:
        promo, _ = await validate_promo(promo_code, cb.from_user.id)
        if promo:
            price, discount, _ = apply_promo_to_price(p['price'], promo)

    rate    = await get_usd_kzt_rate()
    usd_amt = kzt_to_usd(price, rate)

    inv = await create_invoice(usd_amt, f"Покупка: {p['name']} ({size})",
                               f"{cb.from_user.id}:{pid}:{size}")
    if not inv:
        await cb.answer("⚠️ Ошибка создания счёта. Попробуйте позже.", show_alert=True)
        return

    await save_crypto(cb.from_user.id, pid, size, str(inv['invoice_id']),
                      price, usd_amt, promo_code, discount)

    text = (
        f"🔐 <b>Оплата через CryptoBot</b>\n\n"
        f"{ae('box')} <b>Товар:</b> {p['name']}\n"
        f"{ae('size')} <b>Размер:</b> {size}\n"
        f"{ae('money')} <b>Сумма:</b> <code>{fmt_price(price)}</code> (~<b>{usd_amt} USDT</b>)\n"
    )
    if promo_code and discount > 0:
        text += f"🎟 <b>Промокод:</b> <code>{promo_code}</code> (−{fmt_price(discount)})\n"
    text += (f"\n<blockquote>1. Нажмите «Оплатить»\n2. Вернитесь в бот\n3. Нажмите «Проверить оплату»</blockquote>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=inv['pay_url'])],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"chk_{inv['invoice_id']}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data=f"buy_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("chk_"))
async def cb_chk(cb: types.CallbackQuery):
    inv_id = cb.data[4:]
    inv    = await check_invoice(inv_id)
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

    promo_code = payment.get('promo_code', '')
    discount   = payment.get('discount', 0)

    oid = await create_order(uid, cb.from_user.username, cb.from_user.first_name,
                             pid, size, price, 'crypto', user['phone'],
                             user['default_address'], promo_code, discount)
    # Attach pending note if exists
    note_key = f"ordernote:{uid}:{pid}:{size}"
    pending_note, note_hit = _cache_get(note_key)
    if note_hit and pending_note:
        await set_order_note(oid, pending_note)
        _cache_invalidate(note_key)
    await add_purchase(uid, pid, price, 'crypto')
    await reduce_stock(pid)
    bonus = await add_bonus(uid, price)

    # Партнёрский бонус для ПОКУПАТЕЛЯ
    buyer = await get_user(uid)
    ref_code = buyer.get('ref_code', '') if buyer else ''
    ref_bonus_msg = ""
    if ref_code:
        partner = await get_partner_by_ref(ref_code)
        if partner and partner['user_id'] != uid:
            is_new = buyer['total_purchases'] <= 1
            try:
                bonus_cfg = json.loads(partner['bonus_new'] if is_new else partner['bonus_repeat'])
            except Exception:
                bonus_cfg = {"type": "discount_percent", "value": 5}
            # Применяем бонус к ПОКУПАТЕЛЮ
            btype = bonus_cfg.get('type', '')
            bval  = float(bonus_cfg.get('value', 0))
            buyer_bonus_amount = 0.0
            if btype == 'bonus_fixed' and bval > 0:
                await db_run('UPDATE users SET bonus_balance=bonus_balance+$1 WHERE user_id=$2', (bval, uid))
                _cache_invalidate(f"user:{uid}")
                buyer_bonus_amount = bval
                ref_bonus_msg = f"🎁 <b>Реф-бонус:</b> +{fmt_price(bval)} на бонусный счёт\n"
            elif btype == 'cashback_x2':
                extra = bonus
                await db_run('UPDATE users SET bonus_balance=bonus_balance+$1 WHERE user_id=$2', (extra, uid))
                _cache_invalidate(f"user:{uid}")
                buyer_bonus_amount = extra
                ref_bonus_msg = f"🎁 <b>Реф-бонус:</b> кешбэк x2 (+{fmt_price(extra)})\n"
            # discount_percent уже был применён на этапе формирования цены (при наличии promo)
            # Партнёр получает фиксированный процент магазина (5% по умолчанию)
            partner_earn = round(price * 5 / 100, 0)
            await record_partner_referral(partner['user_id'], uid, is_new, partner_earn, oid)
            try:
                await bot.send_message(partner['user_id'],
                    f"🤝 <b>По вашей ссылке сделали заказ!</b>\n\n"
                    f"{'🆕 Новый' if is_new else '🔄 Повторный'} покупатель совершил покупку.\n"
                    f"💰 Вам начислено: <b>+{fmt_price(partner_earn)}</b>",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    if promo_code:
        promo_row = await get_promo_by_code(promo_code)
        if promo_row:
            await use_promo(uid, promo_row['id'], oid)
            if promo_row['promo_type'] == 'cashback_bonus':
                await db_run('UPDATE users SET bonus_balance=bonus_balance+$1 WHERE user_id=$2',
                             (promo_row['value'], uid))
                _cache_invalidate(f"user:{uid}")

    try:
        await cb.message.delete()
    except Exception:
        pass

    await _notify_manager_new_order(oid, uid, cb.from_user.username, cb.from_user.first_name,
                                    product, size, price, 'CryptoBot',
                                    user['phone'], user['default_address'], promo_code, discount)
    await log_event("purchase", uid, f"order={oid},method=crypto,amount={price}")

    promo_msg = ""
    if promo_code:
        promo_msg = f"🎟 Промокод: <code>{promo_code}</code>\n"
    if discount > 0:
        promo_msg += f"💰 Скидка: {fmt_price(discount)}\n"

    await bot.send_message(uid,
        f"🎉 <b>Оплата подтверждена! Заказ #{oid} оформлен.</b>\n\n"
        f"{ae('box')} {product['name']}  ({size})\n"
        f"{ae('money')} {fmt_price(price)}\n"
        f"{promo_msg}"
        f"{ae('phone')} {user['phone']}\n"
        f"{ae('pin')} {user['default_address']}\n\n"
        f"{ae('gift')} Кэшбэк: <b>{fmt_price(bonus)}</b> на бонусный счёт\n"
        f"{ref_bonus_msg}"
        f"\n<blockquote>Мы свяжемся с вами для согласования доставки.</blockquote>",
        parse_mode="HTML", reply_markup=kb_main()
    )
    await cb.answer("✅ Готово!")

# ══════════════════════════════════════════════
#  Оплата Kaspi
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("pkaspi_"))
async def cb_pkaspi(cb: types.CallbackQuery):
    parts = cb.data.split("_", 3)
    pid   = int(parts[1])
    size  = parts[2]
    promo_code = parts[3] if len(parts) > 3 and parts[3] else ''

    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    price    = p['price']
    discount = 0
    if promo_code:
        promo, _ = await validate_promo(promo_code, cb.from_user.id)
        if promo:
            price, discount, _ = apply_promo_to_price(p['price'], promo)

    # Читаем примечание покупателя из кэша и сохраняем вместе с платежом
    note_key = f"ordernote:{cb.from_user.id}:{pid}:{size}"
    pending_note, note_hit = _cache_get(note_key)
    buyer_note = pending_note if note_hit and pending_note else ''

    kid = await save_kaspi(cb.from_user.id, pid, size, price, promo_code, discount, buyer_note)

    text = (
        f"🏦 <b>Оплата через Kaspi</b>\n\n"
        f"{ae('box')} <b>Товар:</b> {p['name']}  ({size})\n"
        f"{ae('money')} <b>Сумма:</b> <code>{fmt_price(price)}</code>\n"
    )
    if promo_code and discount > 0:
        text += f"🎟 <b>Промокод:</b> <code>{promo_code}</code> (−{fmt_price(discount)})\n"
    text += (
        f"\n━━━━━━━━━━━━━━━━━\n"
        f"📱 Номер для перевода:\n<code>{KASPI_PHONE}</code>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>После перевода нажмите «Я оплатил» — менеджер проверит и подтвердит вручную.</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"kpaid_{kid}_{pid}_{size}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data=f"buy_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("kpaid_"))
async def cb_kpaid(cb: types.CallbackQuery):
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

    promo_line = ""
    if kp.get('promo_code'):
        promo_line = (f"🎟 <b>Промокод:</b> <code>{kp['promo_code']}</code>\n"
                      f"💰 <b>Скидка:</b> {fmt_price(kp.get('discount', 0))}\n")

    note_line = ""
    if kp.get('buyer_note'):
        note_line = f"📝 <b>Примечание:</b>\n<blockquote>{kp['buyer_note']}</blockquote>\n"

    mgr_text = (
        f"🏦 <b>ЗАЯВКА KASPI #{kid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('user')} @{uname} (<code>{kp['user_id']}</code>)\n"
        f"👤 <b>Имя:</b> {cb.from_user.first_name or '—'}\n"
        f"{ae('box')} <b>Товар:</b> {product['name']}  ({size})\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(kp['amount'])}\n"
        f"{promo_line}"
        f"{ae('phone')} <b>Телефон:</b> {user['phone'] if user else '—'}\n"
        f"{ae('pin')} <b>Адрес:</b> {user['default_address'] if user else '—'}\n"
        f"{note_line}"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>Проверьте поступление перевода:</blockquote>"
    )
    mgr_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"kapprove_{kid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"kreject_{kid}"),
    ]])
    try:
        mgr_msg = await bot.send_message(MANAGER_ID, mgr_text, parse_mode="HTML", reply_markup=mgr_kb)
        await set_kaspi_status(kid, 'waiting', mgr_msg.message_id)
    except Exception:
        await cb.answer("⚠️ Не удалось уведомить менеджера. Обратитесь в поддержку.", show_alert=True)
        return

    try:
        await cb.message.edit_text(
            f"⏳ <b>Ожидаем подтверждения менеджера</b>\n\n"
            f"<blockquote>Обычно это занимает несколько минут.</blockquote>",
            parse_mode="HTML", reply_markup=kb_back("shop")
        )
    except Exception:
        pass
    await cb.answer("✅ Заявка отправлена!")

@router.callback_query(F.data.startswith("kapprove_"))
async def cb_kapprove(cb: types.CallbackQuery):
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

    promo_code = kp.get('promo_code', '')
    discount   = kp.get('discount', 0)

    buyer = await db_one('SELECT username, first_name FROM users WHERE user_id=$1', (kp['user_id'],))
    buyer_uname = buyer['username'] if buyer else ''
    buyer_fname = buyer['first_name'] if buyer else ''

    oid = await create_order(kp['user_id'], buyer_uname, buyer_fname,
                             kp['product_id'], size, kp['amount'], 'kaspi',
                             user['phone'] if user else '', user['default_address'] if user else '',
                             promo_code, discount)
    # Сохраняем примечание покупателя если есть
    if kp.get('buyer_note'):
        await set_order_note(oid, kp['buyer_note'])
    await add_purchase(kp['user_id'], kp['product_id'], kp['amount'], 'kaspi')
    await reduce_stock(kp['product_id'])
    bonus = await add_bonus(kp['user_id'], kp['amount'])
    await log_event("purchase", kp['user_id'], f"order={oid},method=kaspi,amount={kp['amount']}")

    if promo_code:
        promo_row = await get_promo_by_code(promo_code)
        if promo_row:
            await use_promo(kp['user_id'], promo_row['id'], oid)
            if promo_row['promo_type'] == 'cashback_bonus':
                await db_run('UPDATE users SET bonus_balance=bonus_balance+$1 WHERE user_id=$2',
                             (promo_row['value'], kp['user_id']))
                _cache_invalidate(f"user:{kp['user_id']}")

    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n✅ <b>ПОДТВЕРЖДЕНО</b> — @{who}",
                                   parse_mode="HTML")
    except Exception:
        pass

    await _notify_manager_new_order(oid, kp['user_id'], buyer_uname, buyer_fname,
                                    product, size, kp['amount'], 'Kaspi',
                                    user['phone'] if user else '', user['default_address'] if user else '',
                                    promo_code, discount)

    promo_msg = ""
    if promo_code:
        promo_msg = f"🎟 Промокод: <code>{promo_code}</code>\n"
    if discount > 0:
        promo_msg += f"💰 Скидка: {fmt_price(discount)}\n"

    try:
        await bot.send_message(kp['user_id'],
            f"✅ <b>Оплата подтверждена! Заказ #{oid} оформлен.</b>\n\n"
            f"{ae('box')} {product['name']}  ({size})\n"
            f"{ae('money')} {fmt_price(kp['amount'])}\n"
            f"{promo_msg}"
            f"{ae('gift')} Кэшбэк: <b>{fmt_price(bonus)}</b>\n\n"
            f"<blockquote>Ожидайте уведомлений о статусе доставки!</blockquote>",
            parse_mode="HTML"
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
        await bot.send_message(kp['user_id'],
            f"❌ <b>Оплата отклонена</b>\n\n"
            f"{ae('box')} {product['name']} — {fmt_price(kp['amount'])}\n\n"
            f"<blockquote>Менеджер не нашёл перевод. Если уверены в оплате — напишите: {SUPPORT_USERNAME}</blockquote>",
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
        await cb.message.edit_text(cb.message.html_text + f"\n\n❌ <b>ОТКЛОНЕНО</b> — @{who}",
                                   parse_mode="HTML")
    except Exception:
        pass
    await cb.answer("❌ Отклонено, пользователь уведомлён")

# ══════════════════════════════════════════════
#  Уведомление менеджера о новом заказе
# ══════════════════════════════════════════════
async def _notify_manager_new_order(oid, uid, uname, first_name, product, size,
                                    price, method, phone, address, promo_code='', discount=0):
    uname_s    = f"@{uname}" if uname else "—"
    promo_line = ""
    if promo_code:
        promo_line = (f"🎟 <b>Промокод:</b> <code>{promo_code}</code>\n"
                      f"💰 <b>Скидка:</b> {fmt_price(discount)}\n")
    note = await get_order_note(oid)
    note_line = f"📝 <b>Примечание:</b>\n<blockquote>{note}</blockquote>\n" if note else ""

    text = (
        f"🛍 <b>НОВЫЙ ЗАКАЗ #{oid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('user')} <b>Покупатель:</b> {uname_s}\n"
        f"👤 <b>Имя:</b> {first_name or '—'}\n"
        f"🆔 <b>TG ID:</b> <code>{uid}</code>\n"
        f"{ae('box')} <b>Товар:</b> {product['name']}\n"
        f"{ae('size')} <b>Размер:</b> {size}\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(price)}\n"
        f"{promo_line}"
        f"💳 <b>Оплата:</b> {method}\n"
        f"{ae('phone')} <b>Телефон:</b> {phone or '—'}\n"
        f"{ae('pin')} <b>Адрес:</b> {address or '—'}\n"
        f"{note_line}"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Управление статусом", callback_data=f"ordstatus_{oid}")
    ]])
    try:
        await bot.send_message(MANAGER_ID, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

# ══════════════════════════════════════════════
#  Управление статусами заказов
# ══════════════════════════════════════════════
ORDER_STATUSES = ["processing", "china", "arrived", "delivered", "confirmed"]

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
            text=f"{mark}{order_status_text(s)}", callback_data=f"setordst_{oid}_{s}"
        )])
    kb_rows.append([InlineKeyboardButton(text="✏️ Свой статус", callback_data=f"customst_{oid}")])

    buyer_info = ""
    if order.get('username'):
        buyer_info += f"\n{ae('user')} @{order['username']}"
    if order.get('first_name'):
        buyer_info += f" ({order['first_name']})"
    buyer_info += f"\n🆔 <code>{order['user_id']}</code>"

    text = (f"📋 <b>Заказ #{oid}</b>{buyer_info}\n"
            f"Статус: {order_status_text(order['status'])}\n\n"
            f"<blockquote>Выберите статус или введите свой:</blockquote>")
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("customst_"))
async def cb_customst(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    oid = int(cb.data.split("_")[1])
    await state.update_data(custom_oid=oid)
    await state.set_state(AdminSt.set_custom_status)
    try:
        await cb.message.edit_text(
            f"✏️ <b>Произвольный статус для заказа #{oid}</b>\n\n"
            f"<blockquote>Введите текст статуса (например: «Сортировочный центр»):</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="‹ Назад", callback_data=f"ordstatus_{oid}")
            ]])
        )
    except Exception:
        pass
    await cb.answer()

@router.message(AdminSt.set_custom_status)
async def proc_custom_status(msg: types.Message, state: FSMContext):
    d      = await state.get_data()
    oid    = d.get('custom_oid')
    status = msg.text.strip()[:100]
    await state.clear()
    if not oid:
        await msg.answer("❌ Ошибка: заказ не найден.", reply_markup=kb_admin_back())
        return
    order   = await get_order(oid)
    product = await get_product(order['product_id']) if order else None
    await set_order_status(oid, status)
    pname = product['name'] if product else '—'
    short = f"#{product.get('short_id', '')}" if product and product.get('short_id') else ''
    try:
        await bot.send_message(order['user_id'],
            f"{ae('truck')} <b>Статус заказа #{oid} обновлён</b>\n\n"
            f"{ae('box')} {pname} {short} ({order['size']})\n"
            f"🔄 <b>Новый статус:</b> {status}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await msg.answer(f"✅ <b>Статус заказа #{oid} обновлён:</b>\n<i>{status}</i>",
                     parse_mode="HTML", reply_markup=kb_admin_back())

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
    pname = product['name'] if product else '—'
    short = f"#{product.get('short_id', '')}" if product and product.get('short_id') else ''

    try:
        if status == "delivered":
            await bot.send_message(order['user_id'],
                f"🚚 <b>Ваш заказ #{oid} доставлен!</b>\n\n"
                f"{ae('box')} {pname} {short} ({order['size']})\n\n"
                f"<blockquote>Пожалуйста, подтвердите получение:</blockquote>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Подтверждаю получение",
                                         callback_data=f"confirm_order_{oid}")
                ]])
            )
        else:
            await bot.send_message(order['user_id'],
                f"{ae('truck')} <b>Статус заказа #{oid} обновлён</b>\n\n"
                f"{ae('box')} {pname} {short} ({order['size']})\n"
                f"🔄 <b>Новый статус:</b> {order_status_text(status)}",
                parse_mode="HTML"
            )
    except Exception:
        pass

    await cb.answer(f"✅ {order_status_text(status)}", show_alert=True)
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n→ {order_status_text(status)}",
                                   parse_mode="HTML")
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
        await bot.send_message(MANAGER_ID, f"✅ <b>Заказ #{oid} подтверждён покупателем.</b>",
                               parse_mode="HTML")
    except Exception:
        pass

    await state.update_data(review_oid=oid, review_pid=order['product_id'])
    await state.set_state(ReviewSt.rating)
    try:
        await cb.message.edit_text(
            f"🎉 <b>Спасибо за подтверждение!</b>\n\n<blockquote>Оцените товар от 1 до 5 звёзд:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=str(i), callback_data=f"rating_{i}") for i in range(1, 6)
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
        await cb.message.edit_text(f"Оценка: <b>{stars_map[rating]}</b>\n\n"
                                   f"<blockquote>Напишите ваш отзыв о товаре:</blockquote>",
                                   parse_mode="HTML")
    except Exception:
        pass
    await cb.answer()

@router.message(ReviewSt.comment)
async def review_comment(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await add_review(msg.from_user.id, d['review_pid'], d['review_oid'], d['rating'], msg.text)
    await msg.answer(f"⭐ <b>Спасибо за отзыв!</b>\n\n"
                     f"<blockquote>Ваш отзыв поможет другим покупателям.</blockquote>",
                     parse_mode="HTML", reply_markup=kb_main())

# ══════════════════════════════════════════════
#  РЕКЛАМА
# ══════════════════════════════════════════════
AD_WARNING_TEXT = (
    "⚠️ <b>ВАЖНО! ОЗНАКОМЬТЕСЬ ДО ОПЛАТЫ</b>\n\n"
    "Уважаемые рекламодатели! Прежде чем оплатить заказ, внимательно прочитайте список.\n\n"
    "<b>МЫ НЕ РЕКЛАМИРУЕМ следующие тематики НИ ПРИ КАКИХ УСЛОВИЯХ:</b>\n\n"
    "<b>1. МОШЕННИЧЕСТВО И ФИНАНСОВЫЕ ПИРАМИДЫ</b>\n"
    "❌ Финансовые пирамиды, хайпы, сомнительные инвестиции.\n"
    "❌ Заработок в интернете «без вложений».\n"
    "❌ Продажа баз данных, слитой информации, взломов.\n\n"
    "<b>2. СПАМ И НАКРУТКИ</b>\n"
    "❌ Программы для рассылок. ❌ Накрутка подписчиков.\n\n"
    "<b>3. АЗАРТНЫЕ ИГРЫ</b>\n"
    "❌ Онлайн-казино. ❌ Букмекерские конторы без лицензии.\n\n"
    "<b>4. ВЗРОСЛЫЙ КОНТЕНТ (18+)</b>\n"
    "❌ Порно, эротика, интим-услуги.\n\n"
    "<b>5-7. ТОВАРЫ БЕЗ ДОКАЗАТЕЛЬСТВ / ПОЛИТИКА / КОНКУРЕНТЫ</b>\n"
    "❌ «Чудо-лекарства», политическая агитация, прямые конкуренты.\n\n"
    "⚠️ Если ваш товар в этом списке — <b>НЕ ОПЛАЧИВАЙТЕ</b>."
)

@router.callback_query(F.data == "ad_warning")
async def cb_ad_warning(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ознакомлен, продолжить", callback_data="ad_continue")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="shop")],
    ])
    try:
        await cb.message.edit_text(AD_WARNING_TEXT, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(AD_WARNING_TEXT, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "ad_continue")
async def cb_ad_continue(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdSt.description)
    text = (f"📢 <b>Оформление рекламы</b>\n\n"
            f"<blockquote>Стоимость размещения: <b>{fmt_price(AD_PRICE_KZT)}</b>\n\n"
            f"Опишите вашу рекламу:\n• Что рекламируете\n• Ссылка / контакт\n• Пожелания по формату</blockquote>")
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb_back("ad_warning"))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb_back("ad_warning"))
    await cb.answer()

@router.message(AdSt.description)
async def proc_ad_description(msg: types.Message, state: FSMContext):
    await state.update_data(ad_desc=msg.text.strip())
    text = (f"📢 <b>Выберите способ оплаты</b>\n\n"
            f"Стоимость: <b>{fmt_price(AD_PRICE_KZT)}</b>\n\n"
            f"<blockquote>Ваша реклама:\n{msg.text.strip()[:200]}</blockquote>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 CryptoBot (USDT)", callback_data="ad_pay_crypto")],
        [InlineKeyboardButton(text="🏦 Kaspi", callback_data="ad_pay_kaspi")],
    ])
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "ad_pay_crypto")
async def cb_ad_pay_crypto(cb: types.CallbackQuery, state: FSMContext):
    rate    = await get_usd_kzt_rate()
    usd_amt = kzt_to_usd(AD_PRICE_KZT, rate)
    inv = await create_invoice(usd_amt, "Реклама в боте", f"ad:{cb.from_user.id}")
    if not inv:
        await cb.answer("⚠️ Ошибка создания счёта.", show_alert=True)
        return
    await state.update_data(ad_inv_id=str(inv['invoice_id']))
    text = (f"🔐 <b>Оплата рекламы через CryptoBot</b>\n\n"
            f"{ae('money')} <b>Сумма:</b> <code>{fmt_price(AD_PRICE_KZT)}</code> (~<b>{usd_amt} USDT</b>)\n\n"
            f"<blockquote>1. Нажмите «Оплатить»\n2. Вернитесь в бот\n3. Нажмите «Проверить оплату»</blockquote>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=inv['pay_url'])],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"ad_chk_{inv['invoice_id']}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("ad_chk_"))
async def cb_ad_chk(cb: types.CallbackQuery, state: FSMContext):
    inv_id = cb.data[7:]
    inv    = await check_invoice(inv_id)
    if not inv:
        await cb.answer("⚠️ Ошибка проверки.", show_alert=True)
        return
    if inv['status'] != 'paid':
        await cb.answer("⏳ Оплата ещё не поступила.", show_alert=True)
        return
    d    = await state.get_data()
    desc = d.get('ad_desc', '—')
    await state.clear()
    aid = await create_ad_request(cb.from_user.id, desc, 'crypto')
    await _notify_manager_ad(aid, cb.from_user, desc, 'CryptoBot')
    await cb.message.edit_text(
        f"🎉 <b>Оплата получена! Заявка на рекламу #{aid} принята.</b>\n\n"
        f"<blockquote>Менеджер свяжется с вами.</blockquote>",
        parse_mode="HTML", reply_markup=kb_main()
    )
    await cb.answer("✅ Готово!")

@router.callback_query(F.data == "ad_pay_kaspi")
async def cb_ad_pay_kaspi(cb: types.CallbackQuery, state: FSMContext):
    text = (f"🏦 <b>Оплата рекламы через Kaspi</b>\n\n"
            f"{ae('money')} <b>Сумма:</b> <code>{fmt_price(AD_PRICE_KZT)}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📱 Номер для перевода:\n<code>{KASPI_PHONE}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"<blockquote>После перевода нажмите «Я оплатил»</blockquote>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я оплатил", callback_data="ad_kaspi_paid")
    ]])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "ad_kaspi_paid")
async def cb_ad_kaspi_paid(cb: types.CallbackQuery, state: FSMContext):
    d    = await state.get_data()
    desc = d.get('ad_desc', '—')
    await state.clear()
    aid = await create_ad_request(cb.from_user.id, desc, 'kaspi')

    mgr_text = (
        f"📢 <b>ЗАЯВКА НА РЕКЛАМУ #{aid} (Kaspi)</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('user')} @{cb.from_user.username or '—'} (<code>{cb.from_user.id}</code>)\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(AD_PRICE_KZT)} (Kaspi)\n"
        f"📝 <b>Описание:</b>\n<blockquote>{desc[:500]}</blockquote>\n"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━\n\n<blockquote>Проверьте перевод:</blockquote>"
    )
    mgr_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ad_approve_{aid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ad_reject_{aid}"),
    ]])
    try:
        await bot.send_message(MANAGER_ID, mgr_text, parse_mode="HTML", reply_markup=mgr_kb)
    except Exception:
        pass
    try:
        await cb.message.edit_text(
            f"⏳ <b>Заявка #{aid} отправлена менеджеру.</b>\n\n<blockquote>Ожидайте подтверждения.</blockquote>",
            parse_mode="HTML", reply_markup=kb_main()
        )
    except Exception:
        pass
    await cb.answer("✅ Заявка отправлена!")

@router.callback_query(F.data.startswith("ad_approve_"))
async def cb_ad_approve(cb: types.CallbackQuery):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split("_")
    if len(parts) < 3:
        await cb.answer("Ошибка", show_alert=True)
        return
    aid = int(parts[2])
    ar  = await get_ad_request(aid)
    if not ar:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if ar['status'] != 'pending':
        await cb.answer("Уже обработана", show_alert=True)
        return
    await set_ad_status(aid, 'approved')
    try:
        await bot.send_message(ar['user_id'],
            f"✅ <b>Оплата рекламы подтверждена! Заявка #{aid} принята.</b>\n\n"
            f"<blockquote>Менеджер свяжется для запуска рекламы.</blockquote>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n✅ <b>ПОДТВЕРЖДЕНО</b> — @{who}",
                                   parse_mode="HTML")
    except Exception:
        pass
    await cb.answer("✅ Реклама подтверждена!")

@router.callback_query(F.data.startswith("ad_reject_"))
async def cb_ad_reject(cb: types.CallbackQuery):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split("_")
    if len(parts) < 3:
        await cb.answer("Ошибка", show_alert=True)
        return
    aid = int(parts[2])
    ar  = await get_ad_request(aid)
    if not ar or ar['status'] != 'pending':
        await cb.answer("Заявка уже обработана", show_alert=True)
        return
    await set_ad_status(aid, 'rejected')
    try:
        await bot.send_message(ar['user_id'],
            f"❌ <b>Оплата рекламы #{aid} не подтверждена.</b>\n\n"
            f"<blockquote>Свяжитесь с поддержкой: {SUPPORT_USERNAME}</blockquote>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n❌ <b>ОТКЛОНЕНО</b> — @{who}",
                                   parse_mode="HTML")
    except Exception:
        pass
    await cb.answer("❌ Отклонено")

async def _notify_manager_ad(aid, tg_user, desc, method):
    text = (
        f"📢 <b>НОВАЯ ЗАЯВКА НА РЕКЛАМУ #{aid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('user')} @{tg_user.username or '—'} (<code>{tg_user.id}</code>)\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(AD_PRICE_KZT)}\n"
        f"💳 <b>Оплата:</b> {method}\n"
        f"📝 <b>Описание:</b>\n<blockquote>{desc[:500]}</blockquote>\n"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    try:
        await bot.send_message(MANAGER_ID, text, parse_mode="HTML")
    except Exception:
        pass

# ══════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_panel")
async def cb_adm_panel(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.clear()
    try:
        await cb.message.edit_text("🎩 <b>Панель управления</b>",
                                   parse_mode="HTML", reply_markup=kb_admin())
    except Exception:
        await send_media(cb.from_user.id, "🎩 <b>Панель управления</b>", "admin_panel", kb_admin())
    await cb.answer()

@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uc, pc, rv, ac, oc, prc, bc, cmp = await get_stats()
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👥 Пользователей: <b>{uc}</b>\n"
        f"🚫 Заблокировано: <b>{bc}</b>\n"
        f"{ae('cart')} Заказов: <b>{pc}</b>\n"
        f"{ae('money')} Выручка: <b>{fmt_price(rv)}</b>\n"
        f"{ae('box')} Товаров: <b>{ac}</b>\n"
        f"🔄 В работе: <b>{oc}</b>\n"
        f"🎟 Промокодов: <b>{prc}</b>\n"
        f"⚠️ Жалоб (открытых): <b>{cmp}</b>\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Лог за 24ч (HTML)", callback_data="adm_log")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ══════════════════════════════════════════════
#  ADMIN — HTML Лог за 24 часа
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_log")
async def cb_adm_log(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    await cb.answer("⏳ Генерирую лог...", show_alert=False)

    since = (datetime.now() - timedelta(hours=24)).isoformat()
    events = await db_all(
        "SELECT * FROM event_log WHERE created_at >= $1 ORDER BY created_at DESC",
        (since,)
    )
    orders = await db_all(
        "SELECT o.*, p.name AS pname FROM orders o JOIN products p ON o.product_id=p.id "
        "WHERE o.created_at >= $1 ORDER BY o.created_at DESC",
        (since,)
    )
    purchases = await db_all(
        "SELECT pu.*, u.username, u.first_name, p.name AS pname "
        "FROM purchases pu JOIN users u ON pu.user_id=u.user_id JOIN products p ON pu.product_id=p.id "
        "WHERE pu.purchased_at >= $1 ORDER BY pu.purchased_at DESC",
        (since,)
    )
    complaints = await db_all(
        "SELECT c.*, u.username, u.first_name FROM complaints c JOIN users u ON c.user_id=u.user_id "
        "WHERE c.created_at >= $1 ORDER BY c.created_at DESC",
        (since,)
    )
    new_users = await db_all(
        "SELECT * FROM users WHERE registered_at >= $1 ORDER BY registered_at DESC",
        (since,)
    )

    total_revenue = sum(p['price'] for p in purchases)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ShopBot — Лог за 24 часа</title>
<style>
  :root {{
    --bg: #0f0f1a;
    --card: #1a1a2e;
    --accent: #7c3aed;
    --accent2: #06b6d4;
    --green: #10b981;
    --red: #ef4444;
    --yellow: #f59e0b;
    --text: #e2e8f0;
    --muted: #64748b;
    --border: #2d2d44;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 20px;
  }}
  .header {{
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 16px;
    padding: 24px 32px;
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .header h1 {{ font-size: 1.8rem; font-weight: 700; }}
  .header p {{ opacity: 0.85; font-size: 0.9rem; }}
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .stat-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }}
  .stat-card .val {{
    font-size: 2rem;
    font-weight: 700;
    color: var(--accent2);
  }}
  .stat-card .lbl {{
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 4px;
  }}
  .section {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 16px;
    margin-bottom: 20px;
    overflow: hidden;
  }}
  .section-header {{
    padding: 16px 24px;
    background: linear-gradient(90deg, rgba(124,58,237,.2), transparent);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 600;
    font-size: 1rem;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  th {{
    padding: 12px 16px;
    text-align: left;
    font-size: 0.75rem;
    text-transform: uppercase;
    color: var(--muted);
    background: rgba(255,255,255,.03);
    border-bottom: 1px solid var(--border);
  }}
  td {{
    padding: 12px 16px;
    font-size: 0.85rem;
    border-bottom: 1px solid rgba(45,45,68,.5);
    vertical-align: top;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: rgba(255,255,255,.02); }}
  .badge {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
  }}
  .badge-green {{ background: rgba(16,185,129,.2); color: var(--green); }}
  .badge-red {{ background: rgba(239,68,68,.2); color: var(--red); }}
  .badge-yellow {{ background: rgba(245,158,11,.2); color: var(--yellow); }}
  .badge-blue {{ background: rgba(6,182,212,.2); color: var(--accent2); }}
  .empty {{ padding: 32px; text-align: center; color: var(--muted); }}
  .footer {{
    text-align: center;
    color: var(--muted);
    font-size: 0.8rem;
    margin-top: 32px;
    padding-bottom: 20px;
  }}
  @media (max-width: 600px) {{
    .header h1 {{ font-size: 1.3rem; }}
    th, td {{ padding: 8px 12px; font-size: 0.8rem; }}
  }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>📊 ShopBot — Лог за 24 часа</h1>
    <p>Сгенерировано: {fmt_dt()} | {SHOP_NAME}</p>
  </div>
</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="val">{len(purchases)}</div>
    <div class="lbl">Покупок</div>
  </div>
  <div class="stat-card">
    <div class="val">{fmt_price(total_revenue)}</div>
    <div class="lbl">Выручка</div>
  </div>
  <div class="stat-card">
    <div class="val">{len(new_users)}</div>
    <div class="lbl">Новых юзеров</div>
  </div>
  <div class="stat-card">
    <div class="val">{len(orders)}</div>
    <div class="lbl">Заказов</div>
  </div>
  <div class="stat-card">
    <div class="val">{len(complaints)}</div>
    <div class="lbl">Жалоб</div>
  </div>
  <div class="stat-card">
    <div class="val">{len(events)}</div>
    <div class="lbl">Событий</div>
  </div>
</div>
"""

    # Покупки
    html += """<div class="section">
  <div class="section-header">💰 Покупки</div>"""
    if purchases:
        html += """<table>
  <tr><th>#</th><th>Покупатель</th><th>Товар</th><th>Сумма</th><th>Метод</th><th>Время</th></tr>"""
        for pu in purchases:
            uname = f"@{pu['username']}" if pu.get('username') else str(pu['user_id'])
            method_badge = ('badge-blue' if pu['method'] == 'crypto' else 'badge-yellow')
            html += f"""<tr>
    <td>{pu['id']}</td>
    <td>{uname}<br><small style="color:#64748b">{pu.get('first_name','')}</small></td>
    <td>{pu['pname']}</td>
    <td style="color:#10b981;font-weight:600">{fmt_price(pu['price'])}</td>
    <td><span class="badge {method_badge}">{pu['method'].upper()}</span></td>
    <td>{pu['purchased_at'][:16]}</td>
  </tr>"""
        html += "</table>"
    else:
        html += '<div class="empty">Покупок за 24 часа не было</div>'
    html += "</div>"

    # Заказы
    html += """<div class="section">
  <div class="section-header">📦 Новые заказы</div>"""
    if orders:
        html += """<table>
  <tr><th>#</th><th>Покупатель</th><th>Товар</th><th>Размер</th><th>Статус</th><th>Время</th></tr>"""
        for o in orders:
            uname  = f"@{o['username']}" if o.get('username') else str(o['user_id'])
            status = o['status']
            s_class = 'badge-green' if status == 'confirmed' else ('badge-red' if status == 'rejected' else 'badge-yellow')
            html += f"""<tr>
    <td>{o['id']}</td>
    <td>{uname}</td>
    <td>{o['pname']}</td>
    <td>{o['size']}</td>
    <td><span class="badge {s_class}">{order_status_text(status)}</span></td>
    <td>{o['created_at'][:16]}</td>
  </tr>"""
        html += "</table>"
    else:
        html += '<div class="empty">Заказов за 24 часа не было</div>'
    html += "</div>"

    # Жалобы
    html += """<div class="section">
  <div class="section-header">⚠️ Жалобы</div>"""
    if complaints:
        html += """<table>
  <tr><th>#</th><th>Пользователь</th><th>Заказ</th><th>Описание</th><th>Статус</th><th>Время</th></tr>"""
        for c in complaints:
            uname = f"@{c['username']}" if c.get('username') else str(c['user_id'])
            html += f"""<tr>
    <td>{c['id']}</td>
    <td>{uname}<br><small style="color:#64748b">{c.get('first_name','')}</small></td>
    <td>#{c['order_id'] or '—'}</td>
    <td style="max-width:300px;word-break:break-word">{c['description'][:200]}</td>
    <td><span class="badge badge-red">{c['status']}</span></td>
    <td>{c['created_at'][:16]}</td>
  </tr>"""
        html += "</table>"
    else:
        html += '<div class="empty">Жалоб за 24 часа не было</div>'
    html += "</div>"

    # Новые пользователи
    html += """<div class="section">
  <div class="section-header">👥 Новые пользователи</div>"""
    if new_users:
        html += """<table>
  <tr><th>ID</th><th>Username</th><th>Имя</th><th>Регистрация</th></tr>"""
        for u in new_users:
            uname = f"@{u['username']}" if u.get('username') else '—'
            html += f"""<tr>
    <td><code>{u['user_id']}</code></td>
    <td>{uname}</td>
    <td>{u.get('first_name','—')}</td>
    <td>{u['registered_at'][:16]}</td>
  </tr>"""
        html += "</table>"
    else:
        html += '<div class="empty">Новых пользователей не было</div>'
    html += "</div>"

    # События
    html += """<div class="section">
  <div class="section-header">📋 Лог событий</div>"""
    if events:
        html += """<table>
  <tr><th>Событие</th><th>Пользователь</th><th>Данные</th><th>Время</th></tr>"""
        for ev in events[:50]:
            html += f"""<tr>
    <td><span class="badge badge-blue">{ev['event_type']}</span></td>
    <td>{ev['user_id']}</td>
    <td>{str(ev.get('data',''))[:100]}</td>
    <td>{ev['created_at'][:16]}</td>
  </tr>"""
        html += "</table>"
    else:
        html += '<div class="empty">Событий не зарегистрировано</div>'
    html += "</div>"

    html += f"""<div class="footer">
  Сгенерировано ботом {SHOP_NAME} | {fmt_dt()}
</div>
</body>
</html>"""

    # Отправляем как файл
    buf = io.BytesIO(html.encode('utf-8'))
    buf.name = f"shopbot_log_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    await bot.send_document(cb.from_user.id, types.BufferedInputFile(buf.getvalue(), filename=buf.name),
                            caption=f"📊 <b>Лог за 24 часа</b>\n{fmt_dt()}",
                            parse_mode="HTML")

# ══════════════════════════════════════════════
#  ADMIN — Заказы
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_orders")
async def cb_adm_orders(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    orders = await db_all(
        "SELECT o.*, p.name AS pname FROM orders o JOIN products p ON o.product_id=p.id "
        "ORDER BY o.created_at DESC LIMIT 20"
    )
    if not orders:
        await cb.answer("Заказов пока нет", show_alert=True)
        return
    kb_rows = []
    for o in orders:
        uname = f"@{o['username']}" if o.get('username') else ""
        label = (f"#{o['id']} {uname} {o['pname'][:10]} ({o['size']}) — {order_status_text(o['status'])}")
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"orddetail_{o['id']}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text("📋 <b>Заказы</b>\n<blockquote>Нажмите для просмотра:</blockquote>",
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer("📋 <b>Заказы</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("orddetail_"))
async def cb_orddetail(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    oid   = int(cb.data.split("_")[1])
    order = await get_order(oid)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True)
        return
    product = await get_product(order['product_id'])

    uname_s = f"@{order['username']}" if order.get('username') else "—"
    promo_line = ""
    if order.get('promo_code'):
        promo_line = (f"🎟 <b>Промокод:</b> <code>{order['promo_code']}</code>\n"
                      f"💰 <b>Скидка:</b> {fmt_price(order.get('discount', 0))}\n")

    text = (
        f"📋 <b>Заказ #{oid}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('user')} <b>Покупатель:</b> {uname_s}\n"
        f"👤 <b>Имя:</b> {order.get('first_name') or '—'}\n"
        f"🆔 <b>TG ID:</b> <code>{order['user_id']}</code>\n"
        f"{ae('box')} <b>Товар:</b> {product['name'] if product else '—'}\n"
        f"{ae('size')} <b>Размер:</b> {order['size']}\n"
        f"{ae('money')} <b>Сумма:</b> {fmt_price(order['price'])}\n"
        f"{promo_line}"
        f"💳 <b>Оплата:</b> {order['method']}\n"
        f"{ae('phone')} <b>Телефон:</b> {order['phone'] or '—'}\n"
        f"{ae('pin')} <b>Адрес:</b> {order['address'] or '—'}\n"
        f"🔄 <b>Статус:</b> {order_status_text(order['status'])}\n"
        f"{ae('cal')} {order['created_at'][:16]}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Управление статусом", callback_data=f"ordstatus_{oid}")],
        [InlineKeyboardButton(text="💬 Написать покупателю",
                              callback_data=f"adm_msguser_{order['user_id']}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_orders")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ══════════════════════════════════════════════
#  ADMIN — Пользователи (новый раздел)
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_users")
async def cb_adm_users(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    users = await get_all_users(limit=20)
    kb_rows = []
    for u in users:
        ban_icon = "🚫" if u.get('is_banned') else "👤"
        uname    = f"@{u['username']}" if u.get('username') else str(u['user_id'])
        kb_rows.append([InlineKeyboardButton(
            text=f"{ban_icon} {uname} — {u.get('first_name','?')[:15]}",
            callback_data=f"adm_user_{u['user_id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="💬 Написать пользователю", callback_data="adm_msg_user")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "👥 <b>Пользователи</b>\n<blockquote>Последние 20 зарегистрированных:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer("👥 <b>Пользователи</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("adm_user_"))
async def cb_adm_user(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uid  = int(cb.data.split("_")[2])
    user = await get_user(uid)
    if not user:
        await cb.answer("Пользователь не найден", show_alert=True)
        return

    uname    = f"@{user['username']}" if user.get('username') else "—"
    ban_icon = "🚫 Заблокирован" if user.get('is_banned') else "✅ Активен"

    text = (
        f"👤 <b>Пользователь</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"💬 <b>Username:</b> {uname}\n"
        f"📛 <b>Имя:</b> {user.get('first_name','—')}\n"
        f"📱 <b>Телефон:</b> {user.get('phone','—')}\n"
        f"📍 <b>Адрес:</b> {user.get('default_address','—')}\n"
        f"🛍 <b>Заказов:</b> {user.get('total_purchases',0)}\n"
        f"💰 <b>Потрачено:</b> {fmt_price(user.get('total_spent',0))}\n"
        f"🎁 <b>Бонусов:</b> {fmt_price(user.get('bonus_balance',0))}\n"
        f"📅 <b>Регистрация:</b> {user.get('registered_at','—')[:10]}\n"
        f"🔒 <b>Статус:</b> {ban_icon}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

    if user.get('is_banned'):
        ban_btn = InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"adm_unban_{uid}")
    else:
        ban_btn = InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"adm_ban_{uid}")

    current_role = await get_user_role(uid)
    role_label = ROLES.get(current_role, current_role or "—")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ban_btn],
        [InlineKeyboardButton(text=f"👑 Роль: {role_label}", callback_data=f"adm_role_edit_{uid}")],
        [InlineKeyboardButton(text="💬 Написать пользователю",
                              callback_data=f"adm_msguser_{uid}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_users")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("adm_ban_"))
async def cb_adm_ban(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uid = int(cb.data.split("_")[2])
    await ban_user(uid)
    await log_event("ban", uid, f"by={cb.from_user.id}")
    try:
        await bot.send_message(uid, "🚫 <b>Вы заблокированы в этом боте.</b>", parse_mode="HTML")
    except Exception:
        pass
    await cb.answer("✅ Пользователь заблокирован", show_alert=True)
    # Обновляем карточку
    fake = types.CallbackQuery(
        id=cb.id, from_user=cb.from_user, message=cb.message,
        chat_instance=cb.chat_instance, data=f"adm_user_{uid}"
    )
    await cb_adm_user(fake)

@router.callback_query(F.data.startswith("adm_unban_"))
async def cb_adm_unban(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uid = int(cb.data.split("_")[2])
    await unban_user(uid)
    await log_event("unban", uid, f"by={cb.from_user.id}")
    try:
        await bot.send_message(uid, "✅ <b>Ваша блокировка снята. Добро пожаловать обратно!</b>", parse_mode="HTML")
    except Exception:
        pass
    await cb.answer("✅ Пользователь разблокирован", show_alert=True)
    fake = types.CallbackQuery(
        id=cb.id, from_user=cb.from_user, message=cb.message,
        chat_instance=cb.chat_instance, data=f"adm_user_{uid}"
    )
    await cb_adm_user(fake)

# ── Написать конкретному пользователю ──────────
@router.callback_query(F.data == "adm_msg_user")
async def cb_adm_msg_user(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.set_state(AdminSt.msg_user_id)
    try:
        await cb.message.edit_text(
            "💬 <b>Написать пользователю</b>\n\n"
            "<blockquote>Введите Telegram ID пользователя:</blockquote>",
            parse_mode="HTML", reply_markup=kb_back("adm_users")
        )
    except Exception:
        await cb.message.answer("💬 <b>Введите Telegram ID пользователя:</b>",
                                parse_mode="HTML", reply_markup=kb_back("adm_users"))
    await cb.answer()

@router.callback_query(F.data.startswith("adm_msguser_"))
async def cb_adm_msguser(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    uid = int(cb.data.split("_")[2])
    await state.update_data(msg_target_uid=uid)
    await state.set_state(AdminSt.msg_user_text)
    try:
        await cb.message.edit_text(
            f"💬 <b>Сообщение пользователю</b> <code>{uid}</code>\n\n"
            f"<blockquote>Введите текст сообщения (поддерживается HTML):</blockquote>",
            parse_mode="HTML", reply_markup=kb_back("adm_users")
        )
    except Exception:
        await cb.message.answer(f"💬 <b>Введите сообщение для {uid}:</b>",
                                parse_mode="HTML", reply_markup=kb_back("adm_users"))
    await cb.answer()

@router.message(AdminSt.msg_user_id)
async def proc_msg_user_id(msg: types.Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите числовой Telegram ID.")
        return
    await state.update_data(msg_target_uid=uid)
    await state.set_state(AdminSt.msg_user_text)
    await msg.answer(f"💬 <b>Сообщение для пользователя <code>{uid}</code></b>\n\n"
                     f"<blockquote>Введите текст:</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("adm_users"))

@router.message(AdminSt.msg_user_text)
async def proc_msg_user_text(msg: types.Message, state: FSMContext):
    d   = await state.get_data()
    uid = d.get('msg_target_uid')
    await state.clear()
    if not uid:
        await msg.answer("❌ Ошибка: ID не найден.", reply_markup=kb_admin_back())
        return
    try:
        sender = f"@{msg.from_user.username}" if msg.from_user.username else "Администратор"
        text_to_send = (
            f"📩 <b>Сообщение от администратора</b>\n\n"
            f"<blockquote>{msg.text}</blockquote>\n\n"
            f"<i>— {sender}</i>"
        )
        if msg.photo:
            await bot.send_photo(uid, msg.photo[-1].file_id, caption=text_to_send, parse_mode="HTML")
        elif msg.video:
            await bot.send_video(uid, msg.video.file_id, caption=text_to_send, parse_mode="HTML")
        else:
            await bot.send_message(uid, text_to_send, parse_mode="HTML")
        await msg.answer(f"✅ Сообщение отправлено пользователю <code>{uid}</code>",
                         parse_mode="HTML", reply_markup=kb_admin_back())
        await log_event("admin_msg", uid, f"by={msg.from_user.id}")
    except Exception as e:
        await msg.answer(f"❌ Не удалось отправить: {e}", reply_markup=kb_admin_back())

# ══════════════════════════════════════════════
#  ADMIN — Медиа
# ══════════════════════════════════════════════
MEDIA_SECTIONS = [
    ("main_menu",      "🏠 Главная"),
    ("shop_menu",      "🛒 Магазин/Каталог"),
    ("about_menu",     "🏬 О нас"),
    ("support_menu",   "❓ Поддержка"),
    ("profile_menu",   "👤 Профиль"),
    ("admin_panel",    "🎩 Админ панель"),
    ("orders_menu",    "📋 Заказы (админ)"),
    ("category_menu",  "📁 Категории"),
    ("payment_menu",   "💳 Оплата"),
    ("delivery_menu",  "🚚 Доставка"),
    ("promo_menu",     "🎟 Промокоды"),
    ("partnership_m",  "🤝 Партнёрство"),
    ("ad_menu",        "📢 Реклама"),
    ("broadcast_menu", "📨 Рассылка"),
    ("settings_menu",  "⚙️ Настройки"),
    ("review_menu",    "⭐ Отзывы"),
    ("bonus_menu",     "🎁 Бонусы"),
]

@router.callback_query(F.data == "adm_media")
async def cb_adm_media(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    kb_rows = []
    for key, label in MEDIA_SECTIONS:
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"smedia_{key}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "🖼 <b>Настройка медиа</b>\n\n"
            "<blockquote>Выберите раздел для прикрепления фото/видео/GIF:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer("🖼 <b>Настройка медиа</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("smedia_"))
async def cb_smedia(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    key     = cb.data[7:]
    await state.update_data(media_key=key)
    await state.set_state(AdminSt.set_media_file)
    current = await get_media(key)
    status  = "✅ Установлено" if current else "❌ Не установлено"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить медиа", callback_data=f"delmedia_{key}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_media")],
    ])
    try:
        await cb.message.edit_text(
            f"🖼 <b>Медиа для:</b> <code>{key}</code>\nТекущий статус: {status}\n\n"
            f"<b>Отправьте фото, видео (9:16 / 5:9) или GIF:</b>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer(f"🖼 <b>Отправьте медиа для {key}:</b>",
                                parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("delmedia_"))
async def cb_delmedia(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    key = cb.data[9:]
    await db_run('DELETE FROM media_settings WHERE key=$1', (key,))
    _cache_invalidate(f"media:{key}")
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
    await msg.answer(f"✅ Медиа для <code>{key}</code> установлено!",
                     parse_mode="HTML", reply_markup=kb_admin_back())

# ══════════════════════════════════════════════
#  ADMIN — Рассылка
# ══════════════════════════════════════════════
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
        await cb.message.answer("📨 <b>Рассылка</b>", parse_mode="HTML", reply_markup=kb)
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
                await bot.send_photo(uid, msg.photo[-1].file_id, caption=msg.caption, parse_mode="HTML")
            elif msg.video:
                await bot.send_video(uid, msg.video.file_id, caption=msg.caption, parse_mode="HTML")
            elif msg.animation:
                await bot.send_animation(uid, msg.animation.file_id, caption=msg.caption, parse_mode="HTML")
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

# ══════════════════════════════════════════════
#  ADMIN — Категории
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_cats")
async def cb_adm_cats(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cats    = await get_all_categories()
    kb_rows = []
    for c in cats:
        parent_mark = f" ↳" if c.get('parent_id', 0) else ""
        kb_rows.append([
            InlineKeyboardButton(text=f"📂{parent_mark} {c['name']}", callback_data=f"ecat_{c['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"dcat_{c['id']}"),
        ])
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить категорию", callback_data="addcat")])
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить подкатегорию", callback_data="addsubcat")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(f"{ae('folder')} <b>Категории</b>\n\n<blockquote>↳ = подкатегория</blockquote>",
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(f"{ae('folder')} <b>Категории</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data == "addsubcat")
async def cb_addsubcat(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    cats = await get_categories(parent_id=0)
    if not cats:
        await cb.answer("Сначала создайте родительскую категорию!", show_alert=True)
        return
    kb_rows = [[InlineKeyboardButton(text=f"📂 {c['name']}", callback_data=f"subcat_parent_{c['id']}")] for c in cats]
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_cats")])
    try:
        await cb.message.edit_text("📂 <b>Выберите родительскую категорию:</b>",
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer("📂 <b>Выберите родительскую категорию:</b>",
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("subcat_parent_"))
async def cb_subcat_parent(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    parent_id = int(cb.data.split("_")[2])
    await state.update_data(subcat_parent_id=parent_id)
    await state.set_state(AdminSt.add_cat_name)
    await state.update_data(is_subcat=True)
    try:
        await cb.message.edit_text("📂 <b>Введите название подкатегории:</b>",
                                   parse_mode="HTML",
                                   reply_markup=kb_back("addsubcat"))
    except Exception:
        await cb.message.answer("📂 <b>Введите название подкатегории:</b>", parse_mode="HTML")
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
        await cb.message.edit_text(f"{ae('folder')} <b>Новая категория</b>\n\n<blockquote>Введите название:</blockquote>",
                                   parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(f"{ae('folder')} <b>Новая категория</b>", parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.message(AdminSt.add_cat_name)
async def proc_cat_name(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    is_subcat = d.get('is_subcat', False)
    parent_id = d.get('subcat_parent_id', 0)
    await add_category(msg.text, parent_id=parent_id if is_subcat else 0)
    await state.clear()
    kind = "Подкатегория" if is_subcat else "Категория"
    await msg.answer(f"✅ {kind} добавлена!", reply_markup=kb_admin_back())

@router.callback_query(F.data.startswith("dcat_"))
async def cb_dcat(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[1])
    await del_category(cid)
    await cb.answer("✅ Категория удалена", show_alert=True)
    await cb_adm_cats(cb)

# ══════════════════════════════════════════════
#  ADMIN — Товары
# ══════════════════════════════════════════════
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
        await cb.message.edit_text("📦 <b>Товары</b>\n\n<blockquote>Выберите категорию:</blockquote>",
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer("📦 <b>Товары</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
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
            InlineKeyboardButton(text="✏️", callback_data=f"editprod_{p['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"dprod_{p['id']}"),
        ])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")])
    try:
        await cb.message.edit_text("<blockquote>📦 Товары категории:</blockquote>",
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer("<blockquote>📦 Товары:</blockquote>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
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
    text = (
        f"{ae('box')} <b>{p['name']}</b>\n\n"
        f"{p['description']}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('money')} <b>Цена:</b> {fmt_price(p['price'])}\n"
        f"{ae('size')} <b>Размеры:</b> {', '.join(sizes) or '—'}\n"
        f"{ae('box')} <b>Остаток:</b> {p['stock']} шт.\n"
        f"{ae('phone')} <b>Тел. продавца:</b> {p['seller_phone'] or '—'}\n"
        f"💬 <b>TG продавца:</b> {'@' + p['seller_username'] if p['seller_username'] else '—'}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"editprod_{pid}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ── Редактирование товара ──────────────────────
@router.callback_query(F.data.startswith("editprod_"))
async def cb_editprod(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    pid = int(cb.data.split("_")[1])
    p   = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📛 Название", callback_data=f"epf_{pid}_name"),
         InlineKeyboardButton(text="📝 Описание", callback_data=f"epf_{pid}_description")],
        [InlineKeyboardButton(text="💰 Цена", callback_data=f"epf_{pid}_price"),
         InlineKeyboardButton(text="📊 Остаток", callback_data=f"epf_{pid}_stock")],
        [InlineKeyboardButton(text="📐 Размеры", callback_data=f"epf_{pid}_sizes"),
         InlineKeyboardButton(text="📞 Тел. продавца", callback_data=f"epf_{pid}_seller_phone")],
        [InlineKeyboardButton(text="💬 TG продавца", callback_data=f"epf_{pid}_seller_username")],
        [InlineKeyboardButton(text="‹ Назад", callback_data=f"vprod_{pid}")],
    ])
    text = (f"✏️ <b>Редактирование: {p['name']}</b>\n\n"
            f"<blockquote>Выберите поле для изменения:</blockquote>")
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

EDIT_FIELD_LABELS = {
    'name':             ('📛 Название', 'Введите новое название:'),
    'description':      ('📝 Описание', 'Введите новое описание:'),
    'price':            ('💰 Цена', 'Введите новую цену в ₸:'),
    'stock':            ('📊 Остаток', 'Введите количество на складе:'),
    'sizes':            ('📐 Размеры', 'Введите размеры через запятую (или "нет"):'),
    'seller_phone':     ('📞 Телефон', 'Введите номер телефона продавца:'),
    'seller_username':  ('💬 TG username', 'Введите @username продавца (или "нет"):'),
}

@router.callback_query(F.data.startswith("epf_"))
async def cb_epf(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    parts = cb.data.split("_", 2)
    pid   = int(parts[1])
    field = parts[2]
    label, prompt = EDIT_FIELD_LABELS.get(field, (field, f"Введите значение для {field}:"))
    await state.update_data(edit_pid=pid, edit_field=field)
    await state.set_state(AdminSt.edit_prod_value)
    try:
        await cb.message.edit_text(
            f"✏️ <b>{label}</b>\n\n<blockquote>{prompt}</blockquote>",
            parse_mode="HTML",
            reply_markup=kb_back(f"editprod_{pid}")
        )
    except Exception:
        await cb.message.answer(f"✏️ <b>{prompt}</b>", parse_mode="HTML",
                                reply_markup=kb_back(f"editprod_{pid}"))
    await cb.answer()

@router.message(AdminSt.edit_prod_value)
async def proc_edit_prod_value(msg: types.Message, state: FSMContext):
    d     = await state.get_data()
    pid   = d.get('edit_pid')
    field = d.get('edit_field')
    await state.clear()

    raw = msg.text.strip()
    value = raw

    if field == 'price':
        try:
            value = float(raw.replace(',', '.').replace(' ', ''))
        except ValueError:
            await msg.answer("❌ Введите число.", reply_markup=kb_admin_back())
            return
    elif field == 'stock':
        try:
            value = int(raw)
        except ValueError:
            await msg.answer("❌ Введите целое число.", reply_markup=kb_admin_back())
            return
    elif field == 'sizes':
        if raw.lower() in ('нет', 'no', '-', '—'):
            value = '[]'
        else:
            sizes_list = [s.strip().upper() for s in raw.split(',') if s.strip()]
            value = json.dumps(sizes_list, ensure_ascii=False)
    elif field == 'seller_username':
        if raw.lower() in ('нет', 'no', '-', '—'):
            value = ''
        else:
            value = raw.lstrip('@')

    await update_product_field(pid, field, value)
    label = EDIT_FIELD_LABELS.get(field, (field,))[0]
    await msg.answer(f"✅ <b>{label} обновлено!</b>\n\nЗначение: <code>{value}</code>",
                     parse_mode="HTML", reply_markup=kb_admin_back())

@router.callback_query(F.data.startswith("dprod_"))
async def cb_dprod(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    pid = int(cb.data.split("_")[1])
    await del_product(pid)
    await cb.answer("✅ Товар удалён", show_alert=True)
    try:
        await cb.message.edit_text("✅ Товар удалён",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                       InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")
                                   ]]))
    except Exception:
        pass

# ══════════════════════════════════════════════
#  Добавление товара (9 шагов)
# ══════════════════════════════════════════════
@router.callback_query(F.data == "addprod")
async def cb_addprod(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    # Показываем ВСЕ категории включая подкатегории
    all_cats = await get_all_categories()
    if not all_cats:
        await cb.answer("Сначала создайте категорию!", show_alert=True)
        return
    kb = []
    for c in all_cats:
        parent_mark = "  ↳ " if c.get('parent_id', 0) else ""
        kb.append([InlineKeyboardButton(
            text=f"📂{parent_mark}{c['name']}",
            callback_data=f"npcat_{c['id']}"
        )])
    kb.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")])
    try:
        await cb.message.edit_text(
            "📦 <b>Новый товар</b>\n\n<blockquote>Шаг 1/9 — Выберите категорию (↳ = подкатегория):</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    except Exception:
        await cb.message.answer("📦 <b>Новый товар</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
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
        await cb.message.edit_text("📦 <b>Шаг 2/9 — Название товара</b>\n\n<blockquote>Введите название:</blockquote>",
                                   parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer("📦 <b>Шаг 2/9 — Название</b>", parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.message(AdminSt.add_prod_name)
async def proc_prod_name(msg: types.Message, state: FSMContext):
    name = msg.html_text if msg.entities else msg.text
    await state.update_data(name=name)
    await state.set_state(AdminSt.add_prod_desc)
    await msg.answer("📦 <b>Шаг 3/9 — Описание товара</b>\n\n<blockquote>Введите описание:</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_desc)
async def proc_prod_desc(msg: types.Message, state: FSMContext):
    desc = msg.html_text if msg.entities else msg.text
    await state.update_data(desc=desc)
    await state.set_state(AdminSt.add_prod_price)
    await msg.answer("📦 <b>Шаг 4/9 — Цена в тенге ₸</b>\n\n<blockquote>Введите цену (например: 5000):</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_price)
async def proc_prod_price(msg: types.Message, state: FSMContext):
    try:
        price = float(msg.text.replace(",", ".").replace(" ", ""))
    except ValueError:
        await msg.answer("❌ Введите число, например: <code>5000</code>", parse_mode="HTML")
        return
    await state.update_data(price=price)
    await state.set_state(AdminSt.add_prod_sizes)
    await msg.answer("📦 <b>Шаг 5/9 — Размеры</b>\n\n"
                     "<blockquote>Введите размеры через запятую:\n<i>Например: S, M, L, XL</i>\n\n"
                     "Нет размеров — напишите <b>нет</b></blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_sizes)
async def proc_prod_sizes(msg: types.Message, state: FSMContext):
    raw = msg.text.strip()
    if raw.lower() in ("нет", "no", "-", "—"):
        sizes_list = []
    else:
        sizes_list = [s.strip().upper() for s in raw.split(",") if s.strip()]
    await state.update_data(sizes=sizes_list)
    await state.set_state(AdminSt.add_prod_stock)
    await msg.answer("📦 <b>Шаг 6/9 — Остаток на складе</b>\n\n<blockquote>Введите количество (например: 10):</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_stock)
async def proc_prod_stock(msg: types.Message, state: FSMContext):
    try:
        stock = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите целое число.", parse_mode="HTML")
        return
    await state.update_data(stock=stock)
    await state.set_state(AdminSt.add_prod_seller_ph)
    await msg.answer("📦 <b>Шаг 7/9 — Телефон продавца</b>\n\n"
                     "<blockquote>Введите номер телефона продавца:\n<i>Пример: +7 701 234 56 78</i></blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_seller_ph)
async def proc_prod_seller_ph(msg: types.Message, state: FSMContext):
    await state.update_data(seller_phone=msg.text.strip())
    await state.set_state(AdminSt.add_prod_seller_un)
    await msg.answer("📦 <b>Шаг 7/9 — Telegram-юзернейм продавца</b>\n\n"
                     "<blockquote>Введите @username или напишите <b>нет</b>:</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_seller_un)
async def proc_prod_seller_un(msg: types.Message, state: FSMContext):
    raw = msg.text.strip()
    seller_un = ("" if raw.lower() in ("нет", "no", "-", "—") else raw.lstrip("@"))
    await state.update_data(seller_un=seller_un)
    await state.set_state(AdminSt.add_prod_card)
    await msg.answer("📦 <b>Шаг 8/9 — Карточка товара</b>\n\n"
                     "<blockquote>Отправьте фото или видео для карточки товара.\n"
                     "Рекомендуется формат <b>16:9</b>.\n\n"
                     "Напишите <b>нет</b> чтобы пропустить.</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_card,
                F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO]))
async def proc_prod_card_media(msg: types.Message, state: FSMContext):
    if msg.photo:
        fid, mt = msg.photo[-1].file_id, 'photo'
    else:
        fid, mt = msg.video.file_id, 'video'
    await state.update_data(card_fid=fid, card_mt=mt)
    await state.set_state(AdminSt.add_prod_gallery)
    await msg.answer("📦 <b>Шаг 9/9 — Галерея товара</b>\n\n"
                     "<blockquote>Отправьте ZIP-архив с фото и/или видео.\n\n"
                     "⚠️ Требования:\n"
                     "• Только .jpg, .jpeg, .png, .mp4\n"
                     "• Максимум 10 файлов\n\n"
                     "Напишите <b>нет</b> чтобы пропустить.</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("addprod"))

@router.message(AdminSt.add_prod_card, F.text)
async def proc_prod_card_skip(msg: types.Message, state: FSMContext):
    if msg.text.strip().lower() in ("нет", "no", "-", "—"):
        await state.update_data(card_fid='', card_mt='')
        await state.set_state(AdminSt.add_prod_gallery)
        await msg.answer("📦 <b>Шаг 9/9 — Галерея товара</b>\n\n"
                         "<blockquote>Отправьте ZIP-архив или напишите <b>нет</b>.</blockquote>",
                         parse_mode="HTML", reply_markup=kb_back("addprod"))
    else:
        await msg.answer("⚠️ Отправьте фото/видео или напишите <b>нет</b>.", parse_mode="HTML")

@router.message(AdminSt.add_prod_gallery, F.content_type == ContentType.DOCUMENT)
async def proc_prod_gallery_zip(msg: types.Message, state: FSMContext):
    status_msg = await msg.answer("⏳ Обрабатываю ZIP-архив...")
    try:
        file_info = await bot.get_file(msg.document.file_id)
        buf       = io.BytesIO()
        await bot.download_file(file_info.file_path, buf)
        buf.seek(0)

        gallery_files = []
        with zipfile.ZipFile(buf) as zf:
            names = [n for n in zf.namelist()
                     if n.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4'))
                     and not n.startswith('__MACOSX')][:10]
            for name in names:
                data = zf.read(name)
                ext  = name.lower().rsplit('.', 1)[-1]
                mt   = 'photo' if ext in ('jpg', 'jpeg', 'png') else 'video'
                try:
                    if mt == 'photo':
                        sent = await bot.send_photo(msg.chat.id,
                                                    types.BufferedInputFile(data, filename=name))
                        fid  = sent.photo[-1].file_id
                    else:
                        sent = await bot.send_video(msg.chat.id,
                                                    types.BufferedInputFile(data, filename=name))
                        fid  = sent.video.file_id
                    try:
                        await sent.delete()
                    except Exception:
                        pass
                    gallery_files.append({'file_id': fid, 'media_type': mt})
                except Exception as e:
                    await msg.answer(f"⚠️ Не удалось загрузить {name}: {e}")
    except zipfile.BadZipFile:
        await status_msg.edit_text("❌ Файл повреждён или не ZIP.")
        return

    await state.update_data(gallery=gallery_files)
    await _finish_add_product(msg, state, status_msg)

@router.message(AdminSt.add_prod_gallery, F.text)
async def proc_prod_gallery_skip(msg: types.Message, state: FSMContext):
    if msg.text.strip().lower() in ("нет", "no", "-", "—"):
        await state.update_data(gallery=[])
        await _finish_add_product(msg, state)
    else:
        await msg.answer("⚠️ Отправьте ZIP-архив или напишите <b>нет</b>.", parse_mode="HTML")

async def _finish_add_product(msg: types.Message, state: FSMContext, status_msg=None):
    d         = await state.get_data()
    seller_un = d.get('seller_un', '')
    gallery   = d.get('gallery', [])
    await add_product(
        d['cid'], d['name'], d['desc'], d['price'], d.get('sizes', []),
        d['stock'], seller_username=seller_un, seller_phone=d.get('seller_phone', ''),
        card_file_id=d.get('card_fid', ''), card_media_type=d.get('card_mt', ''),
        gallery=gallery
    )
    await state.clear()
    sizes_str = ', '.join(d.get('sizes', [])) or '—'
    result = (
        f"✅ <b>Товар добавлен!</b>\n\n"
        f"{ae('size')} Размеры: {sizes_str}\n"
        f"{ae('box')} Остаток: {d['stock']} шт.\n"
        f"{ae('money')} Цена: {fmt_price(d['price'])}\n"
        f"{ae('phone')} Продавец: {d.get('seller_phone', '—')}\n"
        f"💬 TG: {'@' + seller_un if seller_un else '—'}\n"
        f"🖼 Карточка: {'✅' if d.get('card_fid') else '—'}\n"
        f"📸 Галерея: {len(gallery)} фото/видео"
    )
    if status_msg:
        await status_msg.edit_text(result, parse_mode="HTML", reply_markup=kb_admin_back())
    else:
        await msg.answer(result, parse_mode="HTML", reply_markup=kb_admin_back())

# ══════════════════════════════════════════════
#  ПРОМОКОДЫ — Админ-панель
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_promos")
async def cb_adm_promos(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    promos  = await get_all_promos(active_only=False)
    kb_rows = []
    for p in promos:
        status_icon = "✅" if p['is_active'] else "❌"
        type_label  = PROMO_TYPES.get(p['promo_type'], p['promo_type'])
        usage       = f"{p['used_count']}"
        if p['max_uses'] > 0:
            usage += f"/{p['max_uses']}"
        kb_rows.append([InlineKeyboardButton(
            text=f"{status_icon} {p['code']} — {type_label} ({usage})",
            callback_data=f"vpromo_{p['id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="➕ Создать промокод", callback_data="addpromo")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            f"{ae('promo')} <b>Промокоды</b>\n\n<blockquote>Управление промокодами:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer(f"{ae('promo')} <b>Промокоды</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("vpromo_"))
async def cb_vpromo(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    pid   = int(cb.data.split("_")[1])
    promo = await get_promo_by_id(pid)
    if not promo:
        await cb.answer("Промокод не найден", show_alert=True)
        return

    type_label = PROMO_TYPES.get(promo['promo_type'], promo['promo_type'])
    status     = "✅ Активен" if promo['is_active'] else "❌ Деактивирован"
    usage      = f"{promo['used_count']}"
    if promo['max_uses'] > 0:
        usage += f" / {promo['max_uses']}"
    else:
        usage += " (безлимит)"

    if promo['promo_type'] == 'discount_percent':
        val_s = f"{int(promo['value'])}%"
    elif promo['promo_type'] in ('discount_fixed', 'cashback_bonus'):
        val_s = fmt_price(promo['value'])
    else:
        val_s = str(promo['value']) if promo['value'] else "—"

    text = (
        f"🎟 <b>Промокод: {promo['code']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Тип:</b> {type_label}\n"
        f"💰 <b>Значение:</b> {val_s}\n"
        f"📝 <b>Описание:</b> {promo['description'] or '—'}\n"
        f"📊 <b>Использований:</b> {usage}\n"
        f"🔄 <b>Статус:</b> {status}\n"
        f"{ae('cal')} <b>Создан:</b> {promo['created_at'][:16] if promo['created_at'] else '—'}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb_rows = []
    if promo['is_active']:
        kb_rows.append([InlineKeyboardButton(text="🗑 Деактивировать",
                                             callback_data=f"delpromo_{promo['id']}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_promos")])
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("delpromo_"))
async def cb_delpromo(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    pid = int(cb.data.split("_")[1])
    await delete_promo(pid)
    await cb.answer("✅ Промокод деактивирован", show_alert=True)
    await cb_adm_promos(cb)

@router.callback_query(F.data == "addpromo")
async def cb_addpromo(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.set_state(AdminSt.promo_code)
    try:
        await cb.message.edit_text(
            "🎟 <b>Создание промокода</b>\n\n"
            "<blockquote>Шаг 1/5 — Введите код промокода:\n<i>Например: SALE20, GIFT2024, VIP</i></blockquote>",
            parse_mode="HTML", reply_markup=kb_back("adm_promos")
        )
    except Exception:
        await cb.message.answer("🎟 <b>Введите код промокода:</b>",
                                parse_mode="HTML", reply_markup=kb_back("adm_promos"))
    await cb.answer()

@router.message(AdminSt.promo_code)
async def proc_promo_code(msg: types.Message, state: FSMContext):
    code = msg.text.strip().upper()
    if len(code) < 2 or len(code) > 30:
        await msg.answer("❌ Код должен быть от 2 до 30 символов.")
        return
    existing = await db_one('SELECT id FROM promocodes WHERE code=$1', (code,))
    if existing:
        await msg.answer("❌ Промокод с таким кодом уже существует. Введите другой:")
        return
    await state.update_data(promo_code=code)
    await state.set_state(AdminSt.promo_type)

    kb_rows = []
    for key, label in PROMO_TYPES.items():
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"ptype_{key}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_promos")])

    await msg.answer(
        f"🎟 <b>Промокод: {code}</b>\n\n<blockquote>Шаг 2/5 — Выберите тип промокода:</blockquote>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )

@router.callback_query(F.data.startswith("ptype_"), AdminSt.promo_type)
async def cb_ptype(cb: types.CallbackQuery, state: FSMContext):
    ptype = cb.data[6:]
    await state.update_data(promo_type=ptype)
    await state.set_state(AdminSt.promo_value)

    hints = {
        "discount_percent": "Введите размер скидки в процентах:\n<i>Например: 10, 20, 50</i>",
        "discount_fixed":   "Введите размер скидки в тенге ₸:\n<i>Например: 500, 1000, 2000</i>",
        "cashback_bonus":   "Введите размер бонуса в тенге ₸:\n<i>Например: 300, 500</i>",
        "gift":             "Введите описание подарка:\n<i>Например: Футболка в подарок</i>\n\nИли введите <b>0</b>.",
        "free_delivery":    "Введите <b>0</b> (бесплатная доставка не требует значения).",
        "special_offer":    "Введите значение скидки/бонуса или <b>0</b>:",
    }
    hint = hints.get(ptype, "Введите значение:")
    try:
        await cb.message.edit_text(
            f"🎟 <b>Шаг 3/5 — Значение</b>\n\n<blockquote>{hint}</blockquote>",
            parse_mode="HTML", reply_markup=kb_back("adm_promos")
        )
    except Exception:
        pass
    await cb.answer()

@router.message(AdminSt.promo_value)
async def proc_promo_value(msg: types.Message, state: FSMContext):
    d     = await state.get_data()
    ptype = d.get('promo_type', '')

    if ptype in ('gift', 'special_offer') and not msg.text.strip().replace('.', '').replace(',', '').isdigit():
        await state.update_data(promo_value=0, promo_gift_desc=msg.text.strip())
    else:
        try:
            val = float(msg.text.replace(",", ".").replace(" ", ""))
        except ValueError:
            await msg.answer("❌ Введите число.")
            return
        if ptype == 'discount_percent' and (val < 1 or val > 100):
            await msg.answer("❌ Процент должен быть от 1 до 100.")
            return
        await state.update_data(promo_value=val)

    await state.set_state(AdminSt.promo_description)
    await msg.answer("🎟 <b>Шаг 4/5 — Описание промокода</b>\n\n"
                     "<blockquote>Введите описание для покупателей:\n<i>Например: Скидка 20% на все товары!</i>\n\n"
                     "Или напишите <b>нет</b> чтобы пропустить.</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("adm_promos"))

@router.message(AdminSt.promo_description)
async def proc_promo_desc(msg: types.Message, state: FSMContext):
    raw  = msg.text.strip()
    desc = "" if raw.lower() in ("нет", "no", "-", "—") else raw
    d    = await state.get_data()
    if d.get('promo_gift_desc') and not desc:
        desc = d['promo_gift_desc']
    await state.update_data(promo_description=desc)
    await state.set_state(AdminSt.promo_max_uses)
    await msg.answer("🎟 <b>Шаг 5/5 — Лимит использований</b>\n\n"
                     "<blockquote>Введите максимальное количество использований:\n<i>Например: 100</i>\n\n"
                     "Введите <b>0</b> для безлимита.</blockquote>",
                     parse_mode="HTML", reply_markup=kb_back("adm_promos"))

@router.message(AdminSt.promo_max_uses)
async def proc_promo_max_uses(msg: types.Message, state: FSMContext):
    try:
        max_uses = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите целое число.")
        return
    if max_uses < 0:
        max_uses = 0

    d = await state.get_data()
    await state.clear()

    code  = d['promo_code']
    ptype = d['promo_type']
    value = d.get('promo_value', 0)
    desc  = d.get('promo_description', '')

    await create_promo(code, ptype, value, desc, max_uses)
    type_label = PROMO_TYPES.get(ptype, ptype)
    if ptype == 'discount_percent':
        val_s = f"{int(value)}%"
    elif ptype in ('discount_fixed', 'cashback_bonus'):
        val_s = fmt_price(value)
    else:
        val_s = str(value) if value else "—"
    usage_s = str(max_uses) if max_uses > 0 else "Безлимит"

    await msg.answer(
        f"✅ <b>Промокод создан!</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎟 <b>Код:</b> <code>{code}</code>\n"
        f"📋 <b>Тип:</b> {type_label}\n"
        f"💰 <b>Значение:</b> {val_s}\n"
        f"📝 <b>Описание:</b> {desc or '—'}\n"
        f"📊 <b>Лимит:</b> {usage_s}\n"
        f"━━━━━━━━━━━━━━━━━",
        parse_mode="HTML", reply_markup=kb_admin_back()
    )

# ══════════════════════════════════════════════
#  ADMIN — Настройки
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_settings")
async def cb_adm_settings(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Описание магазина", callback_data="edit_shop_info")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")],
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Назад", callback_data="adm_settings")
    ]])
    try:
        await cb.message.edit_text("📝 <b>Описание магазина</b>\n\n<blockquote>Введите новое описание:</blockquote>",
                                   parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer("📝 <b>Описание магазина</b>", parse_mode="HTML", reply_markup=kb)
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
    "adm_panel", "adm_media", "adm_cats", "adm_products", "addprod",
    "adm_settings", "ad_warning", "partnership", "about_back",
    "adm_promos", "support_back", "support_contacts", "adm_users",
    "adm_roles", "adm_partners", "adm_drops", "adm_botmsgs",
}

# ══════════════════════════════════════════════
#  ДРОПЫ — Пользователи
# ══════════════════════════════════════════════
@router.callback_query(F.data == "drops_menu")
async def cb_drops_menu(cb: types.CallbackQuery):
    active  = await get_active_drops()
    upcoming = await get_upcoming_drops()
    kb_rows = []
    drops_header = await get_bot_msg('drops_header')

    if active:
        for d in active:
            sizes = json.loads(d['sizes'] or '[]')
            sz = ', '.join(sizes) if sizes else '—'
            kb_rows.append([InlineKeyboardButton(
                text=f"🔥 {d['name']} · {fmt_price(d['price'])}",
                callback_data=f"drop_{d['id']}"
            )])
    if upcoming:
        for d in upcoming:
            start = d['start_at'][:16] if d.get('start_at') else '?'
            kb_rows.append([InlineKeyboardButton(
                text=f"⏳ {d['name']} — старт: {start}",
                callback_data=f"drop_{d['id']}"
            )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="shop")])
    try:
        await cb.message.edit_text(drops_header, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(drops_header, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("drop_"))
async def cb_drop_detail(cb: types.CallbackQuery):
    did = int(cb.data.split("_")[1])
    d   = await db_one('SELECT * FROM drops WHERE id=$1', (did,))
    if not d:
        await cb.answer("Дроп не найден", show_alert=True)
        return
    now = datetime.now().isoformat()
    sizes   = json.loads(d['sizes'] or '[]')
    sizes_s = ', '.join(sizes) if sizes else '—'
    is_live = d['start_at'] <= now
    status  = "🔥 Уже в продаже!" if is_live else f"⏳ Старт: {d['start_at'][:16]}"

    text = (
        f"🔥 <b>{d['name']}</b>\n\n"
        f"<blockquote>{d['description']}</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('money')} <b>Цена:</b> <code>{fmt_price(d['price'])}</code>\n"
        f"{ae('size')} <b>Размеры:</b> {sizes_s}\n"
        f"{ae('box')} <b>Статус:</b> {status}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb_rows = []
    if is_live and d['stock'] > 0:
        kb_rows.append([InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy_drop_{did}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="drops_menu")])
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

# ══════════════════════════════════════════════
#  ПАРТНЁРСКАЯ ПРОГРАММА — Пользователи
# ══════════════════════════════════════════════
@router.callback_query(F.data == "partner_program")
async def cb_partner_program(cb: types.CallbackQuery):
    uid     = cb.from_user.id
    partner = await get_partner(uid)
    header  = await get_bot_msg('partner_header')

    if partner:
        me = await bot.get_me()
        ref_url = f"https://t.me/{me.username}?start=ref_{partner['ref_code']}"
        try:
            bonus_new    = json.loads(partner['bonus_new'])
            bonus_repeat = json.loads(partner['bonus_repeat'])
        except Exception:
            bonus_new    = {"type": "discount_percent", "value": 5}
            bonus_repeat = {"type": "discount_percent", "value": 3}

        text = (
            f"🤝 <b>Партнёрская программа</b>\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔗 <b>Ваша реф-ссылка / промокод:</b>\n<code>{ref_url}</code>\n\n"
            f"👥 <b>Приглашено:</b> {partner['total_invited']}\n"
            f"💰 <b>Заработано:</b> {fmt_price(partner['total_earned'])}\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🎁 <b>Покупатели получат (новые):</b> {_fmt_buyer_bonus(bonus_new)}\n"
            f"🔄 <b>Покупатели получат (повторные):</b> {_fmt_buyer_bonus(bonus_repeat)}\n"
            f"━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Мои приглашённые", callback_data="partner_refs")],
            [InlineKeyboardButton(text="⚙️ Настроить бонусы", callback_data="partner_set_bonuses")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="profile_view")],
        ])
    else:
        text = (
            f"{header}\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>Зарегистрируйтесь как партнёр и получайте бонусы за каждого приглашённого покупателя!</blockquote>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Стать партнёром", callback_data="become_partner")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="profile_view")],
        ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "become_partner")
async def cb_become_partner(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(PartnerSt.choose_ref)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Сгенерировать автоматически", callback_data="partner_autoref")],
        [InlineKeyboardButton(text="✏️ Ввести свой код", callback_data="partner_customref")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="partner_program")],
    ])
    try:
        await cb.message.edit_text(
            "🔗 <b>Создание реф-ссылки</b>\n\n"
            "<blockquote>Выберите тип реферального кода:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception:
        await cb.message.answer("🔗 <b>Создание реф-ссылки</b>", parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "partner_autoref")
async def cb_partner_autoref(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    uid      = cb.from_user.id
    ref_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    ok = await create_partner(uid, ref_code)
    if not ok:
        await cb.answer("Ошибка: код уже занят, попробуйте ещё раз.", show_alert=True)
        return
    me  = await bot.get_me()
    url = f"https://t.me/{me.username}?start=ref_{ref_code}"
    await cb.message.edit_text(
        f"✅ <b>Партнёрский аккаунт создан!</b>\n\n"
        f"🔗 <b>Ваша ссылка:</b>\n<code>{url}</code>\n\n"
        f"<blockquote>Теперь настройте бонусы, которые <b>получат ваши покупатели</b> при первой и повторной покупке по вашей ссылке.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настроить бонусы покупателям", callback_data="partner_set_bonuses")],
            [InlineKeyboardButton(text="‹ Профиль", callback_data="profile_view")],
        ])
    )
    await cb.answer("✅ Готово!")

@router.callback_query(F.data == "partner_customref")
async def cb_partner_customref(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(PartnerSt.custom_ref)
    try:
        await cb.message.edit_text(
            "✏️ <b>Введите ваш реф-код (промокод)</b>\n\n"
            "<blockquote>Только латинские буквы и цифры, от 4 до 16 символов.\nПример: ALEX2024</blockquote>",
            parse_mode="HTML", reply_markup=kb_back("become_partner")
        )
    except Exception:
        pass
    await cb.answer()

@router.message(PartnerSt.custom_ref)
async def proc_partner_customref(msg: types.Message, state: FSMContext):
    await state.clear()
    code = msg.text.strip().upper()
    if not code.isalnum() or len(code) < 4 or len(code) > 16:
        await msg.answer("❌ Код должен содержать только буквы/цифры, от 4 до 16 символов.")
        return
    ok = await create_partner(msg.from_user.id, code)
    if not ok:
        await msg.answer("❌ Этот код уже занят. Попробуйте другой.")
        return
    me  = await bot.get_me()
    url = f"https://t.me/{me.username}?start=ref_{code}"
    await msg.answer(
        f"✅ <b>Партнёрский аккаунт создан!</b>\n\n"
        f"🔗 <b>Ваша ссылка:</b>\n<code>{url}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настроить бонусы покупателям", callback_data="partner_set_bonuses")],
            [InlineKeyboardButton(text="‹ Профиль", callback_data="profile_view")],
        ])
    )

# Бонусы, которые получат ПОКУПАТЕЛИ по реф-ссылке партнёра
BUYER_BONUS_OPTIONS_NEW = [
    ("Скидка 5% на первый заказ",  {"type": "discount_percent", "value": 5}),
    ("Скидка 10% на первый заказ", {"type": "discount_percent", "value": 10}),
    ("Скидка 15% на первый заказ", {"type": "discount_percent", "value": 15}),
    ("Кешбэк x2 (двойной)",        {"type": "cashback_x2",      "value": 2}),
    ("Бонус 500 ₸ на счёт",        {"type": "bonus_fixed",       "value": 500}),
]
BUYER_BONUS_OPTIONS_REPEAT = [
    ("Скидка 3% на повторный заказ",  {"type": "discount_percent", "value": 3}),
    ("Скидка 5% на повторный заказ",  {"type": "discount_percent", "value": 5}),
    ("Кешбэк x2 на повторный заказ",  {"type": "cashback_x2",      "value": 2}),
    ("Бонус 200 ₸ на счёт",           {"type": "bonus_fixed",       "value": 200}),
]

def _fmt_buyer_bonus(b: dict) -> str:
    t, v = b.get('type', ''), b.get('value', 0)
    if t == 'discount_percent': return f"Скидка {v}%"
    if t == 'cashback_x2':      return f"Кешбэк x{v}"
    if t == 'bonus_fixed':      return fmt_price(v) + " на бонусный счёт"
    return str(v)

@router.callback_query(F.data == "partner_set_bonuses")
async def cb_partner_set_bonuses(cb: types.CallbackQuery):
    kb_rows = []
    for label, val in BUYER_BONUS_OPTIONS_NEW:
        kb_rows.append([InlineKeyboardButton(
            text=f"🆕 {label}",
            callback_data=f"pbonus_new_{json.dumps(val, separators=(',', ':'))}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="partner_program")])
    try:
        await cb.message.edit_text(
            "⚙️ <b>Бонус для новых покупателей</b>\n\n"
            "<blockquote>Выберите, что получат <b>новые покупатели</b> (впервые), "
            "которые перейдут по вашей реф-ссылке и сделают заказ:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("pbonus_new_"))
async def cb_pbonus_new(cb: types.CallbackQuery):
    raw = cb.data[len("pbonus_new_"):]
    try:
        bonus_new = json.loads(raw)
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return
    kb_rows = []
    for label, val in BUYER_BONUS_OPTIONS_REPEAT:
        kb_rows.append([InlineKeyboardButton(
            text=f"🔄 {label}",
            callback_data=f"pbonus_rep_{json.dumps(val, separators=(',', ':'))}_{json.dumps(bonus_new, separators=(',', ':'))}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="partner_set_bonuses")])
    try:
        await cb.message.edit_text(
            "⚙️ <b>Бонус для повторных покупателей</b>\n\n"
            "<blockquote>Выберите, что получат <b>повторные покупатели</b>, "
            "которые снова делают заказ по вашей ссылке:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("pbonus_rep_"))
async def cb_pbonus_rep(cb: types.CallbackQuery):
    raw = cb.data[len("pbonus_rep_"):]
    # Разбираем два JSON-объекта: repeat_json _ new_json
    # Ищем границу между двумя JSON с конца
    try:
        brace_depth = 0
        split_pos = -1
        for i in range(len(raw) - 1, -1, -1):
            if raw[i] == '}': brace_depth += 1
            if raw[i] == '{': brace_depth -= 1
            if brace_depth == 0 and i > 0 and raw[i-1] == '_':
                split_pos = i - 1
                break
        if split_pos == -1:
            raise ValueError("split not found")
        bonus_repeat = json.loads(raw[:split_pos])
        bonus_new    = json.loads(raw[split_pos+1:])
    except Exception:
        await cb.answer("Ошибка разбора данных", show_alert=True)
        return
    uid = cb.from_user.id
    await update_partner_bonuses(uid, bonus_new, bonus_repeat)
    await cb.message.edit_text(
        f"✅ <b>Настройки сохранены!</b>\n\n"
        f"🆕 <b>Новым покупателям:</b> {_fmt_buyer_bonus(bonus_new)}\n"
        f"🔄 <b>Повторным покупателям:</b> {_fmt_buyer_bonus(bonus_repeat)}\n\n"
        f"<blockquote>Эти бонусы будут применяться автоматически для всех, "
        f"кто пришёл по вашей реф-ссылке.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="‹ Партнёрская программа", callback_data="partner_program")
        ]])
    )
    await cb.answer("✅ Сохранено!")

@router.callback_query(F.data == "partner_refs")
async def cb_partner_refs(cb: types.CallbackQuery):
    refs = await get_partner_referrals(cb.from_user.id, limit=20)
    if not refs:
        await cb.answer("Приглашённых пока нет", show_alert=True)
        return
    lines = []
    for r in refs:
        uname = f"@{r['username']}" if r.get('username') else str(r['referred_uid'])
        kind  = "🆕 Новый" if r['is_new_buyer'] else "🔄 Повторный"
        bonus = fmt_price(r['bonus_amount'])
        dt    = r['created_at'][:10] if r.get('created_at') else ''
        lines.append(f"{kind} {uname} | +{bonus} | {dt}")
    text = "📊 <b>Мои приглашённые</b>\n\n━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) + "\n━━━━━━━━━━━━━━━━━"
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=kb_back("partner_program"))
    except Exception:
        await cb.message.answer(text, parse_mode="HTML",
                                reply_markup=kb_back("partner_program"))
    await cb.answer()

# ══════════════════════════════════════════════
#  ADMIN — Роли
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_roles")
async def cb_adm_roles(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    rows = await db_all('SELECT ur.*, u.username, u.first_name FROM user_roles ur LEFT JOIN users u ON ur.user_id=u.user_id ORDER BY ur.granted_at DESC LIMIT 30')
    kb_rows = []
    for r in rows:
        uname = f"@{r['username']}" if r.get('username') else str(r['user_id'])
        rlabel = ROLES.get(r['role'], r['role'])
        kb_rows.append([InlineKeyboardButton(
            text=f"{rlabel} — {uname}",
            callback_data=f"adm_role_edit_{r['user_id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="➕ Назначить роль", callback_data="adm_role_assign")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "👑 <b>Управление ролями</b>\n\n<blockquote>Только владелец (admin) может менять роли.</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer("👑 <b>Управление ролями</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data == "adm_role_assign")
async def cb_adm_role_assign(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    await state.set_state(AdminSt.role_user_id)
    try:
        await cb.message.edit_text(
            "👑 <b>Назначить роль</b>\n\n<blockquote>Введите Telegram ID пользователя:</blockquote>",
            parse_mode="HTML", reply_markup=kb_back("adm_roles")
        )
    except Exception:
        pass
    await cb.answer()

@router.message(AdminSt.role_user_id)
async def proc_role_user_id(msg: types.Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите числовой Telegram ID.")
        return
    user = await get_user(uid)
    if not user:
        await msg.answer("❌ Пользователь не найден в базе.")
        return
    await state.update_data(role_target_uid=uid)
    await state.clear()
    uname = f"@{user['username']}" if user.get('username') else str(uid)
    current_role = await get_user_role(uid)
    kb_rows = []
    for role_key, role_label in ROLES.items():
        mark = "✓ " if current_role == role_key else ""
        kb_rows.append([InlineKeyboardButton(
            text=f"{mark}{role_label}",
            callback_data=f"setrole_{uid}_{role_key}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_roles")])
    await msg.answer(
        f"👤 <b>{uname}</b>\nТекущая роль: {ROLES.get(current_role, current_role)}\n\n"
        f"<blockquote>Выберите новую роль:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )

@router.callback_query(F.data.startswith("setrole_"))
async def cb_setrole(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    parts   = cb.data.split("_", 2)
    uid     = int(parts[1])
    role    = parts[2]
    await set_user_role(uid, role, cb.from_user.id)
    # If partner role — also create partner record if not exists
    if role == 'partner':
        existing = await get_partner(uid)
        if not existing:
            auto_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            await create_partner(uid, auto_code)
    rlabel = ROLES.get(role, role)
    try:
        await bot.send_message(uid,
            f"👑 <b>Ваша роль изменена</b>\n\nНовая роль: {rlabel}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer(f"✅ Роль {rlabel} назначена", show_alert=True)
    # Возвращаем на карточку пользователя
    fake = types.CallbackQuery(
        id=cb.id, from_user=cb.from_user, message=cb.message,
        chat_instance=cb.chat_instance, data=f"adm_user_{uid}"
    )
    await cb_adm_user(fake)

@router.callback_query(F.data.startswith("adm_role_edit_"))
async def cb_adm_role_edit(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uid  = int(cb.data.split("_")[3])
    user = await get_user(uid)
    current_role = await get_user_role(uid)
    uname = f"@{user['username']}" if user and user.get('username') else str(uid)
    kb_rows = []
    for role_key, role_label in ROLES.items():
        mark = "✓ " if current_role == role_key else ""
        kb_rows.append([InlineKeyboardButton(
            text=f"{mark}{role_label}",
            callback_data=f"setrole_{uid}_{role_key}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад к пользователю", callback_data=f"adm_user_{uid}")])
    try:
        await cb.message.edit_text(
            f"👤 <b>{uname}</b>\nТекущая роль: {ROLES.get(current_role, current_role)}\n\n"
            f"<blockquote>Выберите новую роль:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        pass
    await cb.answer()

# ══════════════════════════════════════════════
#  ADMIN — Партнёры
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_partners")
async def cb_adm_partners(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    partners = await db_all(
        '''SELECT p.*, u.username, u.first_name FROM partners p
           LEFT JOIN users u ON p.user_id=u.user_id
           ORDER BY p.total_earned DESC LIMIT 30'''
    )
    kb_rows = []
    for p in partners:
        uname = f"@{p['username']}" if p.get('username') else str(p['user_id'])
        kb_rows.append([InlineKeyboardButton(
            text=f"🤝 {uname} — {p['ref_code']} | {fmt_price(p['total_earned'])}",
            callback_data=f"adm_partner_{p['user_id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "🤝 <b>Партнёры</b>\n\n<blockquote>Нажмите для управления:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer("🤝 <b>Партнёры</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("adm_partner_"))
async def cb_adm_partner_detail(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uid  = int(cb.data.split("_")[2])
    p    = await get_partner(uid)
    user = await get_user(uid)
    if not p:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    uname = f"@{user['username']}" if user and user.get('username') else str(uid)
    try:
        bonus_new    = json.loads(p['bonus_new'])
        bonus_repeat = json.loads(p['bonus_repeat'])
    except Exception:
        bonus_new    = {"type": "percent", "value": 5}
        bonus_repeat = {"type": "percent", "value": 3}

    def fmt_b(b):
        return f"{b['value']}%" if b['type'] == 'percent' else fmt_price(b['value'])

    text = (
        f"🤝 <b>Партнёр: {uname}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>Реф-код:</b> <code>{p['ref_code']}</code>\n"
        f"👥 <b>Приглашено:</b> {p['total_invited']}\n"
        f"💰 <b>Заработано:</b> {fmt_price(p['total_earned'])}\n"
        f"🆕 <b>Бонус (новый):</b> {fmt_b(bonus_new)}\n"
        f"🔄 <b>Бонус (повторный):</b> {fmt_b(bonus_repeat)}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить бонус (новый)",
                              callback_data=f"adm_pbon_new_{uid}")],
        [InlineKeyboardButton(text="✏️ Изменить бонус (повторный)",
                              callback_data=f"adm_pbon_rep_{uid}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data="adm_partners")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("adm_pbon_new_"))
async def cb_adm_pbon_new(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uid  = int(cb.data.split("_")[3])
    kb_rows = []
    for label, val in BONUS_OPTIONS_NEW:
        kb_rows.append([InlineKeyboardButton(
            text=f"🆕 {label}",
            callback_data=f"adm_pset_new_{uid}_{json.dumps(val)}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=f"adm_partner_{uid}")])
    try:
        await cb.message.edit_text("✏️ <b>Бонус за нового покупателя:</b>",
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("adm_pbon_rep_"))
async def cb_adm_pbon_rep(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    uid  = int(cb.data.split("_")[3])
    kb_rows = []
    for label, val in BONUS_OPTIONS_REPEAT:
        kb_rows.append([InlineKeyboardButton(
            text=f"🔄 {label}",
            callback_data=f"adm_pset_rep_{uid}_{json.dumps(val)}"
        )])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=f"adm_partner_{uid}")])
    try:
        await cb.message.edit_text("✏️ <b>Бонус за повторного покупателя:</b>",
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("adm_pset_new_"))
async def cb_adm_pset_new(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    raw  = cb.data[len("adm_pset_new_"):]
    idx  = raw.find("_")
    uid  = int(raw[:idx])
    val  = json.loads(raw[idx+1:])
    p    = await get_partner(uid)
    if not p:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    try:
        current_repeat = json.loads(p['bonus_repeat'])
    except Exception:
        current_repeat = {"type": "percent", "value": 3}
    await update_partner_bonuses(uid, val, current_repeat)
    await cb.answer("✅ Бонус обновлён", show_alert=True)
    await cb_adm_partner_detail(cb)

@router.callback_query(F.data.startswith("adm_pset_rep_"))
async def cb_adm_pset_rep(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    raw  = cb.data[len("adm_pset_rep_"):]
    idx  = raw.find("_")
    uid  = int(raw[:idx])
    val  = json.loads(raw[idx+1:])
    p    = await get_partner(uid)
    if not p:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    try:
        current_new = json.loads(p['bonus_new'])
    except Exception:
        current_new = {"type": "percent", "value": 5}
    await update_partner_bonuses(uid, current_new, val)
    await cb.answer("✅ Бонус обновлён", show_alert=True)
    await cb_adm_partner_detail(cb)

# ══════════════════════════════════════════════
#  ADMIN — Дропы
# ══════════════════════════════════════════════
@router.callback_query(F.data == "adm_drops")
async def cb_adm_drops(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    drops = await get_all_drops_admin()
    kb_rows = []
    for d in drops:
        now    = datetime.now().isoformat()
        status = "🔥" if (d['is_active'] and d['start_at'] <= now) else ("⏳" if d['is_active'] else "❌")
        kb_rows.append([
            InlineKeyboardButton(text=f"{status} {d['name']}", callback_data=f"adm_drop_{d['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"del_drop_{d['id']}"),
        ])
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить дроп", callback_data="add_drop")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "🔥 <b>Дропы</b>\n\n<blockquote>🔥 = активен | ⏳ = ожидает старта | ❌ = скрыт</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer("🔥 <b>Дропы</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("del_drop_"))
async def cb_del_drop(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    did = int(cb.data.split("_")[2])
    await del_drop(did)
    await cb.answer("✅ Дроп удалён", show_alert=True)
    await cb_adm_drops(cb)

@router.callback_query(F.data == "add_drop")
async def cb_add_drop(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    cats = await get_all_categories()
    kb_rows = [[InlineKeyboardButton(text=f"📂 {c['name']}", callback_data=f"drop_cat_{c['id']}")] for c in cats]
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_drops")])
    try:
        await cb.message.edit_text(
            "🔥 <b>Новый дроп</b>\n\n<blockquote>Шаг 1 — Выберите категорию (или создайте без категории):</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("drop_cat_"))
async def cb_drop_cat(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[2])
    await state.update_data(drop_cid=cid)
    await state.set_state(AdminSt.add_drop_name)
    try:
        await cb.message.edit_text("🔥 <b>Шаг 2 — Название дропа:</b>",
                                   parse_mode="HTML", reply_markup=kb_back("add_drop"))
    except Exception:
        pass
    await cb.answer()

@router.message(AdminSt.add_drop_name)
async def proc_drop_name(msg: types.Message, state: FSMContext):
    await state.update_data(drop_name=msg.text.strip())
    await state.set_state(AdminSt.add_drop_desc)
    await msg.answer("🔥 <b>Шаг 3 — Описание дропа:</b>", parse_mode="HTML", reply_markup=kb_back("add_drop"))

@router.message(AdminSt.add_drop_desc)
async def proc_drop_desc(msg: types.Message, state: FSMContext):
    await state.update_data(drop_desc=msg.text.strip())
    await state.set_state(AdminSt.add_drop_price)
    await msg.answer("🔥 <b>Шаг 4 — Цена в ₸:</b>", parse_mode="HTML")

@router.message(AdminSt.add_drop_price)
async def proc_drop_price(msg: types.Message, state: FSMContext):
    try:
        price = float(msg.text.replace(',', '.').replace(' ', ''))
    except ValueError:
        await msg.answer("❌ Введите число.")
        return
    await state.update_data(drop_price=price)
    await state.set_state(AdminSt.add_drop_sizes)
    await msg.answer("🔥 <b>Шаг 5 — Размеры (через запятую или «нет»):</b>", parse_mode="HTML")

@router.message(AdminSt.add_drop_sizes)
async def proc_drop_sizes(msg: types.Message, state: FSMContext):
    raw = msg.text.strip()
    if raw.lower() in ('нет', 'no', '-'):
        sizes = []
    else:
        sizes = [s.strip().upper() for s in raw.split(',') if s.strip()]
    await state.update_data(drop_sizes=sizes)
    await state.set_state(AdminSt.add_drop_stock)
    await msg.answer("🔥 <b>Шаг 6 — Количество (остаток):</b>", parse_mode="HTML")

@router.message(AdminSt.add_drop_stock)
async def proc_drop_stock(msg: types.Message, state: FSMContext):
    try:
        stock = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Введите целое число.")
        return
    await state.update_data(drop_stock=stock)
    await state.set_state(AdminSt.add_drop_start_at)
    await msg.answer(
        "🔥 <b>Шаг 7 — Дата и время старта продаж</b>\n\n"
        "<blockquote>Формат: ДД.ММ.ГГГГ ЧЧ:ММ\nПример: 25.12.2025 12:00</blockquote>",
        parse_mode="HTML"
    )

@router.message(AdminSt.add_drop_start_at)
async def proc_drop_start(msg: types.Message, state: FSMContext):
    raw = msg.text.strip()
    try:
        dt = datetime.strptime(raw, "%d.%m.%Y %H:%M")
        start_at = dt.isoformat()
    except ValueError:
        await msg.answer("❌ Неверный формат. Введите: ДД.ММ.ГГГГ ЧЧ:ММ")
        return
    await state.update_data(drop_start_at=start_at)
    await state.set_state(AdminSt.add_drop_card)
    await msg.answer(
        "🔥 <b>Шаг 8 — Фото/видео дропа</b>\n\n"
        "<blockquote>Отправьте фото или напишите <b>нет</b>.</blockquote>",
        parse_mode="HTML"
    )

@router.message(AdminSt.add_drop_card, F.photo | F.video)
async def proc_drop_card_media(msg: types.Message, state: FSMContext):
    if msg.photo:
        fid, mt = msg.photo[-1].file_id, 'photo'
    else:
        fid, mt = msg.video.file_id, 'video'
    await state.update_data(drop_card_fid=fid, drop_card_mt=mt)
    await _finish_add_drop(msg, state)

@router.message(AdminSt.add_drop_card, F.text)
async def proc_drop_card_skip(msg: types.Message, state: FSMContext):
    if msg.text.strip().lower() in ('нет', 'no', '-'):
        await state.update_data(drop_card_fid='', drop_card_mt='')
        await _finish_add_drop(msg, state)
    else:
        await msg.answer("⚠️ Отправьте фото/видео или напишите «нет».")

async def _finish_add_drop(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    did = await add_drop(
        d.get('drop_cid', 0), d['drop_name'], d['drop_desc'], d['drop_price'],
        d.get('drop_sizes', []), d['drop_stock'], d['drop_start_at'],
        d.get('drop_card_fid', ''), d.get('drop_card_mt', '')
    )
    start_fmt = d['drop_start_at'][:16].replace('T', ' ')
    await msg.answer(
        f"✅ <b>Дроп добавлен!</b>\n\n"
        f"🔥 <b>{d['drop_name']}</b>\n"
        f"💰 {fmt_price(d['drop_price'])}\n"
        f"⏰ Старт: {start_fmt}",
        parse_mode="HTML", reply_markup=kb_admin_back()
    )

# ══════════════════════════════════════════════
#  ADMIN — Редактирование сообщений бота
# ══════════════════════════════════════════════
BOT_MSG_KEYS_LABELS = {
    'welcome':        '👋 Приветствие (/start)',
    'catalog_header': '🛒 Заголовок каталога',
    'profile_header': '👤 Заголовок профиля',
    'support_header': '❓ Заголовок поддержки',
    'about_header':   '🏬 О магазине',
    'order_confirm':  '🎉 Подтверждение заказа',
    'payment_wait':   '⏳ Ожидание оплаты',
    'drops_header':   '🔥 Заголовок дропов',
    'partner_header': '🤝 Партнёрская программа',
}

@router.callback_query(F.data == "adm_botmsgs")
async def cb_adm_botmsgs(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id):
        return
    kb_rows = []
    for key, label in BOT_MSG_KEYS_LABELS.items():
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"edit_botmsg_{key}")])
    kb_rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "💬 <b>Редактирование сообщений бота</b>\n\n"
            "<blockquote>Выберите сообщение для редактирования:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception:
        await cb.message.answer("💬 <b>Сообщения бота</b>", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data.startswith("edit_botmsg_"))
async def cb_edit_botmsg(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id):
        return
    key   = cb.data[len("edit_botmsg_"):]
    label = BOT_MSG_KEYS_LABELS.get(key, key)
    current = await get_bot_msg(key)
    await state.update_data(botmsg_key=key)
    await state.set_state(AdminSt.bot_msg_text)
    try:
        await cb.message.edit_text(
            f"✏️ <b>{label}</b>\n\n"
            f"<blockquote>Текущее сообщение:\n<i>{current[:300]}</i></blockquote>\n\n"
            f"Введите новый текст (HTML-разметка поддерживается).\n"
            f"Напишите <b>сброс</b> для возврата к дефолтному.",
            parse_mode="HTML", reply_markup=kb_back("adm_botmsgs")
        )
    except Exception:
        await cb.message.answer(f"✏️ <b>{label}</b>\n\nВведите новый текст:", parse_mode="HTML")
    await cb.answer()

@router.message(AdminSt.bot_msg_text)
async def proc_bot_msg_text(msg: types.Message, state: FSMContext):
    d   = await state.get_data()
    key = d.get('botmsg_key', '')
    await state.clear()
    raw = msg.text.strip()
    if raw.lower() in ('сброс', 'reset'):
        await db_run('DELETE FROM bot_messages WHERE key=$1', (key,))
        await msg.answer("✅ Сообщение сброшено к дефолтному.", reply_markup=kb_admin_back())
        return
    await set_bot_msg(key, raw)
    label = BOT_MSG_KEYS_LABELS.get(key, key)
    await msg.answer(f"✅ <b>{label}</b> обновлено!", parse_mode="HTML", reply_markup=kb_admin_back())

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
    print("\033[35m" + "═" * 50)
    print("  🛍  SHOPBOT — Шымкент, Казахстан")
    print("  🗄  PostgreSQL (asyncpg)")
    print("═" * 50 + "\033[0m")
    print(f"  💱 Курс USD/KZT: {USD_KZT_RATE} (фикс.)")
    print(f"  🎁 Кэшбэк: {CASHBACK_PERCENT}%")
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
