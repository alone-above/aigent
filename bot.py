import asyncio
import logging
import os
import aiosqlite
import aiohttp
import ssl
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ContentType, BotCommand, BotCommandScopeChat
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN= os.getenv("CRYPTOBOT_TOKEN")
ADMIN_IDS      = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support")
SHOP_NAME      = os.getenv("SHOP_NAME", "Digital Shop")
KASPI_PHONE    = os.getenv("KASPI_PHONE", "+7XXXXXXXXXX")
MANAGER_ID     = int(os.getenv("MANAGER_ID", str(ADMIN_IDS[0])))

bot     = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)
router  = Router()
dp.include_router(router)

DB_PATH = "shop.db"

# ══════════════════════════════════════════════
#  Анимированные эмодзи — ТОЛЬКО в тексте
#  В кнопках (InlineKeyboardButton.text) —
#  только обычные эмодзи!
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
}
def ae(k): return AE.get(k, "")

# ══════════════════════════════════════════════
#  FSM States
# ══════════════════════════════════════════════
class AdminSt(StatesGroup):
    broadcast      = State()
    set_media_file = State()
    add_cat_name   = State()
    add_prod_cat   = State()
    add_prod_name  = State()
    add_prod_desc  = State()
    add_prod_price = State()
    add_prod_type  = State()
    add_prod_text  = State()
    add_prod_file  = State()
    edit_shop_info = State()

# ══════════════════════════════════════════════
#  Database
# ══════════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                username     TEXT,
                first_name   TEXT,
                total_purchases INTEGER DEFAULT 0,
                total_spent  REAL DEFAULT 0,
                registered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name        TEXT NOT NULL,
                description TEXT,
                price       REAL NOT NULL,
                ptype       TEXT DEFAULT 'text',
                content     TEXT,
                file_id     TEXT,
                is_active   INTEGER DEFAULT 1,
                created_at  TEXT,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );
            CREATE TABLE IF NOT EXISTS purchases (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
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
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                product_id INTEGER,
                invoice_id TEXT UNIQUE,
                amount     REAL,
                status     TEXT DEFAULT 'pending',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS kaspi_payments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                product_id     INTEGER,
                amount         REAL,
                status         TEXT DEFAULT 'pending',
                manager_msg_id INTEGER DEFAULT 0,
                created_at     TEXT
            );
        ''')
        await db.commit()

# ── helpers ──────────────────────────────────
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

# ── user ─────────────────────────────────────
async def ensure_user(u: types.User):
    await db_run(
        'INSERT OR IGNORE INTO users (user_id,username,first_name,registered_at) VALUES(?,?,?,?)',
        (u.id, u.username, u.first_name, datetime.now().isoformat())
    )

async def get_user(uid): return await db_one('SELECT * FROM users WHERE user_id=?', (uid,))

# ── categories ───────────────────────────────
async def get_categories():  return await db_all('SELECT * FROM categories ORDER BY id')
async def add_category(n):   await db_run('INSERT INTO categories(name) VALUES(?)', (n,))
async def del_category(cid):
    await db_run('UPDATE products SET is_active=0 WHERE category_id=?', (cid,))
    await db_run('DELETE FROM categories WHERE id=?', (cid,))

# ── products ─────────────────────────────────
async def get_products(cid):
    return await db_all('SELECT * FROM products WHERE category_id=? AND is_active=1', (cid,))

async def get_product(pid):  return await db_one('SELECT * FROM products WHERE id=?', (pid,))

async def add_product(cid, name, desc, price, ptype, content=None, file_id=None):
    await db_run(
        'INSERT INTO products(category_id,name,description,price,ptype,content,file_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (cid, name, desc, price, ptype, content, file_id, datetime.now().isoformat())
    )

async def del_product(pid):  await db_run('UPDATE products SET is_active=0 WHERE id=?', (pid,))

# ── purchases ────────────────────────────────
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

# ── media / settings ─────────────────────────
async def set_media(key, mtype, fid):
    await db_run('INSERT OR REPLACE INTO media_settings(key,media_type,file_id) VALUES(?,?,?)', (key,mtype,fid))

async def get_media(key):   return await db_one('SELECT * FROM media_settings WHERE key=?', (key,))

async def set_setting(k,v): await db_run('INSERT OR REPLACE INTO shop_settings(key,value) VALUES(?,?)', (k,v))
async def get_setting(k, default=''):
    r = await db_one('SELECT value FROM shop_settings WHERE key=?', (k,))
    return r['value'] if r else default

# ── stats ────────────────────────────────────
async def get_stats():
    uc = (await db_one('SELECT COUNT(*) c FROM users'))['c']
    pc = (await db_one('SELECT COUNT(*) c FROM purchases'))['c']
    rv = (await db_one('SELECT COALESCE(SUM(price),0) s FROM purchases'))['s']
    ac = (await db_one('SELECT COUNT(*) c FROM products WHERE is_active=1'))['c']
    return uc, pc, rv, ac

async def all_user_ids():
    rows = await db_all('SELECT user_id FROM users')
    return [r['user_id'] for r in rows]

# ── crypto payments ──────────────────────────
async def save_crypto(uid, pid, inv_id, amount):
    await db_run(
        'INSERT OR IGNORE INTO crypto_payments(user_id,product_id,invoice_id,amount,created_at) VALUES(?,?,?,?,?)',
        (uid, pid, inv_id, amount, datetime.now().isoformat())
    )

async def get_crypto(inv_id): return await db_one('SELECT * FROM crypto_payments WHERE invoice_id=?', (inv_id,))
async def set_crypto_paid(inv_id): await db_run('UPDATE crypto_payments SET status=? WHERE invoice_id=?', ('paid', inv_id))

# ── kaspi payments ───────────────────────────
async def save_kaspi(uid, pid, amount):
    return await db_insert(
        'INSERT INTO kaspi_payments(user_id,product_id,amount,created_at) VALUES(?,?,?,?)',
        (uid, pid, amount, datetime.now().isoformat())
    )

async def get_kaspi(kid):    return await db_one('SELECT * FROM kaspi_payments WHERE id=?', (kid,))
async def set_kaspi_status(kid, status, mgr_mid=None):
    if mgr_mid is not None:
        await db_run('UPDATE kaspi_payments SET status=?,manager_msg_id=? WHERE id=?', (status, mgr_mid, kid))
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

async def create_invoice(amount, desc, payload):
    url = "https://pay.crypt.bot/api/createInvoice"
    hdr = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    me = await bot.get_me()
    data = {"asset":"USDT","amount":str(amount),"description":desc,"payload":payload,
            "paid_btn_name":"callback","paid_btn_url":f"https://t.me/{me.username}"}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_ctx())) as s:
        async with s.post(url, headers=hdr, json=data) as r:
            res = await r.json()
            return res["result"] if res.get("ok") else None

async def check_invoice(inv_id):
    url = "https://pay.crypt.bot/api/getInvoices"
    hdr = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_ctx())) as s:
        async with s.get(url, headers=hdr, params={"invoice_ids": inv_id}) as r:
            res = await r.json()
            if res.get("ok") and res["result"]["items"]:
                return res["result"]["items"][0]
    return None

# ══════════════════════════════════════════════
#  Keyboards
# ══════════════════════════════════════════════
def kb_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛒 Купить"),    KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏬 О магазине"), KeyboardButton(text="❓ Поддержка")],
    ], resize_keyboard=True)

def kb_back(cd="main"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data=cd)]])

def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика",   callback_data="adm_stats")],
        [InlineKeyboardButton(text="🖼 Медиа",        callback_data="adm_media"),
         InlineKeyboardButton(text="📨 Рассылка",     callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="📦 Товары",       callback_data="adm_products"),
         InlineKeyboardButton(text="📁 Категории",    callback_data="adm_cats")],
        [InlineKeyboardButton(text="⚙️ Настройки",    callback_data="adm_settings")],
    ])

def kb_admin_back():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Админ панель", callback_data="adm_panel")]])

# ══════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════
async def send_media(chat_id, text, key, markup=None):
    m = await get_media(key)
    if m:
        mt = m["media_type"]
        if mt == "photo":
            await bot.send_photo(chat_id, m["file_id"], caption=text, parse_mode="HTML", reply_markup=markup)
        elif mt == "video":
            await bot.send_video(chat_id, m["file_id"], caption=text, parse_mode="HTML", reply_markup=markup)
        elif mt == "animation":
            await bot.send_animation(chat_id, m["file_id"], caption=text, parse_mode="HTML", reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

async def set_cmds(uid):
    cmds = [BotCommand(command="start", description="🚀 Старт")]
    if uid in ADMIN_IDS:
        cmds.append(BotCommand(command="admin", description="🎩 Панель"))
    await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))

async def deliver(uid, product):
    """Выдать товар пользователю."""
    name = product['name']
    text = (
        f"✅ <b>Оплата подтверждена!</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Товар:</b> {name}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
    )
    if product['ptype'] == 'text':
        text += f"<blockquote>{product['content']}</blockquote>"
        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb_back("shop"))
    else:
        await bot.send_message(uid, text, parse_mode="HTML")
        await bot.send_document(uid, product['file_id'], caption="📎 Ваш файл", reply_markup=kb_back("shop"))

def fmt_dt():
    return datetime.now().strftime("%d.%m.%Y %H:%M")

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
        f"<blockquote>{ae('down')} Выберите нужный раздел:</blockquote>"
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=kb_main())

@router.message(Command("admin"))
async def cmd_admin(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS: return
    await state.clear()
    await msg.answer(
        "🎩 <b>Панель управления</b>",
        parse_mode="HTML", reply_markup=kb_admin()
    )

@router.callback_query(F.data == "main")
async def cb_main(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try: await cb.message.delete()
    except: pass
    text = (
        f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n"
        f"<blockquote>{ae('down')} Выберите нужный раздел:</blockquote>"
    )
    await bot.send_message(cb.from_user.id, text, parse_mode="HTML", reply_markup=kb_main())
    await cb.answer()

# ══════════════════════════════════════════════
#  ReplyKeyboard handlers
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
    purchases = await get_purchases(msg.from_user.id)

    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{msg.from_user.id}</code>\n"
        f"{ae('cart')} <b>Покупок:</b> {user['total_purchases']}\n"
        f"{ae('money')} <b>Потрачено:</b> ${user['total_spent']:.2f}\n"
        f"{ae('cal')} <b>Регистрация:</b> {user['registered_at'][:10]}\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    if purchases:
        text += "\n\n📋 <b>Последние покупки:</b>\n"
        for p in purchases[:5]:
            text += f"  • {p['pname']} — <b>${p['price']}</b>\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 История покупок", callback_data="my_purchases")]
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
        [InlineKeyboardButton(text="✉️ Написать", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")]
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
    kb = [[InlineKeyboardButton(text=f"🗂 {c['name']}", callback_data=f"cat_{c['id']}")] for c in cats]
    text = f"{ae('cart')} <b>Каталог</b>\n\n<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    await send_media(chat_id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "shop")
async def cb_shop(cb: types.CallbackQuery):
    cats = await get_categories()
    if not cats:
        await cb.answer("Категории пока не добавлены", show_alert=True)
        return
    kb = [[InlineKeyboardButton(text=f"🗂 {c['name']}", callback_data=f"cat_{c['id']}")] for c in cats]
    text = f"{ae('cart')} <b>Каталог</b>\n\n<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
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
        kb.append([InlineKeyboardButton(
            text=f"📦 {p['name']}  ·  ${p['price']}",
            callback_data=f"prod_{p['id']}"
        )])
    kb.append([InlineKeyboardButton(text="‹ Назад", callback_data="shop")])
    text = f"<blockquote>{ae('down')} Выберите товар:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()

@router.callback_query(F.data.startswith("prod_"))
async def cb_prod(cb: types.CallbackQuery):
    pid = int(cb.data.split("_")[1])
    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    text = (
        f"📦 <b>{p['name']}</b>\n\n"
        f"<blockquote>{p['description']}</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ae('money')} <b>Цена:</b> <code>${p['price']}</code>\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить", callback_data=f"buy_{pid}")],
        [InlineKeyboardButton(text="‹ Назад", callback_data=f"cat_{p['category_id']}")]
    ])
    try:
        await cb.message.delete()
    except: pass
    await send_media(cb.from_user.id, text, f"product_{pid}", kb)
    await cb.answer()

# ══════════════════════════════════════════════
#  Выбор метода оплаты
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("buy_"))
async def cb_buy(cb: types.CallbackQuery):
    pid = int(cb.data.split("_")[1])
    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    text = (
        f"💳 <b>Способ оплаты</b>\n\n"
        f"📦 <b>Товар:</b> {p['name']}\n"
        f"{ae('money')} <b>Сумма:</b> <code>${p['price']}</code>\n\n"
        f"<blockquote>{ae('down')} Выберите удобный способ:</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 CryptoBot (USDT)", callback_data=f"pcrypto_{pid}")],
        [InlineKeyboardButton(text="🏦 Kaspi",            callback_data=f"pkaspi_{pid}")],
        [InlineKeyboardButton(text="‹ Назад",             callback_data=f"prod_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

# ── CryptoBot ────────────────────────────────
@router.callback_query(F.data.startswith("pcrypto_"))
async def cb_pcrypto(cb: types.CallbackQuery):
    pid = int(cb.data.split("_")[1])
    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    inv = await create_invoice(p['price'], f"Покупка: {p['name']}", f"{cb.from_user.id}:{pid}")
    if not inv:
        await cb.answer("⚠️ Ошибка создания счёта. Попробуйте позже.", show_alert=True)
        return
    await save_crypto(cb.from_user.id, pid, str(inv['invoice_id']), p['price'])
    text = (
        f"🔐 <b>Оплата через CryptoBot</b>\n\n"
        f"📦 <b>Товар:</b> {p['name']}\n"
        f"{ae('money')} <b>Сумма:</b> <code>${p['price']} USDT</code>\n\n"
        f"<blockquote>1. Нажмите «Оплатить»\n"
        f"2. Вернитесь сюда\n"
        f"3. Нажмите «Проверить оплату»</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить",        url=inv['pay_url'])],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"chk_{inv['invoice_id']}")],
        [InlineKeyboardButton(text="‹ Назад",             callback_data=f"buy_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("chk_"))
async def cb_chk(cb: types.CallbackQuery):
    inv_id = cb.data[4:]
    inv = await check_invoice(inv_id)
    if not inv:
        await cb.answer("⚠️ Ошибка проверки. Попробуйте позже.", show_alert=True)
        return
    if inv['status'] != 'paid':
        await cb.answer("⏳ Оплата ещё не поступила.", show_alert=True)
        return
    payment = await get_crypto(inv_id)
    if not payment or payment['status'] == 'paid':
        await cb.answer("Товар уже выдан!", show_alert=True)
        return
    await set_crypto_paid(inv_id)
    product = await get_product(payment['product_id'])
    await add_purchase(cb.from_user.id, payment['product_id'], payment['amount'])
    try: await cb.message.delete()
    except: pass
    await deliver(cb.from_user.id, product)
    # уведомление админам
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"💰 <b>Покупка (CryptoBot)</b>\n\n"
                f"👤 @{cb.from_user.username or '—'} (<code>{cb.from_user.id}</code>)\n"
                f"📦 {product['name']}\n"
                f"{ae('money')} ${payment['amount']} USDT\n"
                f"{ae('cal')} {fmt_dt()}",
                parse_mode="HTML"
            )
        except: pass
    await cb.answer("✅ Готово!")

# ── Kaspi ────────────────────────────────────
@router.callback_query(F.data.startswith("pkaspi_"))
async def cb_pkaspi(cb: types.CallbackQuery):
    pid = int(cb.data.split("_")[1])
    p = await get_product(pid)
    if not p:
        await cb.answer("Товар не найден", show_alert=True)
        return
    kid = await save_kaspi(cb.from_user.id, pid, p['price'])
    text = (
        f"🏦 <b>Оплата через Kaspi</b>\n\n"
        f"📦 <b>Товар:</b> {p['name']}\n"
        f"{ae('money')} <b>Сумма:</b> <code>${p['price']}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📱 Номер для перевода:\n"
        f"<code>{KASPI_PHONE}</code>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>После перевода нажмите «Я оплатил» — "
        f"менеджер проверит и подтвердит вручную.</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"kpaid_{kid}")],
        [InlineKeyboardButton(text="‹ Назад",      callback_data=f"buy_{pid}")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("kpaid_"))
async def cb_kpaid(cb: types.CallbackQuery):
    kid = int(cb.data.split("_")[1])
    kp = await get_kaspi(kid)
    if not kp:
        await cb.answer("Платёж не найден", show_alert=True)
        return
    if kp['status'] != 'pending':
        await cb.answer("Этот платёж уже обработан", show_alert=True)
        return
    product = await get_product(kp['product_id'])
    uname = cb.from_user.username or "—"
    mgr_text = (
        f"🏦 <b>Заявка на оплату Kaspi</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 @{uname} (<code>{kp['user_id']}</code>)\n"
        f"📦 <b>Товар:</b> {product['name']}\n"
        f"{ae('money')} <b>Сумма:</b> ${kp['amount']}\n"
        f"{ae('cal')} {fmt_dt()}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>Проверьте поступление и примите решение:</blockquote>"
    )
    mgr_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"kapprove_{kid}"),
            InlineKeyboardButton(text="❌ Отклонить",  callback_data=f"kreject_{kid}"),
        ]
    ])
    try:
        mgr_msg = await bot.send_message(MANAGER_ID, mgr_text, parse_mode="HTML", reply_markup=mgr_kb)
        await set_kaspi_status(kid, 'waiting', mgr_msg.message_id)
    except Exception:
        await cb.answer("⚠️ Не удалось уведомить менеджера. Обратитесь в поддержку.", show_alert=True)
        return
    try:
        await cb.message.edit_text(
            f"⏳ <b>Ожидаем подтверждения</b>\n\n"
            f"Заявка отправлена менеджеру.\n"
            f"Товар будет выдан автоматически после подтверждения.\n\n"
            f"<blockquote>Обычно это занимает несколько минут.</blockquote>",
            parse_mode="HTML",
            reply_markup=kb_back("shop")
        )
    except:
        pass
    await cb.answer("✅ Заявка отправлена!")

@router.callback_query(F.data.startswith("kapprove_"))
async def cb_kapprove(cb: types.CallbackQuery):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    kid = int(cb.data.split("_")[1])
    kp = await get_kaspi(kid)
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
    await set_kaspi_status(kid, 'paid')
    await add_purchase(kp['user_id'], kp['product_id'], kp['amount'], 'kaspi')
    await deliver(kp['user_id'], product)
    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n✅ <b>ПОДТВЕРЖДЕНО</b> — @{who}",
            parse_mode="HTML"
        )
    except: pass
    await cb.answer("✅ Подтверждено, товар выдан!")
    for aid in ADMIN_IDS:
        if aid == MANAGER_ID: continue
        try:
            await bot.send_message(
                aid,
                f"💰 <b>Покупка (Kaspi)</b>\n\n"
                f"👤 <code>{kp['user_id']}</code>\n"
                f"📦 {product['name']}\n"
                f"{ae('money')} ${kp['amount']}\n"
                f"{ae('cal')} {fmt_dt()}\n"
                f"✅ Подтвердил: @{who}",
                parse_mode="HTML"
            )
        except: pass

@router.callback_query(F.data.startswith("kreject_"))
async def cb_kreject(cb: types.CallbackQuery):
    if cb.from_user.id != MANAGER_ID and cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    kid = int(cb.data.split("_")[1])
    kp = await get_kaspi(kid)
    if not kp or kp['status'] in ('paid','rejected'):
        await cb.answer("Платёж уже обработан", show_alert=True)
        return
    product = await get_product(kp['product_id'])
    await set_kaspi_status(kid, 'rejected')
    try:
        await bot.send_message(
            kp['user_id'],
            f"❌ <b>Оплата отклонена</b>\n\n"
            f"📦 {product['name']} — ${kp['amount']}\n\n"
            f"<blockquote>Менеджер не нашёл перевод. "
            f"Если вы уверены — напишите в поддержку: {SUPPORT_USERNAME}</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❓ Поддержка",
                                     url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")
            ]])
        )
    except: pass
    who = cb.from_user.username or str(cb.from_user.id)
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n❌ <b>ОТКЛОНЕНО</b> — @{who}",
            parse_mode="HTML"
        )
    except: pass
    await cb.answer("❌ Отклонено, пользователь уведомлён")

# ══════════════════════════════════════════════
#  Профиль — история покупок
# ══════════════════════════════════════════════
@router.callback_query(F.data == "my_purchases")
async def cb_my_purchases(cb: types.CallbackQuery):
    purchases = await get_purchases(cb.from_user.id)
    if not purchases:
        await cb.answer("У вас пока нет покупок", show_alert=True)
        return
    text = f"{ae('archive')} <b>История покупок</b>\n\n━━━━━━━━━━━━━━━━━\n"
    for p in purchases:
        text += f"📦 {p['pname']} — <b>${p['price']}</b>  <i>({p['purchased_at'][:10]})</i>\n"
    text += "━━━━━━━━━━━━━━━━━"
    try:
        await cb.message.edit_text(text, parse_mode="HTML")
    except:
        await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()

# ══════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════
def admin_guard(uid): return uid in ADMIN_IDS

@router.callback_query(F.data == "adm_panel")
async def cb_adm_panel(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    await state.clear()
    try:
        await cb.message.edit_text("🎩 <b>Панель управления</b>", parse_mode="HTML", reply_markup=kb_admin())
    except:
        await cb.message.answer("🎩 <b>Панель управления</b>", parse_mode="HTML", reply_markup=kb_admin())
    await cb.answer()

# ── Статистика ───────────────────────────────
@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id): return
    uc, pc, rv, ac = await get_stats()
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👥 Пользователей: <b>{uc}</b>\n"
        f"{ae('cart')} Покупок: <b>{pc}</b>\n"
        f"{ae('money')} Выручка: <b>${rv:.2f}</b>\n"
        f"📦 Товаров: <b>{ac}</b>\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb_admin_back())
    except:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb_admin_back())
    await cb.answer()

# ── Медиа ────────────────────────────────────
@router.callback_query(F.data == "adm_media")
async def cb_adm_media(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id): return
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
    except:
        await cb.message.answer(
            "🖼 <b>Настройка медиа</b>\n\n<blockquote>Выберите раздел:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("smedia_"))
async def cb_smedia(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    key = cb.data[7:]
    await state.update_data(media_key=key)
    await state.set_state(AdminSt.set_media_file)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить медиа", callback_data=f"delmedia_{key}")],
        [InlineKeyboardButton(text="‹ Назад",          callback_data="adm_media")],
    ])
    try:
        await cb.message.edit_text(
            "🖼 <b>Отправьте фото, видео или GIF:</b>",
            parse_mode="HTML", reply_markup=kb
        )
    except:
        await cb.message.answer(
            "🖼 <b>Отправьте фото, видео или GIF:</b>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("delmedia_"))
async def cb_delmedia(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    key = cb.data[9:]
    await db_run('DELETE FROM media_settings WHERE key=?', (key,))
    await state.clear()
    await cb.answer("✅ Медиа удалено", show_alert=True)
    await cb_adm_media(cb)

@router.message(AdminSt.set_media_file,
                F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION]))
async def proc_media_file(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    key = d.get("media_key")
    if msg.photo:      fid, mt = msg.photo[-1].file_id, "photo"
    elif msg.video:    fid, mt = msg.video.file_id, "video"
    elif msg.animation:fid, mt = msg.animation.file_id, "animation"
    else:
        await msg.answer("❌ Неподдерживаемый формат", reply_markup=kb_admin_back())
        return
    await set_media(key, mt, fid)
    await state.clear()
    await msg.answer("✅ Медиа установлено!", reply_markup=kb_admin_back())

# ── Рассылка ─────────────────────────────────
@router.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    await state.set_state(AdminSt.broadcast)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="adm_panel")]])
    try:
        await cb.message.edit_text(
            "📨 <b>Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except:
        await cb.message.answer(
            "📨 <b>Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    await cb.answer()

@router.message(AdminSt.broadcast)
async def proc_broadcast(msg: types.Message, state: FSMContext):
    await state.clear()
    users = await all_user_ids()
    ok = fail = 0
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
        except:
            fail += 1
        await asyncio.sleep(0.05)
    await status.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n📤 Отправлено: {ok}\n❌ Ошибок: {fail}",
        parse_mode="HTML", reply_markup=kb_admin_back()
    )

# ── Категории ────────────────────────────────
@router.callback_query(F.data == "adm_cats")
async def cb_adm_cats(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id): return
    cats = await get_categories()
    kb = []
    for c in cats:
        kb.append([
            InlineKeyboardButton(text=f"📂 {c['name']}", callback_data=f"ecat_{c['id']}"),
            InlineKeyboardButton(text="🗑",              callback_data=f"dcat_{c['id']}"),
        ])
    kb.append([InlineKeyboardButton(text="➕ Добавить", callback_data="addcat")])
    kb.append([InlineKeyboardButton(text="‹ Назад",     callback_data="adm_panel")])
    text = f"{ae('folder')} <b>Категории</b>\n\n<blockquote>Управление категориями:</blockquote>"
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()

@router.callback_query(F.data == "addcat")
async def cb_addcat(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    await state.set_state(AdminSt.add_cat_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="adm_cats")]])
    try:
        await cb.message.edit_text(
            f"{ae('folder')} <b>Новая категория</b>\n\n<blockquote>Введите название:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except:
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
    if not admin_guard(cb.from_user.id): return
    cid = int(cb.data.split("_")[1])
    await del_category(cid)
    await cb.answer("✅ Категория удалена", show_alert=True)
    await cb_adm_cats(cb)

# ── Товары ───────────────────────────────────
@router.callback_query(F.data == "adm_products")
async def cb_adm_products(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id): return
    cats = await get_categories()
    kb = [[InlineKeyboardButton(text=f"📂 {c['name']}", callback_data=f"apcat_{c['id']}")] for c in cats]
    kb.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="addprod")])
    kb.append([InlineKeyboardButton(text="‹ Назад",          callback_data="adm_panel")])
    try:
        await cb.message.edit_text(
            "📦 <b>Товары</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    except:
        await cb.message.answer(
            "📦 <b>Товары</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("apcat_"))
async def cb_apcat(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id): return
    cid = int(cb.data.split("_")[1])
    prods = await get_products(cid)
    kb = []
    for p in prods:
        kb.append([
            InlineKeyboardButton(text=f"📦 {p['name']} — ${p['price']}", callback_data=f"vprod_{p['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"dprod_{p['id']}"),
        ])
    kb.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")])
    try:
        await cb.message.edit_text(
            "<blockquote>📦 Товары категории:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    except:
        await cb.message.answer(
            "<blockquote>📦 Товары категории:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("dprod_"))
async def cb_dprod(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id): return
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
    except: pass

# ── Добавление товара (FSM) ──────────────────
@router.callback_query(F.data == "addprod")
async def cb_addprod(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    cats = await get_categories()
    if not cats:
        await cb.answer("Сначала создайте категорию!", show_alert=True)
        return
    kb = [[InlineKeyboardButton(text=f"📂 {c['name']}", callback_data=f"npcat_{c['id']}")] for c in cats]
    kb.append([InlineKeyboardButton(text="‹ Назад", callback_data="adm_products")])
    try:
        await cb.message.edit_text(
            "📦 <b>Новый товар</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    except:
        await cb.message.answer(
            "📦 <b>Новый товар</b>\n\n<blockquote>Выберите категорию:</blockquote>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    await cb.answer()

@router.callback_query(F.data.startswith("npcat_"))
async def cb_npcat(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    cid = int(cb.data.split("_")[1])
    await state.update_data(cid=cid)
    await state.set_state(AdminSt.add_prod_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]])
    try:
        await cb.message.edit_text(
            "📦 <b>Название товара</b>\n\n"
            "<blockquote>Введите название.\n💡 Можно вставлять анимированные эмодзи.</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except:
        await cb.message.answer(
            "📦 <b>Название товара</b>\n\n"
            "<blockquote>Введите название.\n💡 Можно вставлять анимированные эмодзи.</blockquote>",
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]])
    )

@router.message(AdminSt.add_prod_desc)
async def proc_prod_desc(msg: types.Message, state: FSMContext):
    desc = msg.html_text if msg.entities else msg.text
    await state.update_data(desc=desc)
    await state.set_state(AdminSt.add_prod_price)
    await msg.answer(
        "📦 <b>Цена товара</b>\n\n<blockquote>Введите цену в USD (например: 9.99):</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]])
    )

@router.message(AdminSt.add_prod_price)
async def proc_prod_price(msg: types.Message, state: FSMContext):
    try:
        price = float(msg.text.replace(",", "."))
    except ValueError:
        await msg.answer("❌ Введите корректное число, например: <code>9.99</code>", parse_mode="HTML")
        return
    await state.update_data(price=price)
    await state.set_state(AdminSt.add_prod_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Текстовый", callback_data="ptype_text")],
        [InlineKeyboardButton(text="📎 Файловый",  callback_data="ptype_file")],
        [InlineKeyboardButton(text="‹ Назад",      callback_data="addprod")],
    ])
    await msg.answer(
        "📦 <b>Тип товара</b>\n\n<blockquote>Выберите тип контента:</blockquote>",
        parse_mode="HTML", reply_markup=kb
    )

@router.callback_query(F.data.startswith("ptype_"), AdminSt.add_prod_type)
async def cb_ptype(cb: types.CallbackQuery, state: FSMContext):
    pt = cb.data.split("_")[1]
    await state.update_data(ptype=pt)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="addprod")]])
    if pt == "text":
        await state.set_state(AdminSt.add_prod_text)
        try:
            await cb.message.edit_text(
                "📦 <b>Контент товара</b>\n\n<blockquote>Введите текст (ключи, данные, инструкции и т.д.):</blockquote>",
                parse_mode="HTML", reply_markup=kb
            )
        except:
            await cb.message.answer(
                "📦 <b>Контент товара</b>\n\n<blockquote>Введите текст (ключи, данные, инструкции и т.д.):</blockquote>",
                parse_mode="HTML", reply_markup=kb
            )
    else:
        await state.set_state(AdminSt.add_prod_file)
        try:
            await cb.message.edit_text(
                "📦 <b>Файл товара</b>\n\n<blockquote>Отправьте файл:</blockquote>",
                parse_mode="HTML", reply_markup=kb
            )
        except:
            await cb.message.answer(
                "📦 <b>Файл товара</b>\n\n<blockquote>Отправьте файл:</blockquote>",
                parse_mode="HTML", reply_markup=kb
            )
    await cb.answer()

@router.message(AdminSt.add_prod_text)
async def proc_prod_text(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    await add_product(d['cid'], d['name'], d['desc'], d['price'], 'text', content=msg.text)
    await state.clear()
    await msg.answer("✅ Товар добавлен!", reply_markup=kb_admin_back())

@router.message(AdminSt.add_prod_file, F.document)
async def proc_prod_file(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    await add_product(d['cid'], d['name'], d['desc'], d['price'], 'file', file_id=msg.document.file_id)
    await state.clear()
    await msg.answer("✅ Товар добавлен!", reply_markup=kb_admin_back())

# ── Настройки ────────────────────────────────
@router.callback_query(F.data == "adm_settings")
async def cb_adm_settings(cb: types.CallbackQuery):
    if not admin_guard(cb.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Описание магазина", callback_data="edit_shop_info")],
        [InlineKeyboardButton(text="‹ Назад",              callback_data="adm_panel")],
    ])
    try:
        await cb.message.edit_text("⚙️ <b>Настройки</b>", parse_mode="HTML", reply_markup=kb)
    except:
        await cb.message.answer("⚙️ <b>Настройки</b>", parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "edit_shop_info")
async def cb_edit_shop(cb: types.CallbackQuery, state: FSMContext):
    if not admin_guard(cb.from_user.id): return
    await state.set_state(AdminSt.edit_shop_info)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="adm_settings")]])
    try:
        await cb.message.edit_text(
            "📝 <b>Описание магазина</b>\n\n<blockquote>Введите новое описание:</blockquote>",
            parse_mode="HTML", reply_markup=kb
        )
    except:
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
#  Cancel state on nav callbacks
# ══════════════════════════════════════════════
NAV = {"adm_panel","adm_media","adm_cats","adm_products","addprod","adm_settings"}

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
    print("\033[35m" + "═" * 42)
    print("  🤖 t.me/fuck_zaza")
    print("═" * 42 + "\033[0m")
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
