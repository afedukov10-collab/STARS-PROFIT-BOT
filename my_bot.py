import asyncio
import logging
import aiohttp
import os
import uuid
import json
import random
import asyncpg
from datetime import datetime
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# === БИБЛИОТЕКА ДЛЯ FRAGMENT ===
from pyfragment import FragmentClient
from pyfragment.enums import PaymentMethod

# ========== ТВОИ НАСТРОЙКИ ==========
BOT_TOKEN = "9222158924:AAHnZPNU6pRXhwnsmcQvVX2KTzXkkEbYfAk"
XROCKET_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHBJZCI6IjI5OTQzMyIsImp0aSI6ImFwcDoyOTk0MzM6MWU3ZmFhYjAtMGVkMy00NDVjLWEyY2YtOTcxYWFjOTMwNzI3IiwiaWF0IjoxNzk1MTA5ODM2fQ.Jl22525GYpWtpiAPm-nB_xay9iegNLheXqusjNbWsOY"
ADMIN_ID = 8514329538
BY_CARD_NUMBER = "9112 3801 9121 1830"

CHANNEL_LINK = "https://t.me/starsdeporvida" 
CHANNEL_ID = "@starsdeporvida"

# ========== ДАННЫЕ ДЛЯ FRAGMENT ==========
TONCONSOLE_API_KEY = "AGJNDV54ZZJDOXQAAAAMLKH4IXXFMZINFI7BPL6YY2S74B5FUERKDMPYOW2ZLDWCR72CWYQ"
FRAGMENT_SEED = "milk wild accident explain square height remember coast joy skirt tube tattoo merit dove have grief elevator pony cash alley access surround vital agree"
FRAGMENT_COOKIES = {
    "stel_ssid": "0377ac196801f09708_3134387726996450323",
    "stel_token": "9f33e63ca3c60bc8f5ccc97e33767d329f33e6269f33eab616c15fb49301c752e3f99",
    "stel_dt": "-180",
    "stel_ton_token": "59-o_9wFaMiDGnA2f7xORumamdCHSv4gNwb5TwpUWzdHEuXco8Y0rFQGPDFI_U7hjbAzUV552RkaVvMU2vtsmBR8VzyYXMCTN8Uqu6RRdkxGyXCdykhLO3BhTBnXV9rC5jg3qJAEPK2pGpUGxM3omMzc2b6m6sZASEq5RNKlPPBYpArVeoMicKmiGhpYtNj6XaH9jiUR"
}
# ======================================

SETTINGS = {
    "gram_price": 150.0,  
    "rate_rub": 1.33,     
    "usdt_rate": 90.0,    
}

REVIEWS = []
orders = {}
db_pool = None 

GIVEAWAYS = {}
giveaway_tasks = {}
GIFT_CACHE = [] 

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
bot = None
session = None

# --- РАБОТА С POSTGRESQL ---
async def init_db():
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                balance DECIMAL(10, 2) DEFAULT 0.0,
                history_orders JSONB DEFAULT '[]'::jsonb,
                history_deposits JSONB DEFAULT '[]'::jsonb
            )
        """)

async def get_user(user_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not row:
            await conn.execute("INSERT INTO users (user_id) VALUES ($1)", user_id)
            return {"balance": 0.0, "history_orders": [], "history_deposits": []}
        return {
            "balance": float(row["balance"]),
            "history_orders": row["history_orders"],
            "history_deposits": row["history_deposits"]
        }

async def update_balance(user_id, amount):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", amount, user_id)

async def add_order_history(user_id, order_text):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE users SET history_orders = history_orders || $1::jsonb WHERE user_id = $2
        """, json.dumps([order_text]), user_id)

async def add_deposit_history(user_id, deposit_text):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE users SET history_deposits = history_deposits || $1::jsonb WHERE user_id = $2
        """, json.dumps([deposit_text]), user_id)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)
@app.route('/')
def index():
    return "Bot is alive!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- УТИЛИТЫ ---
async def clean_chat(chat_id, message_id=None):
    try:
        if message_id:
            await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

# --- АВТО-КУРС С НБРБ ---
async def get_nbrb_usd_rate():
    url = "https://api.nbrb.by/exrates/rates/431?parammode=2"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return float(data.get("Cur_OfficialRate", 3.0))
    except Exception as e:
        print(f"⚠️ Ошибка получения курса НБРБ: {e}")
    return 3.0

async def convert_rub_to_byn(price_rub: float) -> float:
    usd_byn_rate = await get_nbrb_usd_rate()
    price_usd = price_rub / SETTINGS['usdt_rate']
    return price_usd * usd_byn_rate

# --- СОЗДАНИЕ СЧЕТА XROCKET ---
async def create_xrocket_invoice(amount: float, currency: str, description: str):
    url = "https://pay.api.xrocket.exchange/api/v1/invoices"
    headers = {"Authorization": f"Bearer {XROCKET_API_KEY}", "Content-Type": "application/json"}
    
    payload = {
        "priceAmount": str(round(amount, 3)), 
        "priceCurrency": currency, 
        "description": description, 
        "numPayments": 1
    }
    
    async with session.post(url, json=payload, headers=headers) as response:
        if response.status in [200, 201]:
            return await response.json()
        else:
            text = await response.text()
            raise Exception(f"Ошибка API ({response.status}): {text}")

# --- ОПЛАТА ЧЕРЕЗ GRAM ---
async def buy_stars_fragment(username: str, amount: int):
    async with FragmentClient(
        seed=FRAGMENT_SEED, api_key=TONCONSOLE_API_KEY, cookies=FRAGMENT_COOKIES
    ) as client:
        return await client.purchase_stars(username=username, amount=amount, payment_method=PaymentMethod.GRAM)

# --- СОСТОЯНИЯ ---
class ShopStates(StatesGroup):
    waiting_for_custom_stars = State()
    waiting_for_stars_username = State()
    waiting_for_gram_amount = State()
    waiting_for_gram_username = State()
    waiting_for_deposit_amount = State()
    waiting_for_receipt = State()
    waiting_for_review = State()
    admin_edit_gram = State()
    admin_edit_rate_rub = State()
    gw_waiting_prize = State()
    gw_waiting_winners_count = State()
    gw_waiting_date = State()
    gw_waiting_photo = State()
    gift_waiting_id = State()
    gift_waiting_user = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="⭐ Купить Stars"), KeyboardButton(text="💎 Купить Gram")],
        [KeyboardButton(text="💳 Пополнить баланс"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🎁 Бонусы"), KeyboardButton(text="📞 Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
    ])

# --- ГЛАВНОЕ МЕНЮ ---
@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await get_user(message.from_user.id) 
    await clean_chat(message.chat.id, message.message_id)
    await message.answer(f"🛍️ <b>Добро пожаловать в магазин!</b>\n\nВыбери нужный раздел:", reply_markup=get_main_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    
    # Удаляем сообщение, на котором нажали кнопку "Назад"
    await clean_chat(call.message.chat.id, call.message.message_id)
    
    # Отправляем ОДНО сообщение с меню, чтобы не спамить
    await call.message.answer(f"🛍️ <b>Главное меню</b>\n\nВыбери нужный раздел:", reply_markup=get_main_menu(), parse_mode="HTML")
    await call.answer()

# --- БОНУСЫ ---
@dp.message(F.text == "🎁 Бонусы")
async def menu_bonuses(message: Message):
    await clean_chat(message.chat.id, message.message_id)
    text = (
        "🎁 <b>Бонусы и Розыгрыши!</b>\n\n"
        "В нашем Telegram-канале прямо сейчас проходят активные розыгрыши! 🎉\n"
        "────────────────────\n"
        "🏆 <b>Что можно выиграть:</b>\n"
        "⭐ Звёзды Telegram\n"
        "🎁 Обычные подарки\n"
        "💎 NFT-подарки\n"
        "────────────────────\n"
        "Участвуйте и зарабатывайте на халяву! 😉"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Перейти в канал с розыгрышами", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# --- ПРОФИЛЬ ---
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    user = await get_user(message.from_user.id)
    bal = user.get("balance", 0.0)
    orders_hist = user.get("history_orders", [])
    deposits_hist = user.get("history_deposits", [])
    
    text = f"👤 <b>Ваш профиль</b>\n\n💰 <b>Баланс:</b> {bal:.2f} RUB\n\n"
    text += "📦 <b>Последние заказы:</b>\n"
    if not orders_hist: text += "Нет заказов.\n"
    else:
        for o in orders_hist[-5:]: text += f"• {o}\n"
        
    text += "\n💳 <b>Последние пополнения:</b>\n"
    if not deposits_hist: text += "Нет пополнений.\n"
    else:
        for d in deposits_hist[-5:]: text += f"• {d}\n"
        
    await clean_chat(message.chat.id, message.message_id)
    await message.answer(text, reply_markup=get_back_kb(), parse_mode="HTML")

# --- АДМИН ПАНЕЛЬ ---
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Цена 1 Gram: {SETTINGS['gram_price']} RUB", callback_data="admin_gram")],
        [InlineKeyboardButton(text=f"Цена 1 Star: {SETTINGS['rate_rub']} RUB", callback_data="admin_rate_rub")],
        [InlineKeyboardButton(text="📝 Отзывы", callback_data="admin_reviews")],
        [InlineKeyboardButton(text="🎉 Розыгрыши", callback_data="admin_giveaways")],
        [InlineKeyboardButton(text="🎁 Отправить подарок", callback_data="admin_send_gift")],
        [InlineKeyboardButton(text="⭐ Пополнить звёзды бота", callback_data="admin_topup_stars")],
        [InlineKeyboardButton(text="📋 Список ID подарков", callback_data="admin_list_gifts")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=kb, parse_mode="HTML")

# --- ПОПОЛНЕНИЕ ЗВЁЗД БОТА ---
@dp.callback_query(F.data == "admin_topup_stars")
async def admin_topup_stars(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Пополнение баланса бота",
        description="Покупка звёзд для баланса бота (для подарков)",
        payload="topup_stars_100",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="100 Stars", amount=100)]
    )
    await call.message.answer("⭐ Счёт на 100 звёзд отправлен. Оплати его, чтобы пополнить баланс бота.", reply_markup=get_back_kb())
    await call.answer()

@dp.pre_checkout_query(F.invoice_payload.startswith("topup_stars"))
async def pre_checkout_topup(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_topup(message: Message):
    payment = message.successful_payment
    if payment.invoice_payload.startswith("topup_stars"):
        amount = payment.total_amount
        await message.answer(f"✅ Баланс бота пополнен на <b>{amount} звёзд</b>!", parse_mode="HTML")
        await bot.send_message(ADMIN_ID, f"⭐ Бот пополнен на {amount} звёзд. Теперь можно отправлять подарки.")

# --- СПИСОК ID ПОДАРКОВ ---
@dp.callback_query(F.data == "admin_list_gifts")
async def admin_list_gifts(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    
    try:
        gifts = await bot.get_available_gifts()
        
        if not gifts.gifts:
            await call.message.answer("❌ Список подарков пуст или недоступен.", reply_markup=get_back_kb())
            await call.answer()
            return

        text = "🎁 <b>Доступные подарки (ID для отправки):</b>\n\n"
        for g in gifts.gifts[:30]:
            emoji = g.sticker.emoji if g.sticker else "🎁"
            text += f"<code>{g.id}</code> — {emoji} ({g.star_count} ⭐)\n"
        
        text += "\n<i>Скопируй ID (нажми на код) и вставь его при отправке подарка.</i>"
        await call.message.answer(text, parse_mode="HTML", reply_markup=get_back_kb())
    except Exception as e:
        await call.message.answer(f"❌ Ошибка получения списка: {e}\n\nУбедись, что у бота есть доступ к API подарков.", reply_markup=get_back_kb())
    await call.answer()

@dp.callback_query(F.data == "admin_gram")
async def admin_gram(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(ShopStates.admin_edit_gram)
    await call.message.answer("Введите новую цену за 1 Gram (в рублях):", reply_markup=get_back_kb())
    await call.answer()

@dp.callback_query(F.data == "admin_rate_rub")
async def admin_rate_rub(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(ShopStates.admin_edit_rate_rub)
    await call.message.answer("Введите новую цену за 1 звезду (в рублях):", reply_markup=get_back_kb())
    await call.answer()

@dp.callback_query(F.data == "admin_reviews")
async def admin_reviews(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    if not REVIEWS: 
        await call.message.answer("Отзывов нет.", reply_markup=get_back_kb())
    else:
        for r in REVIEWS[-5:]:
            admin_text = f"👤 @{r['user']}: {r['text']}"
            if r.get("photo_id"): await call.message.answer_photo(r["photo_id"], caption=admin_text)
            else: await call.message.answer(admin_text)
    await call.answer()

@dp.message(ShopStates.admin_edit_gram)
async def set_gram_price(message: Message, state: FSMContext):
    try: 
        SETTINGS['gram_price'] = float(message.text)
        await message.answer(f"✅ Цена Gram: {SETTINGS['gram_price']} RUB", reply_markup=get_back_kb())
    except: 
        await message.answer("❌ Ошибка.", reply_markup=get_back_kb())
    await state.clear()

@dp.message(ShopStates.admin_edit_rate_rub)
async def set_rate_rub(message: Message, state: FSMContext):
    try: 
        SETTINGS['rate_rub'] = float(message.text)
        await message.answer(f"✅ Цена 1 Star: {SETTINGS['rate_rub']} RUB", reply_markup=get_back_kb())
    except: 
        await message.answer("❌ Ошибка.", reply_markup=get_back_kb())
    await state.clear()

# ==========================================
# ОТПРАВКА ПОДАРКОВ (ОТ ЛИЦА БОТА)
# ==========================================

@dp.callback_query(F.data == "admin_send_gift")
async def admin_send_gift_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    
    await state.set_state(ShopStates.gift_waiting_id)
    await clean_chat(call.message.chat.id, call.message.message_id)
    await call.message.answer(
        "🎁 <b>Отправка подарка от бота</b>\n\n"
        "Введи <b>gift_id</b> подарка (посмотри в кнопке «📋 Список ID подарков»):",
        reply_markup=get_back_kb(),
        parse_mode="HTML"
    )
    await call.answer()

@dp.message(ShopStates.gift_waiting_id)
async def admin_gift_set_id(message: Message, state: FSMContext):
    await state.update_data(gift_id=message.text.strip())
    await state.set_state(ShopStates.gift_waiting_user)
    await clean_chat(message.chat.id, message.message_id)
    await message.answer("Введи <b>@username</b> или <b>ID</b> получателя:", reply_markup=get_back_kb(), parse_mode="HTML")

@dp.message(ShopStates.gift_waiting_user)
async def admin_gift_send(message: Message, state: FSMContext):
    data = await state.get_data()
    gift_id = data.get("gift_id")
    target = message.text.strip()
    
    await clean_chat(message.chat.id, message.message_id)
    
    user_id = None
    if target.startswith("@"):
        try:
            chat = await bot.get_chat(target)
            user_id = chat.id
        except Exception as e:
            await message.answer(f"❌ Не удалось найти пользователя: {e}", reply_markup=get_back_kb())
            await state.clear()
            return
    else:
        try:
            user_id = int(target)
        except:
            await message.answer("❌ Неверный формат ID.", reply_markup=get_back_kb())
            await state.clear()
            return
    
    try:
        await bot.send_gift(
            user_id=user_id, 
            gift_id=gift_id
        )
        await message.answer(f"✅ Подарок <code>{gift_id}</code> успешно отправлен пользователю <code>{user_id}</code> от бота!", reply_markup=get_back_kb(), parse_mode="HTML")
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка при отправке:</b>\n<code>{e}</code>\n\n"
            f"<i>Убедись, что на балансе бота достаточно звёзд. Пополни его через кнопку «⭐ Пополнить звёзды бота».</i>",
            reply_markup=get_back_kb(),
            parse_mode="HTML"
        )
    
    await state.clear()

# ==========================================
# ЛОГИКА РОЗЫГРЫШЕЙ
# ==========================================

@dp.callback_query(F.data == "admin_giveaways")
async def admin_giveaways(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать розыгрыш", callback_data="gw_create")],
        [InlineKeyboardButton(text="🏁 Завершить активный", callback_data="gw_finish")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ])
    await call.message.answer("🎉 <b>Управление розыгрышами</b>\n\nВыбери действие:", reply_markup=kb, parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "gw_create")
async def gw_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.gw_waiting_prize)
    await clean_chat(call.message.chat.id, call.message.message_id)
    await call.message.answer("🎁 Введи описание приза (например: '500 Telegram Stars'):", reply_markup=get_back_kb())
    await call.answer()

@dp.message(ShopStates.gw_waiting_prize)
async def gw_set_prize(message: Message, state: FSMContext):
    await state.update_data(gw_prize=message.text)
    await state.set_state(ShopStates.gw_waiting_winners_count)
    await clean_chat(message.chat.id, message.message_id)
    await message.answer("👥 Сколько победителей будет? (Введи число):", reply_markup=get_back_kb())

@dp.message(ShopStates.gw_waiting_winners_count)
async def gw_set_winners(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if count < 1: raise ValueError
    except:
        await message.answer("❌ Введи целое число больше 0.", reply_markup=get_back_kb())
        return
    
    await state.update_data(gw_winners=count)
    await state.set_state(ShopStates.gw_waiting_date)
    await clean_chat(message.chat.id, message.message_id)
    await message.answer("📅 Введи дату и время окончания в формате:\n<code>ДД.ММ ЧЧ:ММ</code>\n\nПример: <code>26.11 18:00</code>", parse_mode="HTML", reply_markup=get_back_kb())

@dp.message(ShopStates.gw_waiting_date)
async def gw_set_date(message: Message, state: FSMContext):
    # Убираем лишние пробелы для надежности
    date_str = " ".join(message.text.strip().split()) 
    
    try:
        now = datetime.now()
        end_time = datetime.strptime(f"{date_str}.{now.year}", "%d.%m.%Y %H:%M")
        if end_time < now:
            end_time = end_time.replace(year=now.year + 1)
    except ValueError:
        await message.answer(f"❌ Неверный формат! Ты ввел: <code>{message.text}</code>\n\nВведи как: <code>26.11 18:00</code>", parse_mode="HTML", reply_markup=get_back_kb())
        return
    
    await state.update_data(gw_end_time=end_time)
    await state.set_state(ShopStates.gw_waiting_photo)
    await clean_chat(message.chat.id, message.message_id)
    await message.answer("📸 Отправь фото для поста в канале (или напиши 'Нет'):", reply_markup=get_back_kb())

@dp.message(ShopStates.gw_waiting_photo, F.photo | F.text)
async def gw_set_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    prize = data.get("gw_prize")
    winners = data.get("gw_winners")
    end_time = data.get("gw_end_time")
    
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    
    await clean_chat(message.chat.id, message.message_id)
    
    gw_id = str(uuid.uuid4())[:8]
    
    post_text = (
        f"🎉 <b>РОЗЫГРЫШ!</b> 🎉\n\n"
        f"🎁 <b>Приз:</b> {prize}\n"
        f"👥 <b>Победителей:</b> {winners}\n"
        f"⏰ <b>Окончание:</b> {end_time.strftime('%d.%m в %H:%M')}\n\n"
        f"👇 Жми кнопку ниже, чтобы участвовать!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Участвовать", callback_data=f"join_gw_{gw_id}")]
    ])
    
    try:
        if photo_id:
            sent_msg = await bot.send_photo(CHANNEL_ID, photo_id, caption=post_text, reply_markup=kb, parse_mode="HTML")
        else:
            sent_msg = await bot.send_message(CHANNEL_ID, post_text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке в канал: {e}\n\nПроверь, что бот админ в канале и CHANNEL_ID верный.", reply_markup=get_back_kb())
        await state.clear()
        return

    GIVEAWAYS[gw_id] = {
        "channel_id": CHANNEL_ID,
        "prize": prize,
        "winners_count": winners,
        "end_time": end_time,
        "message_id": sent_msg.message_id,
        "participants": set(),
        "active": True
    }
    
    delay = (end_time - datetime.now()).total_seconds()
    if delay > 0:
        giveaway_tasks[gw_id] = asyncio.create_task(finish_giveaway_auto(gw_id, delay))
    else:
        await message.answer("⚠️ Дата уже прошла, розыгрыш завершится мгновенно.")

    await message.answer(f"✅ Розыгрыш создан и опубликован!\nID: <code>{gw_id}</code>", parse_mode="HTML", reply_markup=get_back_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("join_gw_"))
async def join_giveaway(call: CallbackQuery):
    gw_id = call.data.split("_")[2]
    if gw_id not in GIVEAWAYS or not GIVEAWAYS[gw_id]["active"]:
        await call.answer("❌ Этот розыгрыш уже завершен или не существует.", show_alert=True)
        return
        
    user_id = call.from_user.id
    GIVEAWAYS[gw_id]["participants"].add(user_id)
    await call.answer("✅ Ты успешно участвуешь в розыгрыше! Удачи!", show_alert=True)

async def finish_giveaway_auto(gw_id: str, delay: float):
    await asyncio.sleep(delay)
    if gw_id in GIVEAWAYS and GIVEAWAYS[gw_id]["active"]:
        await process_giveaway_finish(gw_id)

@dp.callback_query(F.data == "gw_finish")
async def gw_finish_manual(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    
    active_gw = None
    for gw_id, data in GIVEAWAYS.items():
        if data["active"]:
            active_gw = gw_id
            break
            
    if not active_gw:
        await call.answer("Нет активных розыгрышей.", show_alert=True)
        return
        
    await process_giveaway_finish(active_gw)
    await call.message.answer("🏁 Розыгрыш завершен вручную!", reply_markup=get_back_kb())
    await call.answer()

async def process_giveaway_finish(gw_id: str):
    gw = GIVEAWAYS[gw_id]
    gw["active"] = False
    
    participants = list(gw["participants"])
    winners_count = gw["winners_count"]
    
    try:
        await bot.edit_message_reply_markup(
            chat_id=gw["channel_id"],
            message_id=gw["message_id"],
            reply_markup=None
        )
    except: pass
    
    if len(participants) == 0:
        await bot.send_message(ADMIN_ID, f"⚠️ Розыгрыш {gw_id} завершен, но участников нет.")
        await bot.send_message(gw["channel_id"], f"🎉 Розыгрыш завершен! К сожалению, участников нет. 😔")
        return
        
    if len(participants) <= winners_count:
        winners = participants
    else:
        winners = random.sample(participants, winners_count)
        
    winners_text = "🏆 <b>Победители:</b>\n"
    for i, w_id in enumerate(winners):
        try:
            user = await bot.get_chat(w_id)
            name = user.first_name or "Пользователь"
        except:
            name = f"ID:{w_id}"
        winners_text += f"{i+1}. {name} (ID: {w_id})\n"
        
    await bot.send_message(gw["channel_id"], f"🎉 Розыгрыш завершен!\n\n{winners_text}\n\nПобедители получат приз <b>{gw['prize']}</b> в личку!", parse_mode="HTML")
    
    for w_id in winners:
        try:
            await bot.send_message(w_id, f"🎉 Поздравляем! Вы выиграли в розыгрыше!\n\nВаш приз: <b>{gw['prize']}</b>\n\nСвяжитесь с администратором @deporvida3 для получения.", parse_mode="HTML")
        except:
            await bot.send_message(ADMIN_ID, f"⚠️ Не удалось написать победителю {w_id}. Возможно, у него закрыты ЛС.")
            
    await bot.send_message(ADMIN_ID, f"🏁 Розыгрыш завершен!\nПриз: {gw['prize']}\nПобедители: {winners}")

# ==========================================
# КОНЕЦ ЛОГИКИ РОЗЫГРЫШЕЙ
# ==========================================

# --- МЕНЮ КНОПОК ---
@dp.message(F.text == "⭐ Купить Stars")
async def menu_buy_stars(message: Message, state: FSMContext):
    await state.clear()
    await clean_chat(message.chat.id, message.message_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50 ⭐", callback_data="stars_50"), InlineKeyboardButton(text="100 ⭐", callback_data="stars_100")],
        [InlineKeyboardButton(text="500 ⭐", callback_data="stars_500"), InlineKeyboardButton(text="Своё кол-во", callback_data="stars_custom")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("⭐ <b>Покупка Stars</b>\n\nВыбери количество:", reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "💎 Купить Gram")
async def menu_buy_gram(message: Message, state: FSMContext):
    await state.clear()
    await clean_chat(message.chat.id, message.message_id)
    await state.set_state(ShopStates.waiting_for_gram_amount)
    await message.answer(f"💎 <b>Покупка Gram</b>\n\nЦена: <b>{SETTINGS['gram_price']} RUB за 1 Gram</b>\n\nВведите, сколько Gram вы хотите купить:", reply_markup=get_back_kb(), parse_mode="HTML")

@dp.message(F.text == "💳 Пополнить баланс")
async def menu_deposit(message: Message, state: FSMContext):
    await state.clear()
    await clean_chat(message.chat.id, message.message_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 USDT (xRocket)", callback_data="dep_USDT")],
        [InlineKeyboardButton(text="💳 Карта BY", callback_data="dep_CARD_BY")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Выбери способ пополнения:", reply_markup=kb)

@dp.message(F.text == "📞 Поддержка")
async def menu_support(message: Message):
    await clean_chat(message.chat.id, message.message_id)
    await message.answer("👨‍💻 По всем вопросам пишите: @deporvida3", reply_markup=get_back_kb())

# --- ЛОГИКА STARS ---
@dp.callback_query(F.data.startswith("stars_") & (F.data != "stars_custom"))
async def choose_stars(call: CallbackQuery, state: FSMContext):
    amount = int(call.data.split("_")[1])
    await state.update_data(stars_amount=amount)
    await state.set_state(ShopStates.waiting_for_stars_username)
    await clean_chat(call.message.chat.id, call.message.message_id)
    await call.message.answer(f"Вы выбрали <b>{amount} Stars</b>.\n\nВведите ваш <b>@username</b>:", reply_markup=get_back_kb(), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "stars_custom")
async def custom_stars(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.waiting_for_custom_stars)
    await clean_chat(call.message.chat.id, call.message.message_id)
    await call.message.answer("Введите количество Stars (от 50 до 50000):", reply_markup=get_back_kb())
    await call.answer()

@dp.message(ShopStates.waiting_for_custom_stars)
async def process_custom_stars(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        if not (50 <= amount <= 50000): raise ValueError
    except ValueError:
        await message.answer("❌ Введите число от 50 до 50000.", reply_markup=get_back_kb())
        return
    await state.update_data(stars_amount=amount)
    await state.set_state(ShopStates.waiting_for_stars_username)
    await clean_chat(message.chat.id, message.message_id)
    await message.answer(f"Вы выбрали <b>{amount} Stars</b>.\n\nВведите ваш <b>@username</b>:", reply_markup=get_back_kb(), parse_mode="HTML")

@dp.message(ShopStates.waiting_for_stars_username)
async def process_stars_username(message: Message, state: FSMContext):
    username = message.text.strip()
    if not username.startswith("@"):
        await message.answer("❌ Введите юзернейм, начиная с @.", reply_markup=get_back_kb())
        return
        
    await state.update_data(stars_username=username)
    await clean_chat(message.chat.id, message.message_id)
    
    data = await state.get_data()
    stars_amount = data.get("stars_amount")
    
    if not stars_amount:
        await message.answer("❌ Ошибка сессии. Начните заново /start", reply_markup=get_back_kb())
        return
        
    price_rub = stars_amount * SETTINGS['rate_rub']
    price_byn = await convert_rub_to_byn(price_rub)
    
    user = await get_user(message.from_user.id)
    kb_buttons = []
    
    if user["balance"] >= price_rub:
         kb_buttons.append([InlineKeyboardButton(text="💰 Оплатить с баланса", callback_data="pay_stars_balance")])
    
    kb_buttons.append([InlineKeyboardButton(text="💵 Оплатить USDT (xRocket)", callback_data="pay_stars_usdt")])
    kb_buttons.append([InlineKeyboardButton(text="💳 Карта (Бел/Росс)", callback_data="pay_stars_card")])
    kb_buttons.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    await message.answer(
        f"Получатель: <b>{username}</b>\nК оплате: <b>{price_rub:.0f} RUB</b> (~{price_byn:.2f} BYN)\n\nВыбери способ оплаты:", 
        reply_markup=kb, 
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stars_amount = data.get("stars_amount")
    username = data.get("stars_username")
    
    if not stars_amount or not username:
        await call.answer("Сессия истекла, начните заново /start", show_alert=True)
        return

    price_rub = stars_amount * SETTINGS['rate_rub']
    user = await get_user(call.from_user.id)

    if "balance" in call.data:
        if user["balance"] >= price_rub:
            await update_balance(call.from_user.id, -price_rub)
            await add_order_history(call.from_user.id, f"Куплено {stars_amount} Stars за {price_rub:.0f} RUB (с баланса)")
            
            await clean_chat(call.message.chat.id, call.message.message_id)
            await call.message.answer("✅ <b>Списано с баланса!</b>")
            try:
                await buy_stars_fragment(username, stars_amount)
                await call.message.answer(f"🎉 Звезды отправлены! Оставьте отзыв (или /start для выхода):", reply_markup=get_back_kb())
                await state.set_state(ShopStates.waiting_for_review)
            except Exception as e:
                await bot.send_message(ADMIN_ID, f"❌ Ошибка Fragment: {e}")
                await call.message.answer("❌ Ошибка при выдаче звезд. Админ уведомлен.", reply_markup=get_back_kb())
        else:
            await call.answer("Недостаточно средств на балансе!", show_alert=True)
        await state.clear()
        return

    if "usdt" in call.data:
        await clean_chat(call.message.chat.id, call.message.message_id)
        amount_crypto = price_rub / SETTINGS['usdt_rate']
        try:
            invoice_data = await create_xrocket_invoice(amount=amount_crypto, currency="USDT", description=f"Покупка {stars_amount} Stars")
            links = invoice_data.get("links", {})
            pay_link = links.get("telegramBotLink") or links.get("webLink") or links.get("url")
            invoice_id = invoice_data.get("id")
            orders[invoice_id] = {"type": "stars", "username": username, "amount": stars_amount, "price_rub": price_rub, "user_id": call.from_user.id}
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_link)],
                [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_xrocket_{invoice_id}")],
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
            ])
            await call.message.answer(f"<b>Счет создан</b>\n\nТовар: {stars_amount} Stars\nСумма: {amount_crypto:.2f} USDT\n\nОплати по кнопке ниже.", reply_markup=kb, parse_mode="HTML")
            await state.clear()
        except Exception as e:
            await call.message.answer(f"❌ Ошибка xRocket: {e}", reply_markup=get_back_kb())
            
    elif "card" in call.data:
        await clean_chat(call.message.chat.id, call.message.message_id)
        price_byn = await convert_rub_to_byn(price_rub)
        order_id = str(uuid.uuid4())[:8]
        orders[order_id] = {"type": "stars_manual", "username": username, "amount": stars_amount, "user_id": call.from_user.id}
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я перевел / Отправить чек", callback_data=f"manual_{order_id}")],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
        ])
        text = (f"<b>Оплата Stars картой (Бел/Росс)</b>\n\n"
                f"1. Переведите <b>{price_byn:.2f} BYN</b> на:\n<code>{BY_CARD_NUMBER}</code>\n\n"
                f"2. Комментарий: <code>{call.from_user.id}</code>\n\n"
                f"⚠️ <b>После оплаты обязательно прикрепите чек!</b>")
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await state.clear()
    await call.answer()

# --- ПРОВЕРКА ОПЛАТЫ XROCKET (STARS) ---
@dp.callback_query(F.data.startswith("check_xrocket_"))
async def check_xrocket_payment(call: CallbackQuery, state: FSMContext):
    invoice_id = call.data.split("_")[2]
    order = orders.get(invoice_id)
    await call.answer("Проверяем оплату...")
    
    try:
        url = f"https://pay.api.xrocket.exchange/api/v1/invoice?invoiceId={invoice_id}"
        headers = {"Authorization": f"Bearer {XROCKET_API_KEY}"}
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("status", "").lower() == "paid":
                    await clean_chat(call.message.chat.id, call.message.message_id)
                    await call.message.answer("✅ <b>Оплата получена!</b>")
                    
                    if order and order.get("type") == "stars":
                        try:
                            await buy_stars_fragment(order["username"], order["amount"])
                            await add_order_history(order["user_id"], f"Куплено {order['amount']} Stars за {order['price_rub']:.0f} RUB (USDT)")
                            await call.message.answer(f"🎉 Звезды отправлены! Оставьте отзыв (или /start для выхода):", reply_markup=get_back_kb())
                            await state.set_state(ShopStates.waiting_for_review)
                        except Exception as e:
                            await bot.send_message(ADMIN_ID, f"❌ Ошибка Fragment: {e}")
                            await call.message.answer("❌ Ошибка при выдаче. Админ уведомлен.", reply_markup=get_back_kb())
                            
                    elif order and order.get("type") == "stars_manual":
                         await add_order_history(order["user_id"], f"Куплено {order['amount']} Stars за {order.get('price_rub', 0):.0f} RUB (Карта)")
                         try:
                            await buy_stars_fragment(order["username"], order["amount"])
                            await call.message.answer(f"🎉 Звезды отправлены! Оставьте отзыв (или /start для выхода):", reply_markup=get_back_kb())
                            await state.set_state(ShopStates.waiting_for_review)
                         except Exception as e:
                            await bot.send_message(ADMIN_ID, f"❌ Ошибка Fragment: {e}")
                            
                    del orders[invoice_id]
                else:
                    await call.answer("Оплата еще не поступила.", show_alert=True)
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)

# --- ОТПРАВКА ЧЕКА ВРУЧНУЮ ---
@dp.callback_query(F.data.startswith("manual_"))
async def manual_receipt(call: CallbackQuery, state: FSMContext):
    order_id = call.data.split("_")[1]
    await state.update_data(manual_order_id=order_id)
    await state.set_state(ShopStates.waiting_for_receipt)
    await clean_chat(call.message.chat.id, call.message.message_id)
    await call.message.answer("📸 Пожалуйста, отправьте фото или файл чека об оплате:", reply_markup=get_back_kb())
    await call.answer()

@dp.message(ShopStates.waiting_for_receipt, F.photo | F.document)
async def handle_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("manual_order_id")
    order = orders.get(order_id)
    
    if not order:
        await message.answer("❌ Заказ не найден.", reply_markup=get_back_kb())
        await state.clear()
        return
        
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    order["receipt_file_id"] = file_id
    order["user_id"] = message.from_user.id
    orders[order_id] = order
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{order_id}")]
    ])
    
    text = (f"🧾 <b>Новый чек на проверку!</b>\n"
            f"Пользователь: @{message.from_user.username} (ID: {message.from_user.id})\n"
            f"Товар: {order.get('amount')} Stars\n"
            f"Заказ ID: <code>{order_id}</code>")
            
    if "photo" in message:
        await bot.send_photo(ADMIN_ID, file_id, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await bot.send_document(ADMIN_ID, file_id, caption=text, reply_markup=kb, parse_mode="HTML")
        
    await message.answer("✅ Чек отправлен администратору на проверку. Ожидайте.", reply_markup=get_back_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_order(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    order_id = call.data.split("_")[1]
    order = orders.get(order_id)
    if not order: await call.answer("Заказ не найден"); return
    
    await clean_chat(call.message.chat.id, call.message.message_id)
    await call.message.answer(f"✅ Заказ {order_id} одобрен. Выдаю звезды...")
    
    try:
        await buy_stars_fragment(order["username"], order["amount"])
        await add_order_history(order["user_id"], f"Куплено {order['amount']} Stars (Одобрено вручную)")
        await bot.send_message(order["user_id"], "🎉 Ваш чек одобрен! Звезды отправлены.")
    except Exception as e:
        await call.message.answer(f"❌ Ошибка при выдаче: {e}")
    del orders[order_id]
    await call.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_order(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    order_id = call.data.split("_")[1]
    order = orders.get(order_id)
    if not order: await call.answer("Заказ не найден"); return
    
    await clean_chat(call.message.chat.id, call.message.message_id)
    await call.message.answer(f"❌ Заказ {order_id} отклонен.")
    await bot.send_message(order["user_id"], "❌ Ваш чек был отклонен. Свяжитесь с поддержкой.")
    del orders[order_id]
    await call.answer()

# --- ЛОГИКА ПОПОЛНЕНИЯ БАЛАНСА ---
@dp.callback_query(F.data.startswith("dep_"))
async def deposit_processing(call: CallbackQuery, state: FSMContext):
    method = call.data.split("_")[1]
    if method == "CARD_BY":
        await state.set_state(ShopStates.waiting_for_deposit_amount)
        await state.update_data(deposit_method="CARD_BY")
        await clean_chat(call.message.chat.id, call.message.message_id)
        await call.message.answer("Введите сумму пополнения в RUB:", reply_markup=get_back_kb())
    elif method == "USDT":
        await state.set_state(ShopStates.waiting_for_deposit_amount)
        await state.update_data(deposit_method="USDT")
        await clean_chat(call.message.chat.id, call.message.message_id)
        await call.message.answer("Введите сумму пополнения в RUB:", reply_markup=get_back_kb())
    await call.answer()

@dp.message(ShopStates.waiting_for_deposit_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount_rub = float(message.text)
        if amount_rub < 100:
            await message.answer("❌ Минимальная сумма 100 RUB.", reply_markup=get_back_kb())
            return
    except ValueError:
        await message.answer("❌ Введите число.", reply_markup=get_back_kb())
        return

    data = await state.get_data()
    method = data.get("deposit_method")
    await clean_chat(message.chat.id, message.message_id)

    if method == "CARD_BY":
        price_byn = await convert_rub_to_byn(amount_rub)
        order_id = str(uuid.uuid4())[:8]
        orders[order_id] = {"type": "deposit_card", "amount_rub": amount_rub, "user_id": message.from_user.id}
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я перевел / Отправить чек", callback_data=f"manual_{order_id}")],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
        ])
        text = (f"<b>Пополнение картой</b>\n\n"
                f"Переведите <b>{price_byn:.2f} BYN</b> на:\n<code>{BY_CARD_NUMBER}</code>\n\n"
                f"Комментарий: <code>{message.from_user.id}</code>\n\n"
                f"⚠️ <b>Обязательно прикрепите чек!</b>")
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        await state.clear()

    elif method == "USDT":
        amount_crypto = amount_rub / SETTINGS['usdt_rate']
        try:
            invoice_data = await create_xrocket_invoice(amount=amount_crypto, currency="USDT", description=f"Пополнение баланса на {amount_rub} RUB")
            links = invoice_data.get("links", {})
            pay_link = links.get("telegramBotLink") or links.get("webLink") or links.get("url")
            invoice_id = invoice_data.get("id")
            orders[invoice_id] = {"type": "deposit_usdt", "amount_rub": amount_rub, "user_id": message.from_user.id}
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_link)],
                [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_xrocket_{invoice_id}")],
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
            ])
            await message.answer(f"<b>Счет создан</b>\n\nСумма: {amount_crypto:.2f} USDT\n\nОплати по кнопке ниже.", reply_markup=kb, parse_mode="HTML")
            await state.clear()
        except Exception as e:
            await message.answer(f"❌ Ошибка xRocket: {e}", reply_markup=get_back_kb())

# --- ОТЗЫВЫ ---
@dp.message(ShopStates.waiting_for_review, F.photo | F.text)
async def handle_review(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or f"ID:{user_id}"
    
    if message.photo:
        text = message.caption or "Без текста"
        photo_id = message.photo[-1].file_id
        REVIEWS.append({"user": username, "text": text, "photo_id": photo_id})
        await bot.send_photo(ADMIN_ID, photo_id, caption=f"📝 <b>Новый отзыв (с фото)!</b>\n\n👤 @{username}\n💬 {text}", parse_mode="HTML")
        await message.answer("🙏 Спасибо за ваш отзыв с фото! Он отправлен администратору.", reply_markup=get_back_kb())
    else:
        text = message.text
        if text == "/start":
            await state.clear()
            await start(message, state)
            return
        REVIEWS.append({"user": username, "text": text, "photo_id": None})
        await bot.send_message(ADMIN_ID, f"📝 <b>Новый текстовый отзыв!</b>\n\n👤 @{username}\n💬 {text}", parse_mode="HTML")
        await message.answer("🙏 Спасибо за ваш отзыв! Он отправлен администратору.", reply_markup=get_back_kb())
    
    await state.clear()

# --- ЗАПУСК БОТА ---
async def main():
    global bot, session, db_pool
    bot = Bot(token=BOT_TOKEN)
    session = aiohttp.ClientSession()
    
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if DATABASE_URL:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL)
            await init_db()
            print("✅ База данных подключена")
        except Exception as e:
            print(f"⚠️ Ошибка БД: {e}")
            db_pool = None
    else:
        print("⚠️ DATABASE_URL не найден.")

    Thread(target=run_flask, daemon=True).start()
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
