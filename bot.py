import asyncio
import logging
import os
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
from sqlalchemy import BigInteger, String, DateTime, Integer, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from aiohttp import web

# ⚙️ КОНФИГ
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
PORT = int(os.getenv("PORT", 3000))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com")

PLANS = {
    "1m": {"name": "1 месяц",  "price_rub": 299,  "price_stars": 150,  "days": 30},
    "3m": {"name": "3 месяца", "price_rub": 799,  "price_stars": 400,  "days": 90},
    "1y": {"name": "1 год",    "price_rub": 2490, "price_stars": 1200, "days": 365},
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("genumm")

# 🗄️ БАЗА ДАННЫХ
class Base(DeclarativeBase): pass

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
    return user

async def get_user(session: AsyncSession, tg_id: int):
    return await session.get(User, tg_id)

async def extend_subscription(session, tg_id, days, plan_id, method, amount, status="paid"):
    user = await session.get(User, tg_id)
    if not user: return None
    now = datetime.utcnow()
    base = user.sub_end if user.sub_end and user.sub_end > now else now
    user.sub_end = base + timedelta(days=days)
    if not user.vpn_key:
        user.vpn_key = f"vless://genumm-{tg_id}@vpn.genumm.com:443?type=tcp&security=reality#GenummVPN"
    session.add(Payment(user_id=tg_id, plan_id=plan_id, method=method, amount=amount, status=status))
    await session.commit()
    return user

# 🔐 ВЕРИФИКАЦИЯ TELEGRAM INITDATA
def verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data: return None
        hash_ = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash == hash_:
            return json.loads(parsed_data.get("user", "{}"))
        return None
    except Exception as e:
        log.error(f"Ошибка initData: {e}")
        return None

# 🌐 ВЕБ-СЕРВЕР
async def handle_index(request):
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="index.html не найден.", status=404)

async def handle_profile(request):
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data: return web.json_response({"error": "No initData"}, status=401)
    user_data = verify_telegram_init_data(init_data, BOT_TOKEN)
    if not user_data or "id" not in user_data: return web.json_response({"error": "Invalid"}, status=403)
        
    tg_id = user_data["id"]
    async with SessionLocal() as session:
        user = await get_user(session, tg_id)
        if not user: user = await get_or_create_user(session, tg_id, user_data.get("username"))
        return web.json_response({
            "id": user.id, "username": user.username,
            "sub_end": user.sub_end.isoformat() if user.sub_end else None,
            "vpn_key": user.vpn_key
        })

def create_web_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/profile", handle_profile)
    return app

# ⌨️ КЛАВИАТУРЫ БОТА
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть GenummVPN", web_app=WebAppInfo(url=WEBAPP_URL))],
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
    ])

# 🤖 РОУТЕР
router = Router()
def is_admin(uid): return uid in ADMIN_IDS

# Реакция на /start
@router.message(CommandStart())
async def cmd_start(message: Message):
    async with SessionLocal() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
    await message.answer(
        "<b>Добро пожаловать в GenummVPN</b> 🚀\n"
        "<i>Speed up your freedom</i>\n\n"
        "Нажми на кнопку ниже, чтобы открыть личный кабинет и управлять подпиской.",
        reply_markup=main_menu(), parse_mode="HTML")

# Реакция на кнопку "Меню" (если настроена в BotFather)
@router.message(F.text == "Меню")
@router.message(F.text == "Start")
@router.message(F.text == "Главное меню")
async def menu_button(message: Message):
    await cmd_start(message)

# Обработка данных из Mini App
@router.message(F.web_app_data)
async def web_app_data(message: Message, bot: Bot):
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get("action")
        plan_id = data.get("plan")
        plan = PLANS.get(plan_id)
        if not plan: return
        
        if action == "buy_stars":
            await bot.send_invoice(
                chat_id=message.from_user.id,
                title=f"GenummVPN — {plan['name']}",
                description=f"Подписка на {plan['days']} дней",
                payload=f"stars_{plan_id}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=plan['name'], amount=plan['price_stars'])],
            )
        elif action == "buy_sbp":
            stub_url = f"https://yoomoney.ru/checkout/stub?plan={plan_id}&amount={plan['price_rub']}"
            await message.answer(
                f"🏦 <b>Оплата через СБП</b>\n\nСумма: <b>{plan['price_rub']}₽</b>\n"
                f"<i>(Заглушка)</i>\n<a href='{stub_url}'>Перейти к оплате</a>\n\n"
                f"<b>Активировать в тестовом режиме:</b>\n<code>/pay_stub {plan_id} sbp</code>",
                parse_mode="HTML", disable_web_page_preview=True
            )
    except Exception as e:
        log.error(f"Ошибка web_app_data: {e}")

# Telegram Stars
@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def on_stars_paid(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_"): return
    plan_id = payload.split("_")[1]
    plan = PLANS.get(plan_id)
    if not plan: return
    async with SessionLocal() as session:
        await extend_subscription(session, message.from_user.id, plan["days"], plan_id, "stars", plan["price_stars"], status="stub")
    await message.answer(
        "✅ <b>Оплата прошла!</b>\n"
        f"Подписка активирована на {plan['days']} дней. Откройте Mini App.",
        parse_mode="HTML", reply_markup=main_menu())

# Заглушка оплаты
@router.message(Command("pay_stub"))
async def stub_payment(message: Message):
    args = message.text.split()
    if len(args) < 3: return
    plan_id, method = args[1], args[2]
    plan = PLANS.get(plan_id)
    if not plan: return
    async with SessionLocal() as session:
        await extend_subscription(session, message.from_user.id, plan["days"], plan_id, method, plan["price_rub"], status="stub")
    await message.answer(
        f"✅ <b>Тестовая оплата!</b> Подписка на {plan['days']} дней активирована.",
        parse_mode="HTML", reply_markup=main_menu())

# Админка
@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode="HTML")

@router.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    async with SessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        pays = (await session.execute(select(Payment).where(Payment.status.in_(["paid", "stub"])))).scalars().all()
    await cb.message.edit_text(
        f"📊 <b>Статистика</b>\n\nПользователей: <b>{len(users)}</b>\nПлатежей: <b>{len(pays)}</b>",
        reply_markup=admin_menu(), parse_mode="HTML")
    await cb.answer()

# 🚀 ЗАПУСК
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"🌐 Веб-сервер запущен на порту {PORT}")
    
    print("🤖 GenummVPN запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
