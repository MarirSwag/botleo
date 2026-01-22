import asyncio
import aiosqlite
import logging
import time
import os
import html
import traceback
import random
from asyncio import Lock

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- НАСТРОЙКИ ---
TOKEN = "8520560664:AAHeSCOIVLcqwncSEc2YrC6tVULJm_lUw1k" 
CHANNEL_ID = -1003592097094
CHANNEL_LINK = "https://t.me/StandLeoPromo1h"
ADMIN_PASSWORD = "maks201015"
MODER_PASSWORD = "Conexio"
ADMIN_ID = 1967888210

# Время жизни промокода (23 часа 30 минут = 84600 секунд)
CODE_LIFETIME = 84600 

# Настройка путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'economy_bot.db')

# --- ЗАПУСК (БЕЗ ПРОКСИ!) ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN) 
dp = Dispatcher()

# --- ГЛОБАЛЬНЫЕ БЛОКИРОВКИ ---
user_last_click = {}
purchase_locks = {}
dice_cooldown = {} 
dice_locks = {}
robbery_cooldown = {} 
robbery_locks = {}
transfer_locks = {}

class BotStates(StatesGroup):
    auth_admin = State()
    auth_moder = State()
    is_admin = State()
    is_moderator = State()
    wait_promo_data = State()
    wait_balance_action = State()
    wait_broadcast = State()
    wait_clear_confirm = State()
    wait_dice_bet = State()
    wait_transfer_id = State()
    wait_transfer_amount = State()
    wait_wipe_confirm = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            username TEXT,
            coins REAL DEFAULT 0,
            max_coins REAL DEFAULT 0,
            referrer_id INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 0,
            last_slots INTEGER DEFAULT 0)""")
        
        await db.execute("""CREATE TABLE IF NOT EXISTS purchases (
            user_id INTEGER, 
            timestamp INTEGER)""")
        
        await db.execute("""CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY, 
            type TEXT, 
            is_used INTEGER DEFAULT 0,
            added_at INTEGER DEFAULT 0)""")
        
        try: await db.execute("ALTER TABLE users ADD COLUMN last_slots INTEGER DEFAULT 0")
        except: pass
        try: await db.execute("ALTER TABLE users ADD COLUMN max_coins REAL DEFAULT 0")
        except: pass
        try: 
            await db.execute("ALTER TABLE promo_codes ADD COLUMN added_at INTEGER DEFAULT 0")
            now = int(time.time())
            await db.execute("UPDATE promo_codes SET added_at = ? WHERE added_at = 0", (now,))
        except: pass

        await db.execute("UPDATE users SET max_coins = coins WHERE max_coins < coins")
        
        await db.execute("CREATE TABLE IF NOT EXISTS settings (maintenance INTEGER DEFAULT 0)")
        async with db.execute("SELECT count(*) FROM settings") as c:
            if (await c.fetchone())[0] == 0:
                await db.execute("INSERT INTO settings VALUES (0)")
        
        now = int(time.time())
        await db.execute("DELETE FROM purchases WHERE timestamp < ?", (now - 86400,))
        await db.commit()

# --- ФОНОВАЯ ЗАДАЧА ---
async def clean_expired_codes_loop():
    while True:
        try:
            await asyncio.sleep(3600) 
            now = int(time.time())
            limit = now - CODE_LIFETIME 
            async with aiosqlite.connect(DB_PATH, timeout=30) as db:
                await db.execute("DELETE FROM promo_codes WHERE added_at < ? AND is_used = 0", (limit,))
                await db.commit()
        except Exception as e:
            logging.error(f"Cleaner error: {e}")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def check_maintenance(user_id: int) -> bool:
    if user_id == ADMIN_ID: return False
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        try:
            m = await (await db.execute("SELECT maintenance FROM settings")).fetchone()
            return m and m[0] == 1
        except: return False

async def add_coins(user_id: int, amount: float, update_stats: bool = True):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        if amount > 0:
            if update_stats:
                await db.execute("UPDATE users SET coins = coins + ?, max_coins = max_coins + ? WHERE user_id = ?", (amount, amount, user_id))
            else:
                await db.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
        else:
            await db.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def check_sub(user_id):
    if user_id == ADMIN_ID: return True
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return m.status in ["member", "administrator", "creator"]
    except: return False

# --- КЛАВИАТУРЫ ---
def get_main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="⛏ Заработать"), KeyboardButton(text="🎮 Мини-игры")],
        [KeyboardButton(text="🛍 Магазин"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏆 ТОП-10"), KeyboardButton(text="🎁 Рефералы")]
    ], resize_keyboard=True)

def get_games_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎰 Слоты"), KeyboardButton(text="🎲 Кубик"), KeyboardButton(text="🔫 Ограбление")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)

def get_robbery_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚬 Карманник (50 🪙)", callback_data="rob_easy")],
        [InlineKeyboardButton(text="🏠 Взлом Хаты (200 🪙)", callback_data="rob_medium")],
        [InlineKeyboardButton(text="🏦 Банк (1000 🪙)", callback_data="rob_hard")]
    ])

def get_admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📥 Добавить коды")],
        [KeyboardButton(text="💰 Управление балансом"), KeyboardButton(text="📢 Рассылка")],
        [KeyboardButton(text="🗑 Очистить коды"), KeyboardButton(text="🧨 ВАЙП (Сброс)")],
        [KeyboardButton(text="⚙️ Тех. Режим"), KeyboardButton(text="🚪 Выйти из панели")]
    ], resize_keyboard=True)

def get_moder_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📥 Добавить коды")], [KeyboardButton(text="🚪 Выйти из панели")]], resize_keyboard=True)

# --- ГЛАВНЫЙ ОБРАБОТЧИК ---
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    try:
        await state.clear()
        uid = message.from_user.id
        uname = html.escape(message.from_user.full_name)
        args = message.text.split() if message.text else []
        ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0

        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", (uid, uname, ref_id))
            if ref_id != 0 and ref_id != uid:
                await db.execute("UPDATE users SET referrer_id = ? WHERE user_id = ? AND is_active = 0 AND referrer_id = 0", (ref_id, uid))
            try: await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (uname, uid))
            except: pass
            await db.commit()

            if await check_maintenance(uid): return await message.answer("🚧 <b>Бот на тех. обслуживании</b>", parse_mode="HTML")

            if await check_sub(uid):
                user = await (await db.execute("SELECT is_active, referrer_id FROM users WHERE user_id = ?", (uid,))).fetchone()
                if user and user[0] == 0:
                    await db.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", (uid,))
                    ref_id_db = user[1]
                    if ref_id_db > 0 and ref_id_db != uid:
                        await db.execute("UPDATE users SET coins = coins + 250, max_coins = max_coins + 250 WHERE user_id = ?", (ref_id_db,))
                        try: await bot.send_message(ref_id_db, "💰 Начислен бонус <b>250 монет</b> за друга!", parse_mode="HTML")
                        except: pass
                    await db.commit()
                
                welcome_text = (
                    f"👋 <b>Привет, {uname}!</b>\n\n"
                    "<b>📚 ИНСТРУКЦИЯ ПО КНОПКАМ:</b>\n"
                    "⛏ <b>Заработать</b> — получай 2.5 монеты за каждый клик.\n\n"
                    "🎮 <b>Мини-игры:</b>\n"
                    "🎰 <b>Слоты</b> — бесплатный бонус раз в день.\n"
                    "🎲 <b>Кубик</b> — делай ставки и лови удачу (x2).\n"
                    "🔫 <b>Ограбление</b> — воруй у других игроков!\n"
                    "   ├ 🚬 <b>Карманник:</b> Малый риск.\n"
                    "   ├ 🏠 <b>Взлом хаты:</b> Средний риск.\n"
                    "   └ 🏦 <b>Банк:</b> Огромный риск, но огромный куш!\n"
                    "   <i>(Осторожно, полиция штрафует!)</i>\n\n"
                    "🛍 <b>Магазин</b> — покупай промокоды (Лимит: 5 шт.).\n"
                    "👤 <b>Профиль</b> — переводы и баланс.\n"
                    "🎁 <b>Рефералы</b> — зови друзей и получай +250 монет.\n\n"
                    "👇 <b>Начинай игру:</b>"
                )
                await message.answer(welcome_text, reply_markup=get_main_kb(), parse_mode="HTML")
            else:
                ikb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=CHANNEL_LINK)], [InlineKeyboardButton(text="✅ Проверить", callback_data="recheck")]])
                await message.answer("🛡 Для использования бота подпишитесь на канал!", reply_markup=ikb)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")
        logging.error(traceback.format_exc())

# --- ГЛАВНОЕ МЕНЮ ---
@dp.message(F.text.contains("Заработать"), StateFilter("*"))
async def clicker(message: types.Message, state: FSMContext):
    await state.clear() 
    uid = message.from_user.id
    if not await check_sub(uid): return await message.answer("🛑 <b>Ошибка!</b>\nВы не подписаны на канал.", parse_mode="HTML")
    if await check_maintenance(uid): return await message.answer("🚧 Тех. работы")
    
    now = time.time()
    if uid in user_last_click and now - user_last_click[uid] < 0.7: return 
    user_last_click[uid] = now
    
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        res = await (await db.execute("SELECT coins FROM users WHERE user_id = ?", (uid,))).fetchone()
        bal = res[0] if res else 0

    if bal >= 3000: return await message.answer("⛔️ Лимит 3000 монет на клики!")
    await add_coins(uid, 2.5)
    await message.answer(f"✨ +2.5 🪙 | Баланс: <b>{(bal + 2.5):.1f}</b>", parse_mode="HTML")

@dp.message(F.text.contains("Профиль"), StateFilter("*"))
async def profile(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        res = await (await db.execute("SELECT coins, max_coins FROM users WHERE user_id = ?", (uid,))).fetchone()
    c = res[0] if res else 0
    mc = res[1] if res else 0
    
    ikb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💸 Перевести другу", callback_data="transfer_start")]])
    await message.answer(
        f"👤 <b>ПРОФИЛЬ</b>\n💰 Баланс: <b>{c:.1f}</b>\n🏆 Всего заработано: <b>{mc:.1f}</b>\n🆔 Твой ID: <code>{uid}</code>", 
        reply_markup=ikb,
        parse_mode="HTML"
    )

# --- ПЕРЕВОДЫ ---
@dp.callback_query(F.data == "transfer_start")
async def start_transfer(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("📝 <b>Введите ID игрока</b>:", parse_mode="HTML")
    await state.set_state(BotStates.wait_transfer_id)
    await call.answer()

@dp.message(BotStates.wait_transfer_id)
async def process_transfer_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ ID должен быть числом!")
    target_id = int(message.text)
    if target_id == message.from_user.id: return await message.answer("❌ Нельзя переводить себе!")

    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        exists = await (await db.execute("SELECT count(*) FROM users WHERE user_id = ?", (target_id,))).fetchone()
        if exists[0] == 0: return await message.answer("❌ Игрок не найден!")

    await state.update_data(target_id=target_id)
    await message.answer("💰 <b>Введите сумму</b> (Комиссия 15%, мин. 50):", parse_mode="HTML")
    await state.set_state(BotStates.wait_transfer_amount)

@dp.message(BotStates.wait_transfer_amount)
async def process_transfer_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Сумма должна быть числом!")
    amount = int(message.text)
    if amount < 50: return await message.answer("⚠️ Минимум 50 монет.")
    
    data = await state.get_data()
    target_id = data['target_id']
    uid = message.from_user.id
    
    if uid not in transfer_locks: transfer_locks[uid] = Lock()
    async with transfer_locks[uid]:
        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            sender = await (await db.execute("SELECT coins, username FROM users WHERE user_id = ?", (uid,))).fetchone()
            if sender[0] < amount: return await message.answer(f"❌ Мало средств! Баланс: {sender[0]:.1f}")
            
            commission = int(amount * 0.15)
            final_amount = amount - commission
            
            await db.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, uid))
            await db.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (final_amount, target_id))
            await db.commit()
            
            await message.answer(f"✅ <b>Переведено!</b>\n💸 Комиссия: {commission}\n📥 Придет: {final_amount}", parse_mode="HTML")
            try: await bot.send_message(target_id, f"💸 <b>ПЕРЕВОД!</b>\nОт: {sender[1]}\nСумма: <b>{final_amount}</b>", parse_mode="HTML")
            except: pass
    await state.clear()

@dp.message(F.text.contains("ТОП-10"), StateFilter("*"))
async def top_players(message: types.Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute("SELECT username, max_coins, coins FROM users ORDER BY max_coins DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
    
    text = "🏆 <b>ТОП-10 ИГРОКОВ</b>\n<i>(Всего заработано | Баланс)</i>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    for i, row in enumerate(rows, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
        text += f"{medal} <b>{row[0]}</b>\n    └ 🏆 {row[1]:.1f} | 💰 {row[2]:.1f}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text.contains("Рефералы"), StateFilter("*"))
async def refer(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    me = await bot.get_me()
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        cnt = await (await db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ? AND is_active = 1", (uid,))).fetchone()
    await message.answer(f"👥 Приглашено: <b>{cnt[0]}</b>\n🔗 Ссылка:\n<code>https://t.me/{me.username}?start={uid}</code>", parse_mode="HTML")

@dp.message(F.text.contains("Магазин"), StateFilter("*"))
async def shop(message: types.Message, state: FSMContext):
    try:
        await state.clear()
        ikb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎟 Купить промокод", callback_data="buy_common")],
            [InlineKeyboardButton(text="💎 Редкий (2000 🪙)", callback_data="buy_rare")]
        ])
        await message.answer("🛍 <b>МАГАЗИН</b>\n\n⚡️ Только свежие коды (менее 23.5ч)\n📅 Лимит: 5 шт.\n\n💸 <b>Цена:</b>\n• 1-3 шт: 500\n• 4 шт: 1000\n• 5 шт: 1500", reply_markup=ikb, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

# --- ИГРЫ ---
@dp.message(F.text.contains("Мини-игры"), StateFilter("*"))
async def games_menu(message: types.Message, state: FSMContext):
    await state.clear()
    if not await check_sub(message.from_user.id): return await message.answer("🛑 Подпишитесь!")
    await message.answer("🎮 Выберите игру:", reply_markup=get_games_kb())

@dp.message(F.text.contains("Назад"), StateFilter("*"))
async def back_main(message: types.Message, state: FSMContext):
    await state.clear() 
    await message.answer("🏠 Главное меню:", reply_markup=get_main_kb())

@dp.message(F.text.contains("Слоты"))
async def slots_game(message: types.Message):
    uid = message.from_user.id
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        res = await (await db.execute("SELECT last_slots FROM users WHERE user_id = ?", (uid,))).fetchone()
        if res and res[0] and now - res[0] < 86400:
            return await message.answer(f"⏳ Жди {86400-(now-res[0])} сек.")
        
        msg = await message.answer_dice(emoji="🎰")
        val = msg.dice.value
        win = 500 if val == 64 else 150 if val == 1 else 50 if val in [22, 43] else 0
        txt = "🔥 ДЖЕКПОТ!" if val == 64 else "🔔 Победа!" if win > 0 else "😔 Пусто"

        await asyncio.sleep(4)
        if win > 0: await add_coins(uid, win)
        await db.execute("UPDATE users SET last_slots = ? WHERE user_id = ?", (now, uid))
        await db.commit()
        await message.answer(f"{txt} +{win}")

@dp.message(F.text.contains("Кубик"))
async def dice_bet_ask(message: types.Message, state: FSMContext):
    await message.answer("🎲 <b>Кубик</b>\n1-3: -ставка\n4: возврат\n5: x1.5\n6: x2\n\n📝 Введи ставку:", reply_markup=get_games_kb(), parse_mode="HTML")
    await state.set_state(BotStates.wait_dice_bet)

@dp.message(BotStates.wait_dice_bet)
async def dice_bet_process(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if message.text == "⬅️ Назад":
        await state.clear()
        return await message.answer("🏠 Меню", reply_markup=get_games_kb())
    if not message.text.isdigit(): return await message.answer("⚠️ Число!")
    bet = int(message.text)
    if not (10 <= bet <= 1000): return await message.answer("⚠️ Ставка от 10 до 1000!") 

    if uid not in dice_locks: dice_locks[uid] = Lock()
    async with dice_locks[uid]:
        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            bal = (await (await db.execute("SELECT coins FROM users WHERE user_id=?", (uid,))).fetchone())[0]
            if bal < bet: return await message.answer("❌ Мало монет")
            await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (bet, uid))
            await db.commit()
        
        msg = await message.answer_dice(emoji="🎲")
        await asyncio.sleep(3.5)
        val = msg.dice.value
        
        mult = 0
        if val == 4: mult = 1
        elif val == 5: mult = 1.5
        elif val == 6: mult = 2
        
        win = int(bet * mult)
        if win > 0:
            is_real_win = mult > 1
            await add_coins(uid, win, update_stats=is_real_win)
        
        res = f"💀 {val}. Проигрыш" if mult == 0 else f"🤝 {val}. Возврат" if mult == 1 else f"🔥 {val}. +{win}"
        await message.answer(f"{res}\nБаланс: {int(bal-bet+win)}", reply_markup=get_games_kb())

# --- ОГРАБЛЕНИЕ ---
@dp.message(F.text.contains("Ограбление"))
async def robbery_menu(message: types.Message):
    if not await check_sub(message.from_user.id): return await message.answer("🛑 Подпишитесь!")
    
    uid = message.from_user.id
    now = time.time()
    if uid in robbery_cooldown and now - robbery_cooldown[uid] < 600:
        return await message.answer(f"⏳ Полиция на хвосте! Жди {int((600 - (now - robbery_cooldown[uid])) // 60)} мин.")

    await message.answer(
        "🔫 <b>ЦЕЛИ:</b>\n"
        "🚬 <b>Карманник</b> (50)\nШанс: 50% | Улов: 2-4% | Штраф: 150\n"
        "🏠 <b>Взлом</b> (200)\nШанс: 30% | Улов: 5-10% | Штраф: 600\n"
        "🏦 <b>Банк</b> (1000)\nШанс: 15% | Улов: 15-25% | Штраф: 3000",
        reply_markup=get_robbery_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("rob_"))
async def robbery_process(call: types.CallbackQuery):
    uid = call.from_user.id
    mode = call.data.split("_")[1]
    now = time.time()

    if uid in robbery_cooldown and now - robbery_cooldown[uid] < 600:
        return await call.answer("⏳ Кулдаун!", show_alert=True)

    settings = {
        "easy":   {"cost": 50, "min_victim": 200, "chance": 50, "steal": (2, 4), "fine": 150, "name": "Карманник"},
        "medium": {"cost": 200, "min_victim": 1000, "chance": 30, "steal": (5, 10), "fine": 600, "name": "Взлом Хаты"},
        "hard":   {"cost": 1000, "min_victim": 5000, "chance": 15, "steal": (15, 25), "fine": 3000, "name": "Ограбление Банка"}
    }
    s = settings[mode]

    if uid not in robbery_locks: robbery_locks[uid] = Lock()
    async with robbery_locks[uid]:
        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            attacker = await (await db.execute("SELECT coins, username FROM users WHERE user_id=?", (uid,))).fetchone()
            if attacker[0] < s["cost"]: return await call.answer(f"❌ Не хватает {s['cost']} монет!", show_alert=True)

            victim = await (await db.execute("SELECT user_id, coins, username FROM users WHERE coins > ? AND user_id != ? ORDER BY RANDOM() LIMIT 1", (s["min_victim"], uid))).fetchone()
            if not victim: return await call.message.edit_text("🕵️ Нет богатых жертв...")

            await db.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (s["cost"], uid))
            robbery_cooldown[uid] = now

            if random.randint(1, 100) <= s["chance"]:
                percent = random.randint(*s["steal"]) / 100
                steal_amt = int(victim[1] * percent)
                
                await db.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (steal_amt, victim[0]))
                await db.execute("UPDATE users SET coins = coins + ?, max_coins = max_coins + ? WHERE user_id = ?", (steal_amt, steal_amt, uid))
                await db.commit()
                
                await call.message.edit_text(f"🔫 <b>УСПЕХ!</b>\nЖертва: {victim[2]}\nУкрадено: <b>{steal_amt}</b> 🪙", parse_mode="HTML")
                try: await bot.send_message(victim[0], f"🕵️ <b>ВАС ОГРАБИЛИ!</b>\nИгрок {attacker[1]} украл {steal_amt} монет.", parse_mode="HTML")
                except: pass
            else:
                loss = s["cost"] + s["fine"]
                await db.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (s["fine"], uid))
                await db.commit()
                await call.message.edit_text(f"🚓 <b>ПОЙМАЛИ!</b>\nШтраф и взнос: -{loss} монет.", parse_mode="HTML")

# --- МАГАЗИН И АДМИНКА ---
@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(call: types.CallbackQuery):
    if "rare" in call.data: return await call.answer("🚫 Нет в наличии!", show_alert=True)
    uid = call.from_user.id
    if uid not in purchase_locks: purchase_locks[uid] = Lock()
    async with purchase_locks[uid]:
        now, one_day = int(time.time()), int(time.time()) - 86400
        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            await db.execute("DELETE FROM purchases WHERE timestamp < ?", (one_day,))
            cnt = (await (await db.execute("SELECT COUNT(*) FROM purchases WHERE user_id=? AND timestamp > ?", (uid, one_day))).fetchone())[0]
            if cnt >= 5: return await call.answer("🚫 Лимит 5 шт!", show_alert=True)
            price = 500 if cnt < 3 else 1000 if cnt == 3 else 1500
            
            bal = (await (await db.execute("SELECT coins FROM users WHERE user_id=?", (uid,))).fetchone())[0]
            if bal < price: return await call.answer(f"❌ Нужно {price} монет", show_alert=True)
            
            promo = await (await db.execute("SELECT code FROM promo_codes WHERE is_used=0 AND added_at > ? LIMIT 1", (now-CODE_LIFETIME,))).fetchone()
            if not promo: return await call.answer("😔 Коды закончились", show_alert=True)
            
            await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (price, uid))
            await db.execute("UPDATE promo_codes SET is_used=1 WHERE code=?", (promo[0],))
            await db.execute("INSERT INTO purchases (user_id, timestamp) VALUES (?, ?)", (uid, now))
            await db.commit()
            
            msg = f"✅ Куплено за <b>{price}</b>!\nКод: <code>{promo[0]}</code>"
            if cnt in [2, 3]: msg += f"\n⚠️ След. цена выше!"
            await call.message.answer(msg, parse_mode="HTML")
            await call.answer()

@dp.message(Command("admin"))
async def admin_cmd(message: types.Message, state: FSMContext): 
    await message.answer("🔒 Введите пароль:")
    await state.set_state(BotStates.auth_admin)

@dp.message(BotStates.auth_admin)
async def auth_a(message: types.Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD: 
        await message.answer("🔓 Панель Администратора", reply_markup=get_admin_kb())
        await state.set_state(BotStates.is_admin)
    else: 
        await message.answer("❌ Неверный пароль")

@dp.message(Command("admin2"))
async def moder_cmd(message: types.Message, state: FSMContext):
    await message.answer("🔑 Введите пароль Модератора:")
    await state.set_state(BotStates.auth_moder)

@dp.message(BotStates.auth_moder)
async def auth_m(message: types.Message, state: FSMContext):
    if message.text == MODER_PASSWORD:
        await message.answer("🔑 Панель Модератора", reply_markup=get_moder_kb())
        await state.set_state(BotStates.is_moderator)
    else:
        await message.answer("❌ Неверный пароль")

@dp.message(F.text == "💰 Управление балансом", StateFilter(BotStates.is_admin))
async def balance_start(message: types.Message, state: FSMContext):
    await message.answer("📝 Введите: ID СУММА\nПример: 12345 500")
    await state.set_state(BotStates.wait_balance_action)

@dp.message(BotStates.wait_balance_action)
async def balance_process(message: types.Message, state: FSMContext):
    try: 
        parts = message.text.split()
        target_id, amount = int(parts[0]), float(parts[1])
        await add_coins(target_id, amount)
        await message.answer(f"✅ Баланс {target_id} изменен на {amount}")
        try: await bot.send_message(target_id, f"💰 Админ изменил баланс на {amount}")
        except: pass
    except: 
        await message.answer("❌ Ошибка формата")
    await state.set_state(BotStates.is_admin)

@dp.message(F.text == "📥 Добавить коды", StateFilter(BotStates.is_admin, BotStates.is_moderator))
async def add_codes_btn(message: types.Message, state: FSMContext):
    await message.answer("Выберите тип:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Обычные", callback_data="add_common")]]))

@dp.callback_query(F.data=="add_common", StateFilter(BotStates.is_admin, BotStates.is_moderator))
async def add_choice(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(ptype="common")
    await call.message.answer("📝 Пришлите коды через пробел или enter:")
    await state.set_state(BotStates.wait_promo_data)

@dp.message(BotStates.wait_promo_data)
async def save_codes(message: types.Message, state: FSMContext):
    data = await state.get_data()
    codes = message.text.replace('\n', ' ').split()
    now = int(time.time())
    count = 0
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        for c in codes: 
            try:
                await db.execute("INSERT INTO promo_codes (code, type, is_used, added_at) VALUES (?, ?, 0, ?)", (c.strip(), data['ptype'], now))
                count += 1
            except: pass
        await db.commit()
    await message.answer(f"✅ Добавлено: {count}")
    if message.from_user.id == ADMIN_ID:
        await state.set_state(BotStates.is_admin)
    else:
        await state.set_state(BotStates.is_moderator)

@dp.message(F.text=="🗑 Очистить коды", StateFilter(BotStates.is_admin))
async def clear_codes(message: types.Message):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db: 
        await db.execute("DELETE FROM promo_codes WHERE is_used=0")
        await db.commit()
    await message.answer("✅ База кодов очищена")

# --- ВАЙП ---
@dp.message(F.text=="🧨 ВАЙП (Сброс)", StateFilter(BotStates.is_admin))
async def ask_wipe(message: types.Message, state: FSMContext):
    await message.answer("⚠️ <b>ВНИМАНИЕ!</b>\nСброс ВСЕЙ экономики.\nНапишите <b>подтверждаю</b>:", parse_mode="HTML")
    await state.set_state(BotStates.wait_wipe_confirm)

@dp.message(BotStates.wait_wipe_confirm)
async def confirm_wipe(message: types.Message, state: FSMContext):
    if message.text.lower() == "подтверждаю":
        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            await db.execute("UPDATE users SET coins = 0, max_coins = 0")
            await db.execute("DELETE FROM purchases") 
            await db.commit()
        await message.answer("✅ <b>ЭКОНОМИКА СБРОШЕНА!</b>", parse_mode="HTML")
    else:
        await message.answer("❌ Отмена.")
    await state.set_state(BotStates.is_admin)

@dp.message(F.text == "⚙️ Тех. Режим", StateFilter(BotStates.is_admin))
async def toggle_maintenance(message: types.Message):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        current = await (await db.execute("SELECT maintenance FROM settings")).fetchone()
        new_value = 0 if current and current[0] == 1 else 1
        await db.execute("UPDATE settings SET maintenance = ?", (new_value,))
        await db.commit()
        
        status = "🔴 ВКЛЮЧЁН" if new_value == 1 else "🟢 ВЫКЛЮЧЕН"
        await message.answer(f"⚙️ Тех. режим: {status}")

@dp.message(F.text == "📊 Статистика", StateFilter(BotStates.is_admin, BotStates.is_moderator))
async def stats(message: types.Message):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        u = (await (await db.execute("SELECT count(*) FROM users")).fetchone())[0]
        c = (await (await db.execute("SELECT count(*) FROM promo_codes WHERE is_used=0")).fetchone())[0]
        await message.answer(f"📊 Юзеров: {u}\n🎟 Кодов: {c}")

@dp.message(F.text == "📢 Рассылка", StateFilter(BotStates.is_admin))
async def broadcast_start(message: types.Message, state: FSMContext):
    await message.answer("📢 Пришлите пост.")
    await state.set_state(BotStates.wait_broadcast)

@dp.message(BotStates.wait_broadcast)
async def broadcast_process(message: types.Message, state: FSMContext):
    msg = await message.answer("⏳ Рассылка...")
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    cnt = 0
    for u in users:
        try:
            await message.copy_to(u[0])
            cnt += 1
            await asyncio.sleep(0.05)
        except: pass
    await msg.edit_text(f"✅ Отправлено: {cnt}")
    await state.set_state(BotStates.is_admin)

@dp.message(F.text == "🚪 Выйти из панели")
async def exit_panel(message: types.Message, state: FSMContext): 
    await state.clear()
    await message.answer("🚪 Выход", reply_markup=get_main_kb())

async def main():
    await init_db()
    asyncio.create_task(clean_expired_codes_loop()) 
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
