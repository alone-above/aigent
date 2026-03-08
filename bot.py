import asyncio
import logging
import os
import aiosqlite
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, ContentType, BotCommand, BotCommandScopeChat
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support")
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ==================== Animated Emoji ====================
# Animated emoji helper: renders as animated sticker in Telegram
E = {
    "shop":     '<tg-emoji emoji-id="5373052667671093676">🛍</tg-emoji>',
    "question": '<tg-emoji emoji-id="5467666648263564704">❓</tg-emoji>',
    "down":     '<tg-emoji emoji-id="5470177992950946662">👇</tg-emoji>',
    "folder":   '<tg-emoji emoji-id="5433653135799228968">📁</tg-emoji>',
    "money":    '<tg-emoji emoji-id="5472030678633684592">💸</tg-emoji>',
    "cart":     '<tg-emoji emoji-id="5431499171045581032">🛒</tg-emoji>',
    "calendar": '<tg-emoji emoji-id="5431897022456145283">📆</tg-emoji>',
    "id":       '<b>ID</b>',
    "archive":  '<tg-emoji emoji-id="5431736674147114227">🗂</tg-emoji>',
    "store":    '<tg-emoji emoji-id="5265105755677159697">🏬</tg-emoji>',
    "support":  '<tg-emoji emoji-id="5467666648263564704">❓</tg-emoji>',
}

def ae(key: str) -> str:
    """Return animated emoji HTML by key."""
    return E.get(key, "")

DB_PATH = "shop.db"


# ==================== FSM States ====================
class AdminStates(StatesGroup):
    broadcast_text = State()
    broadcast_media = State()
    set_media_select = State()
    set_media_file = State()
    add_category_name = State()
    add_product_category = State()
    add_product_name = State()
    add_product_desc = State()
    add_product_price = State()
    add_product_type = State()
    add_product_content = State()
    add_product_file = State()
    edit_shop_info = State()


class UserStates(StatesGroup):
    waiting_payment = State()


# ==================== Database ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            total_purchases INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0,
            registered_at TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            product_type TEXT DEFAULT 'text',
            content TEXT,
            file_id TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            price REAL,
            purchased_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS media_settings (
            key TEXT PRIMARY KEY,
            media_type TEXT,
            file_id TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS shop_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            invoice_id TEXT,
            amount REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )''')
        await db.commit()


async def add_user(user: types.User):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, registered_at)
            VALUES (?, ?, ?, ?)''', (user.id, user.username, user.first_name, datetime.now().isoformat()))
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM purchases') as cursor:
            purchases_count = (await cursor.fetchone())[0]
        async with db.execute('SELECT COALESCE(SUM(price), 0) FROM purchases') as cursor:
            total_revenue = (await cursor.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM products WHERE is_active = 1') as cursor:
            products_count = (await cursor.fetchone())[0]
    return users_count, purchases_count, total_revenue, products_count


async def get_categories():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM categories') as cursor:
            return await cursor.fetchall()


async def add_category(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT INTO categories (name) VALUES (?)', (name,))
        await db.commit()


async def delete_category(cat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM products WHERE category_id = ?', (cat_id,))
        await db.execute('DELETE FROM categories WHERE id = ?', (cat_id,))
        await db.commit()


async def get_products_by_category(category_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM products WHERE category_id = ? AND is_active = 1',
                              (category_id,)) as cursor:
            return await cursor.fetchall()


async def get_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM products WHERE id = ?', (product_id,)) as cursor:
            return await cursor.fetchone()


async def add_product(category_id, name, description, price, product_type, content=None, file_id=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''INSERT INTO products (category_id, name, description, price, product_type, content, file_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                         (category_id, name, description, price, product_type, content, file_id,
                          datetime.now().isoformat()))
        await db.commit()


async def delete_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE products SET is_active = 0 WHERE id = ?', (product_id,))
        await db.commit()


async def add_purchase(user_id: int, product_id: int, price: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''INSERT INTO purchases (user_id, product_id, price, purchased_at)
            VALUES (?, ?, ?, ?)''', (user_id, product_id, price, datetime.now().isoformat()))
        await db.execute('''UPDATE users SET total_purchases = total_purchases + 1, 
            total_spent = total_spent + ? WHERE user_id = ?''', (price, user_id))
        await db.commit()


async def get_user_purchases(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''SELECT p.*, pr.name as product_name FROM purchases p 
            JOIN products pr ON p.product_id = pr.id WHERE p.user_id = ? ORDER BY p.purchased_at DESC LIMIT 10''',
                              (user_id,)) as cursor:
            return await cursor.fetchall()


async def set_media(key: str, media_type: str, file_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO media_settings (key, media_type, file_id) VALUES (?, ?, ?)',
                         (key, media_type, file_id))
        await db.commit()


async def get_media(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM media_settings WHERE key = ?', (key,)) as cursor:
            return await cursor.fetchone()


async def get_shop_setting(key: str, default: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT value FROM shop_settings WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_shop_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO shop_settings (key, value) VALUES (?, ?)', (key, value))
        await db.commit()


async def save_payment(user_id: int, product_id: int, invoice_id: str, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''INSERT INTO payments (user_id, product_id, invoice_id, amount, created_at)
            VALUES (?, ?, ?, ?, ?)''', (user_id, product_id, invoice_id, amount, datetime.now().isoformat()))
        await db.commit()


async def update_payment_status(invoice_id: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE payments SET status = ? WHERE invoice_id = ?', (status, invoice_id))
        await db.commit()


async def get_payment(invoice_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM payments WHERE invoice_id = ?', (invoice_id,)) as cursor:
            return await cursor.fetchone()


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM users') as cursor:
            return [row[0] for row in await cursor.fetchall()]


# ==================== CryptoBot API ====================
async def create_invoice(amount: float, description: str, payload: str):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    data = {
        "asset": "USDT",
        "amount": str(amount),
        "description": description,
        "payload": payload,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{(await bot.get_me()).username}"
    }
    import ssl
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.json()
            if result.get("ok"):
                return result["result"]
    return None


async def check_invoice(invoice_id: str):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}
    import ssl
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(url, headers=headers, params=params) as resp:
            result = await resp.json()
            if result.get("ok") and result["result"]["items"]:
                return result["result"]["items"][0]
    return None


# ==================== Keyboards ====================
def main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛒 Купить"), KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="🏬 О шопе"), KeyboardButton(text="❓ Поддержка")]
    ], resize_keyboard=True)


def back_button(callback_data: str = "main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🖼 Медиа", callback_data="admin_media")],
        [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📦 Товары", callback_data="admin_products")],
        [InlineKeyboardButton(text="📁 Категории", callback_data="admin_categories")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")]
    ])


def admin_back():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])


# ==================== Helper Functions ====================
async def send_with_media(chat_id: int, text: str, media_key: str, reply_markup=None):
    media = await get_media(media_key)
    if media:
        if media["media_type"] == "photo":
            await bot.send_photo(chat_id, media["file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media["media_type"] == "video":
            await bot.send_video(chat_id, media["file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
        elif media["media_type"] == "animation":
            await bot.send_animation(chat_id, media["file_id"], caption=text, parse_mode="HTML",
                                     reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)


async def set_commands(user_id: int):
    commands = [BotCommand(command="start", description="🚀 Старт")]
    if user_id in ADMIN_IDS:
        commands.append(BotCommand(command="admin", description="🎩 Админ панель"))
    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))


# ==================== Handlers ====================
@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user)
    await set_commands(message.from_user.id)
    text = f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n<blockquote>{ae('down')} Выберите действие:</blockquote>"
    await message.answer(text, parse_mode="HTML", reply_markup=main_keyboard())


@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await message.answer("<blockquote>🎩 <b>Админ панель</b></blockquote>", parse_mode="HTML",
                         reply_markup=admin_keyboard())


@router.callback_query(F.data == "main")
async def cb_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = f"{ae('shop')} <b>{SHOP_NAME}</b>\n\n<blockquote>{ae('down')} Выберите действие:</blockquote>"
    try:
        await callback.message.delete()
    except:
        pass
    await bot.send_message(callback.from_user.id, text, parse_mode="HTML", reply_markup=main_keyboard())
    await callback.answer()


# ==================== Text Button Handlers ====================
@router.message(F.text == "🛒 Купить")
async def text_shop(message: types.Message):
    categories = await get_categories()
    if not categories:
        await message.answer("Категории пока не добавлены")
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=f"📂 {cat['name']}", callback_data=f"cat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main")])

    text = f"{ae('cart')} <b>Каталог товаров</b>\n\n<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    await send_with_media(message.chat.id, text, "shop_menu", InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.message(F.text == "👤 Мой профиль")
async def text_profile(message: types.Message):
    user = await get_user(message.from_user.id)
    purchases = await get_user_purchases(message.from_user.id)

    text = f"👤 <b>Мой профиль</b>\n\n"
    text += f"{ae('id')} <b>ID:</b> <code>{message.from_user.id}</code>\n"
    text += f"{ae('cart')} <b>Покупок:</b> {user['total_purchases']}\n"
    text += f"{ae('money')} <b>Потрачено:</b> ${user['total_spent']:.2f}\n"
    text += f"{ae('calendar')} <b>Регистрация:</b> {user['registered_at'][:10]}\n"

    if purchases:
        text += "\n<b>📋 Последние покупки:</b>\n"
        for p in purchases[:5]:
            text += f"• {p['product_name']} — ${p['price']}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{ae('archive')} Мои покупки", callback_data="my_purchases")]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(F.text == "🏬 О шопе")
async def text_about(message: types.Message):
    info = await get_shop_setting("shop_info", "Информация о магазине не заполнена.")
    text = f"{ae('store')} <b>О шопе</b>\n\n<blockquote>{info}</blockquote>"
    await send_with_media(message.chat.id, text, "about_menu", None)


@router.message(F.text == "❓ Поддержка")
async def text_support(message: types.Message):
    text = f"{ae('support')} <b>Поддержка</b>\n\n<blockquote>По всем вопросам обращайтесь: {SUPPORT_USERNAME}</blockquote>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}")]
    ])

    await send_with_media(message.chat.id, text, "support_menu", keyboard)


# ==================== Shop ====================
@router.callback_query(F.data == "shop")
async def cb_shop(callback: types.CallbackQuery):
    categories = await get_categories()
    if not categories:
        await callback.answer("Категории пока не добавлены", show_alert=True)
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=f"📂 {cat['name']}", callback_data=f"cat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main")])

    text = f"{ae('cart')} <b>Каталог товаров</b>\n\n<blockquote>{ae('down')} Выберите категорию:</blockquote>"
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@router.callback_query(F.data.startswith("cat_"))
async def cb_category(callback: types.CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    products = await get_products_by_category(cat_id)

    if not products:
        await callback.answer("В этой категории пока нет товаров", show_alert=True)
        return

    keyboard = []
    for prod in products:
        keyboard.append([InlineKeyboardButton(
            text=f"📦 {prod['name']} — ${prod['price']}",
            callback_data=f"prod_{prod['id']}"
        )])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="shop")])

    await callback.message.edit_text(
        "<blockquote>" + ae('down') + " Выберите товар:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prod_"))
async def cb_product(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = f"📦 <b>{product['name']}</b>\n\n"
    text += f"<blockquote>{product['description']}</blockquote>\n\n"
    text += f"{ae('money')} <b>Цена:</b> ${product['price']}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить", callback_data=f"buy_{prod_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"cat_{product['category_id']}")]
    ])

    try:
        await callback.message.delete()
    except:
        pass
    await send_with_media(callback.from_user.id, text, f"product_{prod_id}", keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("buy_"))
async def cb_buy(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    # Create CryptoBot invoice
    invoice = await create_invoice(
        amount=product['price'],
        description=f"Покупка: {product['name']}",
        payload=f"{callback.from_user.id}:{prod_id}"
    )

    if not invoice:
        await callback.answer("Ошибка создания платежа. Попробуйте позже.", show_alert=True)
        return

    await save_payment(callback.from_user.id, prod_id, str(invoice['invoice_id']), product['price'])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=invoice['pay_url'])],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{invoice['invoice_id']}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"prod_{prod_id}")]
    ])

    text = f"💳 <b>Оплата товара</b>\n\n"
    text += f"📦 <b>Товар:</b> {product['name']}\n"
    text += f"{ae('money')} <b>Сумма:</b> ${product['price']} USDT\n\n"
    text += "<blockquote>Нажмите кнопку «Оплатить» и после оплаты нажмите «Проверить оплату»</blockquote>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("check_"))
async def cb_check_payment(callback: types.CallbackQuery):
    invoice_id = callback.data.split("_")[1]
    invoice = await check_invoice(invoice_id)

    if not invoice:
        await callback.answer("Ошибка проверки платежа", show_alert=True)
        return

    if invoice['status'] == 'paid':
        payment = await get_payment(invoice_id)
        if payment and payment['status'] == 'pending':
            await update_payment_status(invoice_id, 'paid')
            product = await get_product(payment['product_id'])
            await add_purchase(callback.from_user.id, payment['product_id'], payment['amount'])

            # Send product to user
            text = f"✅ <b>Оплата успешна!</b>\n\n📦 <b>Товар:</b> {product['name']}\n\n"

            if product['product_type'] == 'text':
                text += f"<blockquote>{product['content']}</blockquote>"
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button("shop"))
            else:
                await callback.message.edit_text(text, parse_mode="HTML")
                await bot.send_document(callback.from_user.id, product['file_id'],
                                        caption="📎 Ваш товар", reply_markup=back_button("shop"))

            # Notify admins
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id,
                                           f"💰 <b>Новая покупка!</b>\n\n"
                                           f"👤 Покупатель: @{callback.from_user.username or 'Без юзернейма'}\n"
                                           f"📦 Товар: {product['name']}\n"
                                           f"{ae('money')} Сумма: ${payment['amount']}",
                                           parse_mode="HTML"
                                           )
                except:
                    pass
        else:
            await callback.answer("Товар уже выдан!", show_alert=True)
    else:
        await callback.answer("Оплата не найдена. Попробуйте позже.", show_alert=True)


# ==================== Profile ====================

@router.callback_query(F.data == "my_purchases")
async def cb_my_purchases(callback: types.CallbackQuery):
    purchases = await get_user_purchases(callback.from_user.id)

    if not purchases:
        await callback.answer("У вас пока нет покупок", show_alert=True)
        return

    text = f"{ae('archive')} <b>Мои покупки</b>\n\n"
    for p in purchases:
        text += f"📦 {p['product_name']} — ${p['price']} ({p['purchased_at'][:10]})\n"

    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


# ==================== Admin Handlers ====================
@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "<blockquote>🎩 <b>Админ панель</b></blockquote>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    users, purchases, revenue, products = await get_stats()

    text = "📊 <b>Статистика</b>\n\n"
    text += f"👥 <b>Пользователей:</b> {users}\n"
    text += f"{ae('cart')} <b>Покупок:</b> {purchases}\n"
    text += f"{ae('money')} <b>Выручка:</b> ${revenue:.2f}\n"
    text += f"📦 <b>Товаров:</b> {products}"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_back())
    await callback.answer()


# ==================== Admin Media ====================
@router.callback_query(F.data == "admin_media")
async def cb_admin_media(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="setmedia_main_menu")],
        [InlineKeyboardButton(text=f"{ae('cart')} Меню магазина", callback_data="setmedia_shop_menu")],
        [InlineKeyboardButton(text=f"{ae('store')} О шопе", callback_data="setmedia_about_menu")],
        [InlineKeyboardButton(text=f"{ae('support')} Поддержка", callback_data="setmedia_support_menu")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        "🖼 <b>Настройка медиа</b>\n\n<blockquote>Выберите раздел для установки медиа:</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("setmedia_"))
async def cb_setmedia(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    media_key = callback.data.replace("setmedia_", "")
    await state.update_data(media_key=media_key)
    await state.set_state(AdminStates.set_media_file)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить медиа", callback_data=f"delmedia_{media_key}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_media")]
    ])

    await callback.message.edit_text(
        "🖼 <b>Установка медиа</b>\n\n<blockquote>Отправьте фото, видео или GIF для этого раздела:</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delmedia_"))
async def cb_delmedia(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    media_key = callback.data.replace("delmedia_", "")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM media_settings WHERE key = ?', (media_key,))
        await db.commit()

    await state.clear()
    await callback.answer("✅ Медиа удалено", show_alert=True)
    await cb_admin_media(callback)


@router.message(AdminStates.set_media_file,
                F.content_type.in_([ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION]))
async def process_media_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    media_key = data.get("media_key")

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    elif message.animation:
        file_id = message.animation.file_id
        media_type = "animation"
    else:
        await message.answer("❌ Неподдерживаемый формат", reply_markup=admin_back())
        return

    await set_media(media_key, media_type, file_id)
    await state.clear()
    await message.answer("✅ Медиа успешно установлено!", parse_mode="HTML", reply_markup=admin_back())


# ==================== Admin Broadcast ====================
@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.broadcast_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        "📨 <b>Рассылка</b>\n\n<blockquote>Отправьте текст, фото, видео или GIF для рассылки:</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@router.message(AdminStates.broadcast_text)
async def process_broadcast(message: types.Message, state: FSMContext):
    await state.clear()
    users = await get_all_users()

    success = 0
    failed = 0

    status_msg = await message.answer("📤 Рассылка начата...")

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id,
                                     caption=message.caption, parse_mode="HTML")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id,
                                     caption=message.caption, parse_mode="HTML")
            elif message.animation:
                await bot.send_animation(user_id, message.animation.file_id,
                                         caption=message.caption, parse_mode="HTML")
            else:
                await bot.send_message(user_id, message.text, parse_mode="HTML")
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📤 Успешно: {success}\n"
        f"❌ Ошибок: {failed}",
        parse_mode="HTML",
        reply_markup=admin_back()
    )


# ==================== Admin Categories ====================
@router.callback_query(F.data == "admin_categories")
async def cb_admin_categories(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()

    keyboard = []
    for cat in categories:
        keyboard.append([
            InlineKeyboardButton(text=f"📂 {cat['name']}", callback_data=f"editcat_{cat['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"delcat_{cat['id']}")
        ])
    keyboard.append([InlineKeyboardButton(text="➕ Добавить категорию", callback_data="addcat")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])

    await callback.message.edit_text(
        f"{ae('folder')} <b>Категории</b>\n\n<blockquote>Управление категориями товаров:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data == "addcat")
async def cb_addcat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.add_category_name)

    await callback.message.edit_text(
        f"{ae('folder')} <b>Новая категория</b>\n\n<blockquote>Введите название категории:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_categories")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.add_category_name)
async def process_category_name(message: types.Message, state: FSMContext):
    await add_category(message.text)
    await state.clear()
    await message.answer("✅ Категория добавлена!", reply_markup=admin_back())


@router.callback_query(F.data.startswith("delcat_"))
async def cb_delcat(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    await delete_category(cat_id)
    await callback.answer("✅ Категория удалена", show_alert=True)
    await cb_admin_categories(callback)


# ==================== Admin Products ====================
@router.callback_query(F.data == "admin_products")
async def cb_admin_products(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=f"📂 {cat['name']}", callback_data=f"admincat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="addprod")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])

    await callback.message.edit_text(
        "📦 <b>Товары</b>\n\n<blockquote>Выберите категорию для просмотра товаров:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admincat_"))
async def cb_admincat(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    products = await get_products_by_category(cat_id)

    keyboard = []
    for prod in products:
        keyboard.append([
            InlineKeyboardButton(text=f"📦 {prod['name']} — ${prod['price']}", callback_data=f"viewprod_{prod['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"delprod_{prod['id']}")
        ])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_products")])

    await callback.message.edit_text(
        "<blockquote>📦 Товары в категории:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delprod_"))
async def cb_delprod(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    prod_id = int(callback.data.split("_")[1])
    product = await get_product(prod_id)
    await delete_product(prod_id)
    await callback.answer("✅ Товар удален", show_alert=True)

    # Return to category view
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_products")]
    ])
    await callback.message.edit_text("✅ Товар удален", reply_markup=keyboard)


# ==================== Add Product Flow ====================
@router.callback_query(F.data == "addprod")
async def cb_addprod(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    categories = await get_categories()
    if not categories:
        await callback.answer("Сначала создайте категорию!", show_alert=True)
        return

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(text=f"📂 {cat['name']}", callback_data=f"newprodcat_{cat['id']}")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_products")])

    await callback.message.edit_text(
        "📦 <b>Новый товар</b>\n\n<blockquote>Выберите категорию для товара:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("newprodcat_"))
async def cb_newprodcat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    cat_id = int(callback.data.split("_")[1])
    await state.update_data(category_id=cat_id)
    await state.set_state(AdminStates.add_product_name)

    await callback.message.edit_text(
        "📦 <b>Новый товар</b>\n\n<blockquote>Введите название товара.\n\n"
        "💡 Вы можете использовать анимированные эмодзи — просто скопируйте их из любого чата Telegram и вставьте в название или описание.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.add_product_name)
async def process_product_name(message: types.Message, state: FSMContext):
    # Preserve text with entities (animated emoji) if present
    name = message.html_text if message.entities else message.text
    await state.update_data(name=name)
    await state.set_state(AdminStates.add_product_desc)

    await message.answer(
        "📦 <b>Новый товар</b>\n\n<blockquote>Введите описание товара.\n\n"
        "💡 Вы можете использовать анимированные эмодзи прямо в тексте описания.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])
    )


@router.message(AdminStates.add_product_desc)
async def process_product_desc(message: types.Message, state: FSMContext):
    description = message.html_text if message.entities else message.text
    await state.update_data(description=description)
    await state.set_state(AdminStates.add_product_price)

    await message.answer(
        "📦 <b>Новый товар</b>\n\n<blockquote>Введите цену в USD:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])
    )


@router.message(AdminStates.add_product_price)
async def process_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        await state.update_data(price=price)
        await state.set_state(AdminStates.add_product_type)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Текстовый", callback_data="prodtype_text")],
            [InlineKeyboardButton(text="📎 Файловый", callback_data="prodtype_file")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
        ])

        await message.answer(
            "📦 <b>Новый товар</b>\n\n<blockquote>Выберите тип товара:</blockquote>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except ValueError:
        await message.answer("❌ Введите корректную цену (число)")


@router.callback_query(F.data.startswith("prodtype_"), AdminStates.add_product_type)
async def cb_prodtype(callback: types.CallbackQuery, state: FSMContext):
    prod_type = callback.data.split("_")[1]
    await state.update_data(product_type=prod_type)

    if prod_type == "text":
        await state.set_state(AdminStates.add_product_content)
        await callback.message.edit_text(
            "📦 <b>Новый товар</b>\n\n<blockquote>Введите текстовый контент товара (данные, ключи, инструкции и т.д.):</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
            ])
        )
    else:
        await state.set_state(AdminStates.add_product_file)
        await callback.message.edit_text(
            "📦 <b>Новый товар</b>\n\n<blockquote>Отправьте файл товара:</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="addprod")]
            ])
        )
    await callback.answer()


@router.message(AdminStates.add_product_content)
async def process_product_content(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        'text',
        content=message.text
    )
    await state.clear()
    await message.answer("✅ Товар успешно добавлен!", reply_markup=admin_back())


@router.message(AdminStates.add_product_file, F.document)
async def process_product_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await add_product(
        data['category_id'],
        data['name'],
        data['description'],
        data['price'],
        'file',
        file_id=message.document.file_id
    )
    await state.clear()
    await message.answer("✅ Товар успешно добавлен!", reply_markup=admin_back())


# ==================== Admin Settings ====================
@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить описание магазина", callback_data="edit_shop_info")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

    await callback.message.edit_text(
        "⚙️ <b>Настройки</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data == "edit_shop_info")
async def cb_edit_shop_info(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.edit_shop_info)

    await callback.message.edit_text(
        "📝 <b>Описание магазина</b>\n\n<blockquote>Введите новое описание магазина:</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.edit_shop_info)
async def process_shop_info(message: types.Message, state: FSMContext):
    await set_shop_setting("shop_info", message.text)
    await state.clear()
    await message.answer("✅ Описание магазина обновлено!", reply_markup=admin_back())


# ==================== Cancel State Handler ====================
@router.callback_query(
    F.data.in_(["admin_panel", "admin_media", "admin_categories", "admin_products", "addprod", "admin_settings"]))
async def cancel_state(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()


# ==================== Main ====================
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    print("\033[35m" + "═" * 40)
    print("  🤖 Создатель бота: t.me/fuck_zaza")
    print("═" * 40 + "\033[0m")
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
