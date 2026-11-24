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
    WebAppInfo, LabeledPrice, PreCheckoutQuery, MenuButtonWebApp
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

# Курс конвертации в Stars задаётся здесь, пользователю показывается только при выборе Stars
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
    last_bot_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None = None):
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

# ⌨️ КЛАВИАТУРЫ И ТЕКСТЫ
WELCOME_TEXT = (
    "<b>GenummVPN</b>\n"
    "<i>Speed up your freedom</i>\n\n"
    "Выберите действие:"
)

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить подписку", callback_data="menu_buy")],
        [InlineKeyboardButton(text="📊 Продлить / Тарифы", callback_data="menu_buy")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile")],
        [
            InlineKeyboardButton(text="🔑 Мой ключ", callback_data="menu_key"),
            InlineKeyboardButton(text="📖 Инструкции", callback_data="menu_guide"),
        ],
        [InlineKeyboardButton(text="🛠 Не работает?", callback_data="menu_troubleshoot")],
        [InlineKeyboardButton(text="🚀 Открыть миниап", web_app=WebAppInfo(url=WEBAPP_URL))],
    ])

def plans_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц · 299 ₽", callback_data="select_plan_1m")],
        [InlineKeyboardButton(text="3 месяца · 799 ₽ (−15%)", callback_data="select_plan_3m")],
        [InlineKeyboardButton(text="1 год · 2 490 ₽ (−30%)", callback_data="select_plan_1y")],
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")],
    ])

def checkout_text(plan_id: str) -> str:
    plan = PLANS[plan_id]
    return (
        f"💳 <b>Оплата подписки</b>\n"
        f"Тариф: <b>{plan['name']}</b> · <b>{plan['price_rub']} ₽</b>\n\n"
        f"Выберите способ оплаты:"
    )

def pay_methods_kb(plan_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{plan_id}")],
        [InlineKeyboardButton(text="🏦 СБП (карта РФ)", callback_data=f"pay_sbp_{plan_id}")],
        [InlineKeyboardButton(text="💎 Криптовалюта", callback_data=f"pay_crypto_{plan_id}")],
        [InlineKeyboardButton(text="← К тарифам", callback_data="menu_buy")],
    ])

def back_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")],
    ])

async def send_or_replace_bot_message(bot: Bot, session: AsyncSession, user: User, text: str, reply_markup: InlineKeyboardMarkup):
    # Удаляет предыдущее рабочее сообщение бота, чтобы в чате не копился мусор
    if user.last_bot_msg_id:
        try:
            await bot.delete_message(chat_id=user.id, message_id=user.last_bot_msg_id)
        except Exception:
            pass
    msg = await bot.send_message(
        chat_id=user.id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    user.last_bot_msg_id = msg.message_id
    await session.commit()
    return msg

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
    if plan_id not in PLANS:
        return web.json_response({"ok": False, "error": "Unknown plan"}, status=400)

    tg_id = int(user_data["id"])
    bot: Bot = request.app["bot"]

    try:
        async with SessionLocal() as session:
            user = await get_or_create_user(session, tg_id, user_data.get("username"))
            await send_or_replace_bot_message(
                bot, session, user,
                text=checkout_text(plan_id),
                reply_markup=pay_methods_kb(plan_id)
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
                f"📩 <b>Обращение в поддержку</b>\n"
                f"👤 @{username} (ID: <code>{tg_id}</code>)\n\n"
                f"{text}\n\n"
                f"<i>Ответьте (Reply) на это сообщение.</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            log.error(f"Не удалось отправить админу {admin_id}: {e}")

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
async def cmd_start(message: Message, bot: Bot):
    try:
        await message.delete()
    except Exception:
        pass
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        await send_or_replace_bot_message(
            bot, session, user,
            text=WELCOME_TEXT,
            reply_markup=main_menu()
        )

# --- Навигация главного меню ---
@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(cb: CallbackQuery):
    await cb.message.edit_text(
        WELCOME_TEXT,
        reply_markup=main_menu(),
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data == "menu_buy")
async def menu_buy(cb: CallbackQuery):
    await cb.message.edit_text(
        "💎 <b>Выберите тариф GenummVPN</b>",
        reply_markup=plans_menu(),
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data.startswith("select_plan_"))
async def select_plan(cb: CallbackQuery):
    plan_id = cb.data.split("_")[2]
    if plan_id not in PLANS:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    await cb.message.edit_text(
        checkout_text(plan_id),
        reply_markup=pay_methods_kb(plan_id),
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data == "menu_profile")
async def menu_profile(cb: CallbackQuery):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        now = datetime.utcnow()
        is_active = bool(user.sub_end and user.sub_end > now)
        sub_str = (
            user.sub_end.strftime("%d.%m.%Y %H:%M UTC")
            if is_active else "Нет активной подписки"
        )
        await cb.message.edit_text(
            f"👤 <b>Мой профиль</b>\n\n"
            f"ID: <code>{user.id}</code>\n"
            f"Подписка до: <b>{sub_str}</b>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML"
        )
    await cb.answer()

@router.callback_query(F.data == "menu_key")
async def menu_key(cb: CallbackQuery):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        now = datetime.utcnow()
        is_active = bool(user.sub_end and user.sub_end > now and user.vpn_key)
        if is_active:
            body = f"<code>{user.vpn_key}</code>"
        else:
            body = "Ключ появится после оформления подписки."
        await cb.message.edit_text(
            f"🔑 <b>Ваш ключ доступа</b>\n\n{body}",
            reply_markup=back_menu_kb(),
            parse_mode="HTML"
        )
    await cb.answer()

@router.callback_query(F.data == "menu_guide")
async def menu_guide(cb: CallbackQuery):
    await cb.message.edit_text(
        "📖 <b>Инструкция по подключению</b>\n\n"
        "1. Оформите подписку и скопируйте ключ в миниапе или разделе «Мой ключ».\n"
        "2. Установите клиент: <b>Streisand</b> / <b>Hiddify</b> (iOS, macOS, Windows, Android) или <b>v2rayNG</b> (Android).\n"
        "3. Вставьте ключ из буфера обмена и нажмите «Подключить».",
        reply_markup=back_menu_kb(),
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data == "menu_troubleshoot")
async def menu_troubleshoot(cb: CallbackQuery):
    await cb.message.edit_text(
        "🛠 <b>Не работает?</b>\n\n"
        "• Проверьте системное время и отключите другие VPN/прокси.\n"
        "• Обновите подписку в клиенте или переподключитесь к сети.\n"
        "• Если проблема сохраняется — откройте миниап и напишите оператору в поддержку.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть миниап", web_app=WebAppInfo(url=WEBAPP_URL))],
            [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")],
        ]),
        parse_mode="HTML"
    )
    await cb.answer()

# --- Оплата: всё в одном редактируемом сообщении без спама ---
@router.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(cb: CallbackQuery, bot: Bot):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan:
        await cb.answer("Тариф не найден", show_alert=True)
        return

    invoice_link = await bot.create_invoice_link(
        title=f"GenummVPN — {plan['name']}",
        description=f"Подписка на {plan['days']} дней",
        payload=f"stars_{plan_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["price_stars"])],
    )

    await cb.message.edit_text(
        f"⭐ <b>Оплата Telegram Stars</b>\n"
        f"Тариф: <b>{plan['name']}</b> · <b>{plan['price_rub']} ₽</b> ≈ <b>{plan['price_stars']} ⭐</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Оплатить {plan['price_stars']} ⭐", url=invoice_link)],
            [InlineKeyboardButton(text="← К способам оплаты", callback_data=f"select_plan_{plan_id}")]
        ])
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
        f"Тариф: <b>{plan['name']}</b> · <b>{plan['price_rub']} ₽</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Оплатить {plan['price_rub']} ₽", url=stub_url)],
            [InlineKeyboardButton(text="← К способам оплаты", callback_data=f"select_plan_{plan_id}")]
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
        f"Тариф: <b>{plan['name']}</b> · <b>{plan['price_rub']} ₽</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть CryptoBot", url=stub_url)],
            [InlineKeyboardButton(text="← К способам оплаты", callback_data=f"select_plan_{plan_id}")]
        ])
    )
    await cb.answer()

# --- Успешная оплата Stars ---
@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def on_stars_paid(message: Message, bot: Bot):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_"):
        return
    plan_id = payload.split("_")[1]
    plan = PLANS.get(plan_id)
    if not plan:
        return
    try:
        await message.delete()
    except Exception:
        pass
    async with SessionLocal() as session:
        user = await extend_subscription(
            session,
            message.from_user.id,
            plan["days"],
            plan_id,
            "stars",
            plan["price_stars"],
            status="paid"
        )
        await send_or_replace_bot_message(
            bot, session, user,
            text=(
                f"✅ <b>Подписка активирована на {plan['days']} дней</b>\n\n"
                f"Откройте миниап, чтобы скопировать ключ."
            ),
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
                f"💬 <b>Ответ поддержки:</b>\n\n{message.text}",
                parse_mode="HTML"
            )
            await message.answer("✅ Отправлено.")
        except Exception as e:
            log.error(f"Ошибка при ответе пользователю {user_id}: {e}")
            await message.answer("❌ Не удалось отправить.")

@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "👑 <b>Админ-панель</b>\nОтветьте (Reply) на сообщение клиента из поддержки.",
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

    # Кнопка запуска Mini App слева от строки ввода / меню бота
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Миниап", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except Exception as e:
        log.warning(f"Не удалось установить MenuButtonWebApp: {e}")

    app = create_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info(f"🤖 GenummVPN запущен на порту {PORT}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
