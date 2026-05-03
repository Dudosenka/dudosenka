#!/usr/bin/env python3
import os
import asyncio
import json
import time
from datetime import datetime

# Отключаем прокси (на Render они не нужны)
for var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ[var] = ""

import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# ========== ЧТЕНИЕ ТОКЕНОВ ИЗ ОКРУЖЕНИЯ ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BOSS_ID = 1383365424

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("❌ Ошибка: не заданы переменные окружения TELEGRAM_TOKEN или GEMINI_API_KEY")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")
print("✅ Бот запускается...")

# ---------- ПРОФИЛИ, ПАМЯТЬ, СТИЛЬ БОССА ----------
profiles = {}
history = {}
boss_style = {"profile": "", "msgs": []}
last_style_update = 0

def load():
    global profiles, history, boss_style, last_style_update
    try:
        with open("profiles.json") as f: profiles = json.load(f)
    except: profiles = {}
    try:
        with open("history.json") as f: history = {int(k): v for k, v in json.load(f).items()}
    except: history = {}
    try:
        with open("boss_style.json") as f:
            d = json.load(f)
            boss_style["profile"] = d.get("profile", "")
            last_style_update = d.get("time", 0)
            boss_style["msgs"] = d.get("msgs", [])
    except: pass

def save():
    with open("profiles.json", "w") as f: json.dump(profiles, f, indent=2)
    with open("history.json", "w") as f: json.dump(history, f, indent=2)
    with open("boss_style.json", "w") as f:
        json.dump({"profile": boss_style["profile"], "time": last_style_update, "msgs": boss_style["msgs"][-50:]}, f)

def get_profile(uid):
    uid = str(uid)
    if uid not in profiles:
        profiles[uid] = {"level": "neutral", "score": 0}
        if uid == str(BOSS_ID): profiles[uid]["level"] = "boss"
        save()
    return profiles[uid]

def update_relation(uid, delta):
    if uid == BOSS_ID: return
    p = get_profile(uid)
    p["score"] = max(-10, min(10, p["score"] + delta))
    p["level"] = "loved" if p["score"] >= 7 else ("hated" if p["score"] <= -7 else "neutral")
    save()

def relation_text(uid):
    if uid == BOSS_ID: return "босс"
    return get_profile(uid).get("level", "neutral")

async def update_boss_style(text):
    global last_style_update
    boss_style["msgs"].append(text)
    if len(boss_style["msgs"]) > 50: boss_style["msgs"] = boss_style["msgs"][-50:]
    if time.time() - last_style_update > 86400 or len(boss_style["msgs"]) % 10 == 0:
        if len(boss_style["msgs"]) >= 5:
            sample = "\n".join(boss_style["msgs"][-20:])
            try:
                resp = model.generate_content(f"Опиши стиль речи человека кратко:\n{sample}")
                boss_style["profile"] = resp.text.strip()
                last_style_update = time.time()
                save()
            except: pass

pending = {}
async def handle(update, context):
    msg = update.message
    if not msg or not msg.text: return
    uid, cid, text = msg.from_user.id, msg.chat.id, msg.text
    if uid == BOSS_ID: await update_boss_style(text)
    bot_name = context.bot.username.lower()
    is_private = msg.chat.type == "private"
    need = is_private or any(x in text.lower() for x in [f"@{bot_name}", "дудосенька", "додосик"]) or (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id)
    if not need: return
    if uid == BOSS_ID and text.startswith("/"):
        await commands(update, context)
        return
    if ("@ddsenk" in text.lower() or "создатель" in text.lower()) and uid != BOSS_ID:
        if msg.message_id in pending: pending[msg.message_id].cancel()
        pending[msg.message_id] = asyncio.create_task(delayed_reply(update, context, msg))
        return
    bad = ["дурак","идиот","тупой","плохой","надоел","заткнись"]
    good = ["спасибо","классно","умный","круто","люблю","приятно"]
    score = sum(-1 for w in bad if w in text.lower()) + sum(1 for w in good if w in text.lower())
    if score: update_relation(uid, score)
    if cid not in history: history[cid] = []
    ctx = "\n".join(history[cid][-10:])
    style_instr = f"\nПодражай стилю босса: {boss_style['profile']}" if boss_style["profile"] else ""
    prompt = f"""Ты Дудосенька. Отношение: {relation_text(uid)}.{style_instr}
История:\n{ctx}\nСообщение: {text}\nОтвет:"""
    try:
        reply = model.generate_content(prompt).text.strip() or "😶"
    except Exception as e:
        reply = f"Ошибка: {e}"
    history[cid].append(f"User: {text}")
    history[cid].append(f"Bot: {reply}")
    save()
    await msg.reply_text(reply[:4000])

async def delayed_reply(update, context, msg):
    await asyncio.sleep(300)
    await handle(update, context)

async def commands(update, context):
    msg = update.message
    parts = msg.text.split()
    cmd = parts[0].lower()
    if cmd == "/setlevel" and len(parts) == 3:
        tid, lvl = int(parts[1]), parts[2]
        if lvl in ("loved","neutral","hated"):
            p = get_profile(tid)
            p["level"] = lvl
            p["score"] = 7 if lvl=="loved" else (-7 if lvl=="hated" else 0)
            save()
            await msg.reply_text(f"✅ {tid} → {lvl}")
    elif cmd == "/addfact" and len(parts) >= 4:
        tid, key, val = int(parts[1]), parts[2], " ".join(parts[3:])
        p = get_profile(tid)
        p["facts"] = p.get("facts", {})
        p["facts"][key] = val
        save()
        await msg.reply_text("📝 Факт сохранён")
    elif cmd == "/status":
        await msg.reply_text(f"🤖 Модель: gemini-1.5-flash\nПрофилей: {len(profiles)}\nСтиль босса: {'✅' if boss_style['profile'] else '⏳'}")
    else:
        await msg.reply_text("Команды: /setlevel, /addfact, /status")

async def main():
    load()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.add_handler(CommandHandler("setlevel", commands))
    app.add_handler(CommandHandler("addfact", commands))
    app.add_handler(CommandHandler("status", commands))
    print("🔄 Сброс вебхука...")
    await app.bot.delete_webhook(drop_pending_updates=True)
    print("✅ Бот запущен и готов к работе")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
