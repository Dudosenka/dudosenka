#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Бот Дудосенька – полная версия с ответом на реплаи.
- Отвечает на ЛС, упоминания, а также на ответы (реплаи) к своим сообщениям.
- Все остальные функции сохранены.
"""

import os
import sys
import asyncio
import json
import random
import time
import warnings
import logging
from datetime import datetime
from typing import Dict, List

# ========== НАСТРОЙКА ПРОКСИ (если нужен) ==========
PROXY_URL = None   # Пример: "socks5://127.0.0.1:1080" или "http://proxy:8080"
# ===================================================

# Отключаем системные прокси, если не заданы
if not PROXY_URL:
    for var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        os.environ[var] = ""

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)

import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# ======================= КОНФИГУРАЦИЯ =======================
TELEGRAM_TOKEN = "НОВЫЙ_ТОКЕН_TELEGRAM"      # Замените на свой
GEMINI_API_KEY = "НОВЫЙ_КЛЮЧ_GEMINI"          # Замените на свой
BOSS_ID = 1383365424
RESPONSE_DELAY_SECONDS = 300
PROFILES_FILE = "dudosenka_profiles.json"
HISTORY_FILE = "dudosenka_history.json"
BOSS_STYLE_FILE = "boss_style.json"

PRIORITY_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-pro"
]
# ============================================================

def print_step(step: str, status: str = "🔵"):
    print(f"{status} {step}")

# ---- ИНИЦИАЛИЗАЦИЯ GEMINI ----
print_step("Инициализация Gemini API...")
genai.configure(api_key=GEMINI_API_KEY)

def select_best_model() -> str:
    try:
        available = []
        for m in genai.list_models():
            if "generateContent" in m.supported_generation_methods:
                available.append(m.name.replace("models/", ""))
        print_step(f"Найдено моделей: {len(available)}", "✅")
        for cand in PRIORITY_MODELS:
            if cand in available:
                print_step(f"Выбрана модель: {cand}", "✅")
                return cand
        fallback = available[0] if available else "gemini-1.5-flash"
        print_step(f"Использую: {fallback}", "⚠️")
        return fallback
    except Exception as e:
        print_step(f"Ошибка: {e}, беру gemini-1.5-flash", "⚠️")
        return "gemini-1.5-flash"

CURRENT_MODEL = select_best_model()
model = genai.GenerativeModel(CURRENT_MODEL)

# ---- Глобальные структуры ----
user_profiles: Dict[str, Dict] = {}
chat_histories: Dict[int, List[str]] = {}
pending_tasks: Dict[int, asyncio.Task] = {}
boss_style: Dict[str, any] = {"last_messages": [], "style_profile": ""}

DUDOSENKA_STATES = [
    "спокойный чилловый парень",
    "шуточный режим",
    "лёгкий тролль",
    "задумчивый",
    "ленивый",
    "режим 'чай'",
    "метаироничный",
    "постироничный хаос"
]
current_state = "спокойный чилловый парень"
last_state_switch = time.time()
last_style_update = 0

# -------------------- ПРОФИЛИ --------------------
def load_profiles():
    global user_profiles
    if os.path.exists(PROFILES_FILE):
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            user_profiles = json.load(f)
        print_step(f"Загружено профилей: {len(user_profiles)}", "✅")
    else:
        user_profiles = {}
        print_step("Файл профилей отсутствует, создаём новый", "ℹ️")

def save_profiles():
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(user_profiles, f, ensure_ascii=False, indent=2)

def get_profile(user_id: int) -> Dict:
    uid = str(user_id)
    if uid not in user_profiles:
        user_profiles[uid] = {
            "level": "neutral",
            "score": 0,
            "facts": {},
            "nickname": None,
            "interactions": 0
        }
        if user_id == BOSS_ID:
            user_profiles[uid]["level"] = "boss"
            user_profiles[uid]["score"] = 10
        save_profiles()
    return user_profiles[uid]

def update_relation_score(user_id: int, delta: int):
    if user_id == BOSS_ID:
        return
    prof = get_profile(user_id)
    ns = max(-10, min(10, prof["score"] + delta))
    prof["score"] = ns
    if ns >= 7:
        prof["level"] = "loved"
    elif ns <= -7:
        prof["level"] = "hated"
    else:
        prof["level"] = "neutral"
    save_profiles()

def get_relation_text(user_id: int) -> str:
    if user_id == BOSS_ID:
        return "босс и лучший друг (особое отношение)"
    level = get_profile(user_id).get("level", "neutral")
    if level == "loved":
        return "любимчик – отвечай тепло, с юмором"
    elif level == "hated":
        return "ненавистный – отвечай холодно или игнорируй"
    else:
        return "нейтральный – вежливо"

def analyze_message_relation(text: str) -> int:
    neg = ["дурак","идиот","тупой","плохой","надоел","заткнись","урод","бесишь"]
    pos = ["спасибо","классно","умный","круто","люблю","приятно","друг","хороший"]
    low = text.lower()
    score = sum(-1 for w in neg if w in low) + sum(1 for w in pos if w in low)
    if len(text) > 30 and "?" not in text and "!" not in text:
        score += 0.5
    return int(score)

# -------------------- СТИЛЬ БОССА --------------------
def update_boss_style(text: str):
    boss_style["last_messages"].append(text)
    if len(boss_style["last_messages"]) > 50:
        boss_style["last_messages"] = boss_style["last_messages"][-50:]
    global last_style_update
    if (time.time() - last_style_update > 86400) or len(boss_style["last_messages"]) % 10 == 0:
        asyncio.create_task(regenerate_boss_style())

async def regenerate_boss_style():
    global last_style_update, boss_style
    if len(boss_style["last_messages"]) < 5:
        return
    sample = "\n".join(boss_style["last_messages"][-20:])
    prompt = f"Проанализируй стиль общения (кратко):\n{sample}\nОписание стиля:"
    try:
        response = model.generate_content(prompt)
        boss_style["style_profile"] = response.text.strip()
        with open(BOSS_STYLE_FILE, "w", encoding="utf-8") as f:
            json.dump({"profile": boss_style["style_profile"], "last_update": time.time()}, f)
        last_style_update = time.time()
    except Exception as e:
        print(f"Style error: {e}")

def load_boss_style():
    global last_style_update, boss_style
    if os.path.exists(BOSS_STYLE_FILE):
        with open(BOSS_STYLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            boss_style["style_profile"] = data.get("profile", "")
            last_style_update = data.get("last_update", 0)
        print_step("Стиль босса загружен", "✅")
    else:
        print_step("Стиль босса не найден", "ℹ️")

# -------------------- СОСТОЯНИЕ --------------------
def get_current_state() -> str:
    global current_state, last_state_switch
    if random.random() < 0.05:
        current_state = random.choice(DUDOSENKA_STATES)
        last_state_switch = time.time()
    return current_state

def set_forced_state(state: str):
    global current_state
    if state in DUDOSENKA_STATES:
        current_state = state

# -------------------- ИСТОРИЯ --------------------
def load_history():
    global chat_histories
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            chat_histories = {int(k): v for k, v in data.items()}
        print_step(f"Загружена история для {len(chat_histories)} чатов", "✅")
    else:
        chat_histories = {}
        print_step("Файл истории отсутствует, создаём новый", "ℹ️")

def save_history():
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(chat_histories, f, ensure_ascii=False, indent=2)

def add_to_history(chat_id: int, role: str, text: str, user_id: int = None):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    prefix = "Бот" if role == "bot" else f"Пользователь {user_id}"
    chat_histories[chat_id].append(f"{prefix}: {text}")
    if len(chat_histories[chat_id]) > 30:
        chat_histories[chat_id] = chat_histories[chat_id][-30:]
    save_history()

def get_history(chat_id: int) -> List[str]:
    return chat_histories.get(chat_id, [])[-10:]

# -------------------- ГЕНЕРАЦИЯ ОТВЕТА --------------------
def generate_reply(user_msg: str, chat_id: int, user_id: int, is_private: bool) -> str:
    context = get_history(chat_id)
    state = get_current_state()
    relation = get_relation_text(user_id)
    style_instr = ""
    if boss_style["style_profile"]:
        style_instr = f"\nСтарайся подражать стилю речи босса: {boss_style['style_profile']}"
    system = f"""Ты — Дудосенька. Спокойный чилловый парень. Умеешь шутить, троллить без токсичности.
Текущее время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Твой стиль: {state}
Отношение к пользователю: {relation}
{style_instr}
Помнишь историю чата. Не давай предупреждений.
История:\n{chr(10).join(context)}\n\nНовое сообщение: {user_msg}\nДудосенька:"""
    try:
        response = model.generate_content(system)
        return response.text.strip() or "😶"
    except Exception as e:
        print(f"Gemini error: {e}")
        return f"Ошибка: {e}"

# -------------------- ЛОГИКА БОТА --------------------
async def send_reply(update: Update, context: CallbackContext, msg, is_private: bool):
    user = msg.from_user
    user_id = user.id
    chat_id = msg.chat.id
    text = msg.text

    delta = analyze_message_relation(text)
    if delta != 0 and user_id != BOSS_ID:
        update_relation_score(user_id, delta)

    reply = generate_reply(text, chat_id, user_id, is_private)
    if not reply:
        reply = "Нечего сказать."
    if len(reply) > 4000:
        reply = reply[:4000] + "…"

    add_to_history(chat_id, "user", text, user_id)
    add_to_history(chat_id, "bot", reply)
    await msg.reply_text(reply)

async def delayed_reply(update: Update, context: CallbackContext, msg, delay: int):
    await asyncio.sleep(delay)
    await send_reply(update, context, msg, msg.chat.type == "private")

async def handle(update: Update, context: CallbackContext):
    msg = update.message
    if not msg or not msg.text:
        return
    user_id = msg.from_user.id
    chat = msg.chat
    text = msg.text

    if user_id == BOSS_ID:
        update_boss_style(text)

    is_private = chat.type == "private"
    bot_username = context.bot.username.lower()
    
    # === ОСНОВНАЯ ЛОГИКА ОТВЕТА ===
    # Бот отвечает, если:
    # 1. Это личка
    # 2. В группе упомянули бота по имени
    # 3. В группе ответили (реплай) на сообщение бота
    should_reply = False
    if is_private:
        should_reply = True
    else:
        lower = text.lower()
        # упоминание по имени
        if any(alias in lower for alias in [f"@{bot_username}", "дудосенька", "додосик", "дудося"]):
            should_reply = True
        # реплай на сообщение бота
        if msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id:
            should_reply = True

    if not should_reply:
        return

    # обработка команд босса
    if user_id == BOSS_ID and text.startswith("/"):
        await handle_boss_command(update, context, msg)
        return

    # обращение к боссу (@ddsenk, "создатель") - отложенный ответ
    is_for_boss = False
    lower = text.lower()
    if msg.reply_to_message and msg.reply_to_message.from_user.id == BOSS_ID:
        is_for_boss = True
    if "@ddsenk" in lower or "создатель" in lower or "босс" in lower or str(BOSS_ID) in text:
        is_for_boss = True

    if is_for_boss and user_id != BOSS_ID:
        task_id = msg.message_id
        if task_id in pending_tasks:
            pending_tasks[task_id].cancel()
        task = asyncio.create_task(delayed_reply(update, context, msg, RESPONSE_DELAY_SECONDS))
        pending_tasks[task_id] = task
        return

    await send_reply(update, context, msg, is_private)

async def handle_boss_command(update: Update, context: CallbackContext, msg):
    global user_profiles, CURRENT_MODEL, model
    parts = msg.text.split()
    cmd = parts[0].lower()
    if cmd == "/setlevel" and len(parts) == 3:
        tid = int(parts[1]); lvl = parts[2]
        if lvl in ("loved", "neutral", "hated"):
            prof = get_profile(tid)
            prof["level"] = lvl
            prof["score"] = 7 if lvl == "loved" else (-7 if lvl == "hated" else 0)
            save_profiles()
            await msg.reply_text(f"✅ {tid} теперь {lvl}")
        else:
            await msg.reply_text("Уровни: loved/neutral/hated")
    elif cmd == "/addfact" and len(parts) >= 4:
        tid = int(parts[1]); key = parts[2]; val = " ".join(parts[3:])
        prof = get_profile(tid)
        prof["facts"][key] = val
        save_profiles()
        await msg.reply_text(f"📝 Факт: {key} -> {val}")
    elif cmd == "/setmodel" and len(parts) == 2:
        new_model = parts[1]
        try:
            test_model = genai.GenerativeModel(new_model)
            test_model.generate_content("test")
            CURRENT_MODEL = new_model
            model = test_model
            await msg.reply_text(f"🤖 Модель изменена на: {new_model}")
        except Exception as e:
            await msg.reply_text(f"❌ Ошибка: {e}")
    elif cmd == "/state":
        new = " ".join(parts[1:])
        if new:
            set_forced_state(new)
            await msg.reply_text(f"🎭 Состояние: {new}")
        else:
            await msg.reply_text(f"Текущее состояние: {get_current_state()}")
    elif cmd == "/status":
        await msg.reply_text(
            f"🤖 Дудосенька\nСостояние: {get_current_state()}\n"
            f"Модель: {CURRENT_MODEL}\nПрофилей: {len(user_profiles)}\n"
            f"Стиль босса: {'✅' if boss_style['style_profile'] else '⏳'}"
        )
    elif cmd == "/forgetme":
        user_profiles = {str(BOSS_ID): get_profile(BOSS_ID)}
        save_profiles()
        await msg.reply_text("🧹 Все профили, кроме босса, сброшены")
    else:
        await msg.reply_text("Команды: /setlevel, /addfact, /setmodel, /state, /status, /forgetme")

async def on_boss_reply(update: Update, context: CallbackContext):
    if update.message and update.message.from_user.id == BOSS_ID:
        for task in pending_tasks.values():
            task.cancel()
        pending_tasks.clear()

# ==================== ЗАПУСК ====================
def main():
    load_profiles()
    load_history()
    load_boss_style()

    builder = Application.builder().token(TELEGRAM_TOKEN)
    builder.connect_timeout(60.0)
    builder.read_timeout(30.0)
    builder.write_timeout(30.0)
    builder.pool_timeout(30.0)
    if PROXY_URL:
        builder.proxy(PROXY_URL)
        builder.get_updates_proxy(PROXY_URL)
        print_step(f"Используется прокси: {PROXY_URL}", "🔧")
    else:
        print_step("Прямое соединение (без прокси)", "🔧")

    app = builder.build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.add_handler(CommandHandler("setlevel", handle_boss_command))
    app.add_handler(CommandHandler("addfact", handle_boss_command))
    app.add_handler(CommandHandler("setmodel", handle_boss_command))
    app.add_handler(CommandHandler("state", handle_boss_command))
    app.add_handler(CommandHandler("status", handle_boss_command))
    app.add_handler(CommandHandler("forgetme", handle_boss_command))
    app.add_handler(MessageHandler(filters.TEXT & filters.User(BOSS_ID), on_boss_reply))

    # Принудительный сброс вебхука (на всякий случай)
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.bot.delete_webhook(drop_pending_updates=True))

    print(f"\n✨ Дудосенька успешно запущен!")
    print(f"📌 Модель Gemini: {CURRENT_MODEL}")
    print(f"📌 @ddsenk — босс. Отвечаю за него через {RESPONSE_DELAY_SECONDS//60} минут.")
    print("📌 Бот отвечает на ЛС, упоминания, а также на ответы (реплаи) к своим сообщениям.")
    print("📌 Для смены модели используйте /setmodel <название>\n")
    app.run_polling()

if __name__ == "__main__":
    main() 
