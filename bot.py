import asyncio
import logging
import os
import re
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, LabeledPrice, PreCheckoutQuery
)
from sqlalchemy import BigInteger, String, DateTime, Integer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from aiohttp import web

# ⚙️ КОНФИГ
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
PORT = int(os.getenv("PORT", 3000))

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com").strip()
if not WEBAPP_URL.startswith("http"):
    WEBAPP_URL = f"https://{WEBAPP_URL}"

PLANS = {
    "1m": {"name": "1 месяц",  "price_rub": 299,  "price_stars": 150,  "days": 30},
    "3m": {"name": "3 месяца", "price_rub": 799,  "price_stars": 400,  "days": 90},
    "1y": {"name": "1 год",    "price_rub": 2490, "price_stars": 1200, "days": 365},
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("genumm")

# 🗄️ БАЗА ДАННЫХ
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sub_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    vpn_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_id: Mapped[str] = mapped_column(String(8))
    method: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

engine = create_async_engine("sqlite+aiosqlite:///genumm.db")
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None):
    user = await session.get(User, tg_id)
    if not user:
        user = User(id=tg_id, username=username)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif username and user.username != username:
        user.username = username
        await session.commit()
    return user

async def get_user(session: AsyncSession, tg_id: int):
    return await session.get(User, tg_id)

async def extend_subscription(session: AsyncSession, tg_id: int, days: int, plan_id: str, method: str, amount: int, status: str = "paid"):
    user = await session.get(User, tg_id)
    if not user:
        user = User(id=tg_id)
        session.add(user)
    now = datetime.utcnow()
    base = user.sub_end if user.sub_end and user.sub_end > now else now
    user.sub_end = base + timedelta(days=days)
    if not user.vpn_key:
        user.vpn_key = f"vless://genumm-{tg_id}@vpn.genumm.com:443?type=tcp&security=reality#GenummVPN"
    session.add(Payment(user_id=tg_id, plan_id=plan_id, method=method, amount=amount, status=status))
    await session.commit()
    return user

# 🔐 ВЕРИФИКАЦИЯ INIT DATA
def verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed_data:
            return None
        hash_ = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calculated_hash, hash_):
            return json.loads(parsed_data.get("user", "{}"))
        return None
    except Exception:
        return None

def extract_tg_user(request: web.Request) -> dict | None:
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data or not BOT_TOKEN:
        return None
    user_data = verify_telegram_init_data(init_data, BOT_TOKEN)
    if not user_data or "id" not in user_data:
        return None
    return user_data

# ⌨️ КЛАВИАТУРЫ
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть GenummVPN", web_app=WebAppInfo(url=WEBAPP_URL))],
    ])

def pay_methods_kb(plan_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{plan_id}")],
        [InlineKeyboardButton(text="🏦 СБП (карта РФ)", callback_data=f"pay_sbp_{plan_id}")],
        [InlineKeyboardButton(text="💎 Криптовалюта", callback_data=f"pay_crypto_{plan_id}")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_app")]
    ])

# 🌐 ВЕБ-СЕРВЕР (API + FRONTEND)
async def handle_index(request: web.Request):
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="index.html не найден.", status=404)

async def handle_profile(request: web.Request):
    user_data = extract_tg_user(request)
    if not user_data:
        return web.json_response({"error": "Unauthorized"}, status=401)

    tg_id = int(user_data["id"])
    async with SessionLocal() as session:
        user = await get_or_create_user(session, tg_id, user_data.get("username"))
        return web.json_response({
            "id": user.id,
            "username": user.username,
            "sub_end": user.sub_end.isoformat() if user.sub_end else None,
            "vpn_key": user.vpn_key
        })

async def handle_checkout(request: web.Request):
    user_data = extract_tg_user(request)
    if not user_data:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    plan_id = body.get("plan")
    plan = PLANS.get(plan_id)
    if not plan:
        return web.json_response({"ok": False, "error": "Unknown plan"}, status=400)

    tg_id = int(user_data["id"])
    bot: Bot = request.app["bot"]

    async with SessionLocal() as session:
        await get_or_create_user(session, tg_id, user_data.get("username"))

    try:
        await bot.send_message(
            chat_id=tg_id,
            text=(
                f"💳 <b>Оформление подписки GenummVPN</b>\n\n"
                f"📦 Тариф: <b>{plan['name']}</b> ({plan['days']} дней)\n"
                f"💰 Стоимость: <b>{plan['price_rub']} ₽</b> или <b>{plan['price_stars']} ⭐</b>\n\n"
                f"Выберите удобный способ оплаты:"
            ),
            reply_markup=pay_methods_kb(plan_id),
            parse_mode="HTML"
        )
        return web.json_response({"ok": True})
    except Exception as e:
        log.error(f"Ошибка отправки счёта пользователю {tg_id}: {e}")
        return web.json_response({"ok": False, "error": "Bot send failed"}, status=500)

async def handle_support(request: web.Request):
    user_data = extract_tg_user(request)
    if not user_data:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    text = str(body.get("text", "")).strip()
    if not text:
        return web.json_response({"ok": False, "error": "Empty message"}, status=400)

    tg_id = int(user_data["id"])
    username = user_data.get("username") or "Скрыт"
    bot: Bot = request.app["bot"]

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📩 <b>Новое обращение в поддержку!</b>\n\n"
                f"👤 От: @{username} (ID: <code>{tg_id}</code>)\n\n"
                f"💬 <b>Сообщение:</b>\n{text}\n\n"
                f"<i>Нажмите «Ответить» на это сообщение, чтобы написать клиенту.</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            log.error(f"Не удалось отправить админу {admin_id}: {e}")

    try:
        await bot.send_message(
            tg_id,
            "✅ <b>Ваше обращение передано в поддержку.</b>\nОператор ответит вам в этом чате.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    return web.json_response({"ok": True})

def create_web_app(bot: Bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/profile", handle_profile)
    app.router.add_post("/api/checkout", handle_checkout)
    app.router.add_post("/api/support", handle_support)
    return app

# 🤖 РОУТЕР БОТА
router = Router()

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

@router.message(CommandStart())
async def cmd_start(message: Message):
    async with SessionLocal() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
    await message.answer(
        "<b>Добро пожаловать в GenummVPN</b> 🚀\n"
        "<i>Speed up your freedom</i>\n\n"
        "Откройте личный кабинет по кнопке ниже, чтобы управлять подпиской и получить ключ доступа.",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

# Резервный обработчик web_app_data (если откроют через ReplyKeyboard)
@router.message(F.web_app_data)
async def web_app_data_handler(message: Message, bot: Bot):
    try:
        data = json.loads(message.web_app_data.data)
        if data.get("action") == "checkout":
            plan_id = data.get("plan")
            plan = PLANS.get(plan_id)
            if plan:
                await message.answer(
                    f"💳 <b>Тариф:</b> {plan['name']} ({plan['price_rub']} ₽ / {plan['price_stars']} ⭐)\n\n"
                    f"Выберите способ оплаты:",
                    reply_markup=pay_methods_kb(plan_id),
                    parse_mode="HTML"
                )
    except Exception as e:
        log.error(f"Ошибка web_app_data: {e}")

# --- Оплата ---
@router.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(cb: CallbackQuery, bot: Bot):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=f"GenummVPN — {plan['name']}",
        description=f"Подписка на {plan['days']} дней (VLESS Reality · 10 Gbps)",
        payload=f"stars_{plan_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["price_stars"])],
    )
    await cb.answer()

@router.callback_query(F.data.startswith("pay_sbp_"))
async def pay_sbp(cb: CallbackQuery):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan:
        return
    stub_url = f"https://yoomoney.ru/checkout/stub?plan={plan_id}&amount={plan['price_rub']}"
    await cb.message.edit_text(
        f"🏦 <b>Оплата через СБП</b>\n"
        f"Тариф: <b>{plan['name']}</b> · Сумма: <b>{plan['price_rub']} ₽</b>\n\n"
        f"👉 <a href='{stub_url}'>Перейти к оплате</a>\n\n"
        f"<i>(Тестовая заглушка платёжного шлюза)</i>",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data=f"back_pay_{plan_id}")]
        ])
    )
    await cb.answer()

@router.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(cb: CallbackQuery):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan:
        return
    stub_url = f"https://t.me/CryptoBot?start=stub_{plan_id}"
    await cb.message.edit_text(
        f"💎 <b>Оплата криптовалютой</b>\n"
        f"Тариф: <b>{plan['name']}</b> · Сумма: <b>{plan['price_rub']} ₽</b>\n\n"
        f"👉 <a href='{stub_url}'>Оплатить через CryptoBot</a>\n\n"
        f"<i>(Тестовая заглушка платёжного шлюза)</i>",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data=f"back_pay_{plan_id}")]
        ])
    )
    await cb.answer()

@router.callback_query(F.data.startswith("back_pay_"))
async def back_pay(cb: CallbackQuery):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan:
        return
    await cb.message.edit_text(
        f"💳 <b>Оформление подписки GenummVPN</b>\n\n"
        f"📦 Тариф: <b>{plan['name']}</b> ({plan['days']} дней)\n"
        f"💰 Стоимость: <b>{plan['price_rub']} ₽</b> или <b>{plan['price_stars']} ⭐</b>\n\n"
        f"Выберите удобный способ оплаты:",
        reply_markup=pay_methods_kb(plan_id),
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data == "back_to_app")
async def back_to_app(cb: CallbackQuery):
    await cb.message.edit_text(
        "<b>Добро пожаловать в GenummVPN</b> 🚀\n"
        "<i>Speed up your freedom</i>\n\n"
        "Откройте личный кабинет по кнопке ниже, чтобы управлять подпиской и получить ключ доступа.",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )
    await cb.answer()

# --- Успешная оплата Stars ---
@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def on_stars_paid(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_"):
        return
    plan_id = payload.split("_")[1]
    plan = PLANS.get(plan_id)
    if not plan:
        return
    async with SessionLocal() as session:
        await extend_subscription(
            session,
            message.from_user.id,
            plan["days"],
            plan_id,
            "stars",
            plan["price_stars"],
            status="paid"
        )
    await message.answer(
        "✅ <b>Оплата успешно прошла!</b>\n"
        f"Подписка активирована на <b>{plan['days']} дней</b>.\n\n"
        "Откройте Mini App, чтобы скопировать ваш персональный ключ VLESS.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

# --- Ответ админа пользователю ---
@router.message(F.reply_to_message)
async def admin_reply(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot_me = await message.bot.get_me()
    if not message.reply_to_message.from_user or message.reply_to_message.from_user.id != bot_me.id:
        return

    source_text = message.reply_to_message.html_text or message.reply_to_message.text or ""
    match = re.search(r"ID:\s*(?:<code>)?(\d+)(?:</code>)?", source_text)
    if match:
        user_id = int(match.group(1))
        try:
            await message.bot.send_message(
                user_id,
                f"💬 <b>Ответ службы поддержки:</b>\n\n{message.text}",
                parse_mode="HTML"
            )
            await message.answer("✅ Ответ отправлен пользователю.")
        except Exception as e:
            log.error(f"Ошибка при ответе пользователю {user_id}: {e}")
            await message.answer("❌ Не удалось отправить ответ пользователю.")

@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "👑 <b>Админ-панель GenummVPN</b>\n\n"
        "Чтобы ответить клиенту, просто сделайте <b>Ответить (Reply)</b> на его обращение из поддержки.",
        parse_mode="HTML"
    )

# 🚀 ЗАПУСК
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения!")

    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    app = create_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info(f"🤖 GenummVPN запущен на порту {PORT}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
