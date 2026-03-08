import asyncio
import logging
import os
import aiosqlite
import aiohttp
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ContentType
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support")
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")
DB_PATH = "shop.db"

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# --- Вспомогательные функции для Эмодзи и БД ---

def fix_entities(text: str, is_button: bool = False):
    """Исправляет теги анимированных эмодзи, чтобы бот не падал."""
    if not text: return ""
    
    if is_button:
        # В кнопках анимированные эмодзи ЗАПРЕЩЕНЫ - удаляем теги, оставляя символ
        return re.sub(r'<tg-emoji id=".*?">(.*?)</tg-emoji>', r'\1', text)
    
    # В тексте проверяем, что ID состоит только из цифр
    def validate_emoji(match):
        full_tag = match.group(0)
        emoji_id = match.group(1)
        inner_val = match.group(2)
        return full_tag if emoji_id.isdigit() else inner_val

    return re.sub(r'<tg-emoji id="(.*?)">(.*?)</tg-emoji>', validate_emoji, text)

async def db_run(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, params)
        await db.commit()

async def db_get_all(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql, params) as cursor:
            return await cursor.fetchall()

async def db_get_one(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql, params) as cursor:
            return await cursor.fetchone()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица товаров
        await db.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT,
            description TEXT,
            price REAL,
            ptype TEXT DEFAULT 'text',
            content TEXT,
            file_id TEXT,
            created_at TEXT
        )''')
        
        # Проверка и добавление отсутствующих колонок (Миграция)
        async with db.execute("PRAGMA table_info(products)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if 'ptype' not in columns:
                await db.execute("ALTER TABLE products ADD COLUMN ptype TEXT DEFAULT 'text'")
                await db.execute("ALTER TABLE products ADD COLUMN content TEXT")
                await db.execute("ALTER TABLE products ADD COLUMN file_id TEXT")
        
        await db.execute('CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)')
        await db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0, joined_at TEXT)')
        await db.execute('CREATE TABLE IF NOT EXISTS media_settings (code TEXT PRIMARY KEY, file_id TEXT, file_type TEXT)')
        await db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        await db.commit()

# --- Логика отправки сообщений ---

async def send_media(chat_id, text, code, markup=None):
    clean_text = fix_entities(text, is_button=False)
    
    # Очищаем кнопки
    if markup and hasattr(markup, 'inline_keyboard'):
        for row in markup.inline_keyboard:
            for btn in row:
                btn.text = fix_entities(btn.text, is_button=True)

    # Получаем медиа из БД
    m = await db_get_one("SELECT file_id, file_type FROM media_settings WHERE code=?", (code,))
    
    try:
        if m and m[0]:
            fid, ftype = m[0], m[1]
            if ftype == 'photo': await bot.send_photo(chat_id, fid, caption=clean_text, parse_mode="HTML", reply_markup=markup)
            elif ftype == 'video': await bot.send_video(chat_id, fid, caption=clean_text, parse_mode="HTML", reply_markup=markup)
            elif ftype == 'animation': await bot.send_animation(chat_id, fid, caption=clean_text, parse_mode="HTML", reply_markup=markup)
        else:
            await bot.send_message(chat_id, clean_text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        # Если HTML сломан, шлем без разметки
        await bot.send_message(chat_id, "⚠️ Ошибка отображения. Свяжитесь с админом.")

# --- Обработчики (Handlers) ---

@router.message(CommandStart())
async def cmd_start(msg: types.Message):
    user = await db_get_one("SELECT id FROM users WHERE id=?", (msg.from_user.id,))
    if not user:
        await db_run("INSERT INTO users (id, username, joined_at) VALUES (?,?,?)", 
                     (msg.from_user.id, msg.from_user.username, datetime.now().isoformat()))
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 Каталог", callback_data="catalog")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
         InlineKeyboardButton(text="ℹ️ О шопе", callback_data="about_shop")]
    ])
    await send_media(msg.chat.id, f"Привет, {msg.from_user.first_name}! Добро пожаловать в {SHOP_NAME}", "main_menu", kb)

@router.callback_query(F.data == "about_shop")
async def cb_about(cb: types.CallbackQuery):
    info = await db_get_one("SELECT value FROM settings WHERE key='shop_info'")
    text = info[0] if info else "Информация о магазине еще не заполнена."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="main_menu_back")]])
    await send_media(cb.message.chat.id, text, "about_menu", kb)
    await cb.answer()

@router.callback_query(F.data == "main_menu_back")
async def cb_back(cb: types.CallbackQuery):
    await cb.message.delete()
    # Просто вызываем команду старт заново или шлем меню
    await cmd_start(cb.message)

# --- Админка (Упрощенно для примера создания товара) ---

class AdminSt(StatesGroup):
    add_prod_name = State()
    add_prod_price = State()

@router.message(Command("admin"))
async def admin_panel(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS: return
    await msg.answer("⚙️ Админ-панель")

# --- Запуск ---

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    print("🚀 Бот запущен и база данных готова!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
