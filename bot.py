# ============================================================
# GenummVPN Telegram Bot — единый файл для Bothost
# Оплата: Telegram Stars (рабочая), СБП и Крипта (заглушка)
# ============================================================
import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, LabeledPrice, PreCheckoutQuery
)
from sqlalchemy import BigInteger, String, DateTime, Integer, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ============================================================
# ⚙️ КОНФИГ (переменные окружения Bothost)
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Переменная окружения BOT_TOKEN не найдена. Добавь её в панели Bothost.")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com")  # 🔧 замени на URL своего Mini App

PLANS = {
    "1m": {"name": "1 месяц",  "price_rub": 299,  "price_stars": 150,  "days": 30},
    "3m": {"name": "3 месяца", "price_rub": 799,  "price_stars": 400,  "days": 90},
    "1y": {"name": "1 год",    "price_rub": 2490, "price_stars": 1200, "days": 365},
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("genumm")

# ============================================================
# 🗄️ БАЗА ДАННЫХ (SQLite + SQLAlchemy async)
# ============================================================
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
    return user

async def get_user(session: AsyncSession, tg_id: int):
    return await session.get(User, tg_id)

async def extend_subscription(session, tg_id, days, plan_id, method, amount, status="paid"):
    user = await session.get(User, tg_id)
    if not user:
        return None
    now = datetime.utcnow()
    base = user.sub_end if user.sub_end and user.sub_end > now else now
    user.sub_end = base + timedelta(days=days)
    if not user.vpn_key:
        user.vpn_key = f"vless://genumm-{tg_id}@vpn.genumm.com:443?type=tcp&security=reality#GenummVPN"
    session.add(Payment(user_id=tg_id, plan_id=plan_id, method=method,
                        amount=amount, status=status))
    await session.commit()
    return user

async def get_stats(session):
    users = (await session.execute(select(User))).scalars().all()
    pays = (await session.execute(select(Payment).where(Payment.status.in_(["paid", "stub"])))).scalars().all()
    rub = sum(p.amount for p in pays if p.method == "sbp")
    stars = sum(p.amount for p in pays if p.method == "stars")
    return len(users), rub, stars, len(pays)

# ============================================================
# ⌨️ КЛАВИАТУРЫ
# ============================================================
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть GenummVPN", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile")],
    ])

def plans_kb():
    buttons = [[InlineKeyboardButton(
        text=f"{p['name']} — {p['price_rub']}₽ / {p['price_stars']}⭐",
        callback_data=f"plan_{pid}")] for pid, p in PLANS.items()]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def pay_method_kb(plan_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{plan_id}")],
        [InlineKeyboardButton(text="🏦 СБП (ЮKassa)", callback_data=f"pay_sbp_{plan_id}")],
        [InlineKeyboardButton(text="💎 Крипта (CryptoBot)", callback_data=f"pay_crypto_{plan_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="buy")],
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи (10)", callback_data="admin_users")],
    ])

# ============================================================
# 🤖 РОУТЕР
# ============================================================
router = Router()

def is_admin(uid): return uid in ADMIN_IDS

# ---------- /start ----------
@router.message(CommandStart())
async def cmd_start(message: Message):
    async with SessionLocal() as session:
        await get_or_create_user(session, message.from_user.id, message.from_user.username)
    await message.answer(
        "<b>Добро пожаловать в GenummVPN</b> 🚀\n"
        "<i>Speed up your freedom</i>\n\n"
        "Нажми на кнопку ниже, чтобы открыть приложение или купить подписку.",
        reply_markup=main_menu(), parse_mode="HTML")

# ---------- Профиль ----------
@router.callback_query(F.data == "profile")
async def profile(cb: CallbackQuery):
    async with SessionLocal() as session:
        user = await get_user(session, cb.from_user.id)
    if not user:
        await cb.answer("Нажми /start")
        return
    sub = user.sub_end.strftime("%d.%m.%Y %H:%M") if user.sub_end else "Нет"
    key = user.vpn_key or "Не выдан"
    await cb.message.edit_text(
        f"<b>👤 Профиль</b>\n\nID: <code>{user.id}</code>\n"
        f"Подписка до: <b>{sub}</b>\n\n"
        f"<b>Ваш ключ:</b>\n<code>{key}</code>",
        reply_markup=main_menu(), parse_mode="HTML")
    await cb.answer()

# ---------- Покупка ----------
@router.callback_query(F.data == "buy")
async def buy(cb: CallbackQuery):
    await cb.message.edit_text("<b>Выберите тариф:</b>", reply_markup=plans_kb(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data == "back")
async def back(cb: CallbackQuery):
    await cmd_start(cb.message)
    await cb.answer()

@router.callback_query(F.data.startswith("plan_"))
async def choose_plan(cb: CallbackQuery):
    plan_id = cb.data.split("_")[1]
    plan = PLANS.get(plan_id)
    if not plan:
        await cb.answer("Тариф не найден"); return
    await cb.message.edit_text(
        f"<b>{plan['name']}</b>\n"
        f"Цена: <b>{plan['price_rub']}₽</b> или <b>{plan['price_stars']}⭐</b>\n"
        f"Срок: {plan['days']} дней\n\nВыберите способ оплаты:",
        reply_markup=pay_method_kb(plan_id), parse_mode="HTML")
    await cb.answer()

# ---------- Stars (рабочая) ----------
@router.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(cb: CallbackQuery, bot: Bot):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan: return
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=f"GenummVPN — {plan['name']}",
        description=f"Подписка на {plan['days']} дней",
        payload=f"stars_{plan_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan['name'], amount=plan['price_stars'])],
    )
    await cb.answer()

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)  # заглушка: всегда пропускаем

@router.message(F.successful_payment)
async def on_stars_paid(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_"): return
    plan_id = payload.split("_")[1]
    plan = PLANS.get(plan_id)
    if not plan: return
    async with SessionLocal() as session:
        await extend_subscription(session, message.from_user.id, plan["days"],
                                  plan_id, "stars", plan["price_stars"], status="stub")
    await message.answer(
        "✅ <b>Оплата прошла (тестовый режим)!</b>\n"
        f"Подписка активирована на {plan['days']} дней.",
        parse_mode="HTML", reply_markup=main_menu())

# ---------- СБП (заглушка) ----------
@router.callback_query(F.data.startswith("pay_sbp_"))
async def pay_sbp(cb: CallbackQuery):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan: return
    stub_url = f"https://yoomoney.ru/checkout/stub?plan={plan_id}&amount={plan['price_rub']}"
    await cb.message.edit_text(
        f"🏦 <b>Оплата через СБП</b>\n\n"
        f"Тариф: {plan['name']}\nСумма: <b>{plan['price_rub']}₽</b>\n\n"
        f"<i>(Заглушка. Позже здесь будет ссылка на ЮKassa)</i>\n"
        f"<a href='{stub_url}'>Перейти к оплате (заглушка)</a>\n\n"
        f"<b>Активировать в тестовом режиме:</b>\n"
        f"<code>/pay_stub {plan_id} sbp</code>",
        reply_markup=main_menu(), parse_mode="HTML", disable_web_page_preview=True)
    await cb.answer()

# ---------- Крипта (заглушка) ----------
@router.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(cb: CallbackQuery):
    plan_id = cb.data.split("_")[2]
    plan = PLANS.get(plan_id)
    if not plan: return
    stub_url = f"https://t.me/CryptoBot?start=stub_{plan_id}"
    await cb.message.edit_text(
        f"💎 <b>Оплата криптой (CryptoBot)</b>\n\n"
        f"Тариф: {plan['name']}\nСумма: <b>{plan['price_rub']}₽</b>\n\n"
        f"<i>(Заглушка. Позже здесь будет инвойс CryptoBot)</i>\n"
        f"<a href='{stub_url}'>Открыть CryptoBot (заглушка)</a>\n\n"
        f"<b>Активировать в тестовом режиме:</b>\n"
        f"<code>/pay_stub {plan_id} crypto</code>",
        reply_markup=main_menu(), parse_mode="HTML", disable_web_page_preview=True)
    await cb.answer()

# ---------- Универсальная заглушка ----------
@router.message(Command("pay_stub"))
async def stub_payment(message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /pay_stub <plan_id> <method>"); return
    plan_id, method = args[1], args[2]
    plan = PLANS.get(plan_id)
    if not plan:
        await message.answer("Неверный тариф"); return
    async with SessionLocal() as session:
        await extend_subscription(session, message.from_user.id, plan["days"],
                                  plan_id, method, plan["price_rub"], status="stub")
    await message.answer(
        f"✅ <b>Тестовая оплата принята!</b>\nМетод: {method}\n"
        f"Подписка на {plan['days']} дней активирована.",
        parse_mode="HTML", reply_markup=main_menu())

# ---------- Админка ----------
@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode="HTML")

@router.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Нет доступа"); return
    async with SessionLocal() as session:
        users, rub, stars, pays = await get_stats(session)
    await cb.message.edit_text(
        f"📊 <b>Статистика</b>\n\nПользователей: <b>{users}</b>\n"
        f"Платежей: <b>{pays}</b>\nСумма ₽ (СБП): <b>{rub}</b>\nСумма ⭐: <b>{stars}</b>",
        reply_markup=admin_menu(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data == "admin_users")
async def admin_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    async with SessionLocal() as session:
        users = (await session.execute(select(User).limit(10))).scalars().all()
    lines = [f"<code>{u.id}</code> @{u.username or '—'} до "
             f"{(u.sub_end.strftime('%d.%m') if u.sub_end else '—')}" for u in users]
    await cb.message.edit_text(
        "👥 <b>Последние 10:</b>\n\n" + ("\n".join(lines) or "Пусто"),
        reply_markup=admin_menu(), parse_mode="HTML")
    await cb.answer()

# ============================================================
# 🚀 ЗАПУСК
# ============================================================
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("🤖 GenummVPN бот запущен (тестовый режим оплаты)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
