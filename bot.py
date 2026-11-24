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
    WebAppInfo, LabeledPrice, PreCheckoutQuery, MenuButtonWebApp,
    FSInputFile, InputMediaPhoto
)
from sqlalchemy import BigInteger, String, DateTime, Integer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from aiohttp import web

# ⚙️ КОНФИГ
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
PORT = int(os.getenv("PORT", 3000))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").lstrip("@")

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com").strip()
if not WEBAPP_URL.startswith("http"):
    WEBAPP_URL = f"https://{WEBAPP_URL}"

BANNER_PATH = "1.jpeg"

# ---------------------------------------------------------------------------
# 🎨 КАСТОМНЫЕ ЭМОДЗИ / ИКОНКИ (Telegram Custom Emoji ID)
# В Telegram Bot API нельзя загружать SVG напрямую в кнопки и текст —
# вместо SVG используются 19-значные ID из кастомных эмодзи-паков (t.me/addemoji/...).
# По умолчанию стоят рабочие стандартные ID монохромных UI-иконок (как на скринах Atlanta VPN).
# Любую из них можно переопределить через переменные окружения EMOJI_* без правки кода.
# ---------------------------------------------------------------------------
EMOJI = {
    "wave":     os.getenv("EMOJI_WAVE",     "5472055112702629499"),  # 👋
    "bolt":     os.getenv("EMOJI_BOLT",     "5431449001532594346"),  # ⚡
    "buy":      os.getenv("EMOJI_BUY",      "5258096772776991776"),  # 🛍 Купить VPN
    "gb":       os.getenv("EMOJI_GB",       "5258011929993026890"),  # 📶 Пополнить ГБ
    "subs":     os.getenv("EMOJI_SUBS",     "5271604874419647061"),  # 🔗 Мои подписки
    "invite":   os.getenv("EMOJI_INVITE",   "5258093637450866522"),  # 👥 Пригласить друзей
    "gift":     os.getenv("EMOJI_GIFT",     "5258503720928288433"),  # 🎁 Подарить VPN
    "profile":  os.getenv("EMOJI_PROFILE",  "5258011929993026890"),  # 👤 Мой профиль
    "guide":    os.getenv("EMOJI_GUIDE",    "5258391025281408576"),  # 📖 Инструкции
    "app":      os.getenv("EMOJI_APP",      "5445284980978621387"),  # 🚀 Открыть миниап
    "support":  os.getenv("EMOJI_SUPPORT",  "5258093637450866522"),  # 👥 Поддержка
    "pay":      os.getenv("EMOJI_PAY",      "5258503720928288433"),  # 💳 Способы оплаты
    "balance":  os.getenv("EMOJI_BALANCE",  "5258096772776991776"),  # 💼 Управление балансом
    "promo":    os.getenv("EMOJI_PROMO",    "5271604874419647061"),  # 🎟 Активировать промокод
    "lang":     os.getenv("EMOJI_LANG",     "5258391025281408576"),  # 🌐 Сменить язык
    "rules":    os.getenv("EMOJI_RULES",    "5334882760735598374"),  # 📜 Правила
    "tools":    os.getenv("EMOJI_TOOLS",    "5341715473882955310"),  # 🛠 Решение проблем
    "cross":    os.getenv("EMOJI_CROSS",    "5210952531676504517"),  # ❌ Если не помогло
}

def tg_icon(key: str, fallback: str) -> str:
    """Возвращает HTML-тег <tg-emoji> для текста сообщений."""
    eid = EMOJI.get(key)
    if eid and eid.isdigit():
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback

def styled_btn(
    text: str,
    style: str | None = None,
    icon_key: str | None = None,
    **kwargs
) -> InlineKeyboardButton:
    """
    Кнопка с цветом (style='success'|'primary'|'danger') и кастомной иконкой
    (icon_custom_emoji_id, Bot API 9.4+). Если установлен старый aiogram,
    корректно откатывается до обычной кнопки без падения.
    """
    params = {"text": text, **kwargs}
    if style:
        params["style"] = style
    eid = EMOJI.get(icon_key) if icon_key else None
    if eid and eid.isdigit():
        params["icon_custom_emoji_id"] = eid
    try:
        return InlineKeyboardButton(**params)
    except Exception:
        return InlineKeyboardButton(text=text, **kwargs)

PLANS = {
    "1m": {"name": "1 месяц",  "price_rub": 299,  "price_stars": 150,  "days": 30},
    "3m": {"name": "3 месяца", "price_rub": 799,  "price_stars": 400,  "days": 90},
    "1y": {"name": "1 год",    "price_rub": 2490, "price_stars": 1200, "days": 365},
}

GB_PACKS = {
    "gb50":  {"name": "+50 ГБ",  "price_rub": 149, "price_stars": 75,  "gb": 50},
    "gb150": {"name": "+150 ГБ", "price_rub": 349, "price_stars": 175, "gb": 150},
    "gb500": {"name": "+500 ГБ", "price_rub": 790, "price_stars": 395, "gb": 500},
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
    balance_rub: Mapped[int] = mapped_column(Integer, default=0)
    extra_gb: Mapped[int] = mapped_column(Integer, default=0)
    invited_count: Mapped[int] = mapped_column(Integer, default=0)
    sub_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    vpn_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_bot_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_msg_has_photo: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_id: Mapped[str] = mapped_column(String(16))
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

async def add_extra_gb(session: AsyncSession, tg_id: int, gb: int, pack_id: str, method: str, amount: int, status: str = "paid"):
    user = await session.get(User, tg_id)
    if not user:
        user = User(id=tg_id)
        session.add(user)
    user.extra_gb = (user.extra_gb or 0) + gb
    session.add(Payment(user_id=tg_id, plan_id=pack_id, method=method, amount=amount, status=status))
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

# ⌨️ ТЕКСТЫ И КЛАВИАТУРЫ С КАСТОМНЫМИ ИКОНКАМИ (<tg-emoji> + icon_custom_emoji_id)
def welcome_text() -> str:
    return (
        f"{tg_icon('wave', '👋')} <b>Добро пожаловать в главное меню!</b>\n\n"
        f"{tg_icon('bolt', '⚡')} <b>GenummVPN</b> — <i>Speed up your freedom</i>\n\n"
        f"Выберите действие:"
    )

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn("Купить VPN", style="success", icon_key="buy", callback_data="menu_buy")],
        [styled_btn("Пополнить ГБ", style="success", icon_key="gb", callback_data="menu_gb")],
        [styled_btn("Мои подписки", style="primary", icon_key="subs", callback_data="menu_subs")],
        [styled_btn("Пригласить друзей", style="primary", icon_key="invite", callback_data="menu_invite")],
        [styled_btn("Подарить VPN", style="primary", icon_key="gift", callback_data="menu_gift")],
        [
            styled_btn("Мой профиль", style="primary", icon_key="profile", callback_data="menu_profile"),
            styled_btn("Инструкции", style="primary", icon_key="guide", callback_data="menu_guide"),
        ],
        [styled_btn("Открыть миниап", style="primary", icon_key="app", web_app=WebAppInfo(url=WEBAPP_URL))],
        [styled_btn("Не работает VPN?", callback_data="menu_troubleshoot")],
    ])

def profile_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn("Способы оплаты", style="success", icon_key="pay", callback_data="profile_pay_methods")],
        [styled_btn("Управление балансом", style="primary", icon_key="balance", callback_data="profile_balance")],
        [styled_btn("Активировать промокод", style="primary", icon_key="promo", callback_data="profile_promo")],
        [styled_btn("Сменить язык / Change language", style="primary", icon_key="lang", callback_data="profile_lang")],
        [styled_btn("Правила", style="primary", icon_key="rules", callback_data="profile_rules")],
        [styled_btn("Назад", callback_data="back_to_menu")],
    ])

def troubleshoot_menu():
    support_btn = (
        styled_btn("Поддержка", style="success", icon_key="support", url=f"https://t.me/{SUPPORT_USERNAME}")
        if SUPPORT_USERNAME
        else styled_btn("Поддержка", style="success", icon_key="support", web_app=WebAppInfo(url=WEBAPP_URL))
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [support_btn],
        [styled_btn("Главное меню", callback_data="back_to_menu")],
    ])

def plans_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn("1 месяц · 299 ₽", style="success", icon_key="bolt", callback_data="select_plan_1m")],
        [styled_btn("3 месяца · 799 ₽ (−15%)", style="success", icon_key="bolt", callback_data="select_plan_3m")],
        [styled_btn("1 год · 2 490 ₽ (−30%)", style="primary", icon_key="bolt", callback_data="select_plan_1y")],
        [styled_btn("Назад", callback_data="back_to_menu")],
    ])

def gb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn("+50 ГБ · 149 ₽", style="success", icon_key="gb", callback_data="select_gb_gb50")],
        [styled_btn("+150 ГБ · 349 ₽", style="success", icon_key="gb", callback_data="select_gb_gb150")],
        [styled_btn("+500 ГБ · 790 ₽", style="primary", icon_key="gb", callback_data="select_gb_gb500")],
        [styled_btn("Назад", callback_data="back_to_menu")],
    ])

def checkout_text(item_id: str) -> str:
    if item_id in PLANS:
        item = PLANS[item_id]
        return (
            f"{tg_icon('pay', '💳')} <b>Оплата подписки</b>\n"
            f"Тариф: <b>{item['name']}</b> · <b>{item['price_rub']} ₽</b>\n\n"
            f"Выберите способ оплаты:"
        )
    item = GB_PACKS[item_id]
    return (
        f"{tg_icon('gb', '📶')} <b>Пополнение трафика</b>\n"
        f"Пакет: <b>{item['name']}</b> · <b>{item['price_rub']} ₽</b>\n\n"
        f"Выберите способ оплаты:"
    )

def pay_methods_kb(item_id: str):
    back_cb = "menu_buy" if item_id in PLANS else "menu_gb"
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn("Telegram Stars", style="primary", icon_key="bolt", callback_data=f"pay_stars_{item_id}")],
        [styled_btn("СБП (карта РФ)", style="success", icon_key="pay", callback_data=f"pay_sbp_{item_id}")],
        [styled_btn("Криптовалюта", style="primary", icon_key="balance", callback_data=f"pay_crypto_{item_id}")],
        [styled_btn("Назад", callback_data=back_cb)],
    ])

def back_kb(target: str = "back_to_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn("Назад", callback_data=target)],
    ])

# 🖼️ ЕДИНЫЙ РЕНДЕР С ФОТО 1.jpeg (БЕЗ УДАЛЕНИЯ /start)
async def render_screen(
    bot: Bot,
    session: AsyncSession,
    user: User,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    cb: CallbackQuery | None = None,
    delete_previous: bool = True
):
    has_banner = os.path.exists(BANNER_PATH)

    # 1) Нажатие на кнопку -> редактируем текущее окно плавно
    if cb and cb.message:
        try:
            if has_banner and cb.message.photo:
                await cb.message.edit_media(
                    media=InputMediaPhoto(media=FSInputFile(BANNER_PATH), caption=text, parse_mode="HTML"),
                    reply_markup=reply_markup
                )
                return
            elif not has_banner and not cb.message.photo:
                await cb.message.edit_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                return
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return

    # 2) Для /start не трогаем само сообщение /start и при желании оставляем историю чистой
    if delete_previous and user.last_bot_msg_id:
        try:
            await bot.delete_message(chat_id=user.id, message_id=user.last_bot_msg_id)
        except Exception:
            pass

    if has_banner:
        msg = await bot.send_photo(
            chat_id=user.id,
            photo=FSInputFile(BANNER_PATH),
            caption=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        user.last_msg_has_photo = 1
    else:
        msg = await bot.send_message(
            chat_id=user.id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        user.last_msg_has_photo = 0

    user.last_bot_msg_id = msg.message_id
    await session.commit()

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
            "vpn_key": user.vpn_key,
            "extra_gb": user.extra_gb,
            "balance_rub": user.balance_rub
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
            await render_screen(
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
    # Сообщение /start НЕ удаляется и предыдущее меню не стирается,
    # чтобы на десктопе и телефоне история /start работала стабильно.
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        await render_screen(
            bot, session, user,
            welcome_text(),
            main_menu(),
            delete_previous=False
        )

# --- Главное меню и разделы ---
@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(bot, session, user, welcome_text(), main_menu(), cb=cb)
    await cb.answer()

@router.callback_query(F.data == "menu_buy")
async def menu_buy(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('buy', '🛍')} <b>Купить VPN</b>\n\nВыберите подходящий тариф:",
            plans_menu(),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "menu_gb")
async def menu_gb(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('gb', '📶')} <b>Пополнить ГБ</b>\n"
            f"Дополнительный пакет: <b>{user.extra_gb} ГБ</b>\n\n"
            f"Выберите объём трафика:",
            gb_menu(),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("select_plan_"))
async def select_plan(cb: CallbackQuery, bot: Bot):
    plan_id = cb.data.split("_")[2]
    if plan_id not in PLANS:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(bot, session, user, checkout_text(plan_id), pay_methods_kb(plan_id), cb=cb)
    await cb.answer()

@router.callback_query(F.data.startswith("select_gb_"))
async def select_gb(cb: CallbackQuery, bot: Bot):
    pack_id = cb.data.split("_")[2]
    if pack_id not in GB_PACKS:
        await cb.answer("Пакет не найден", show_alert=True)
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(bot, session, user, checkout_text(pack_id), pay_methods_kb(pack_id), cb=cb)
    await cb.answer()

@router.callback_query(F.data == "menu_subs")
async def menu_subs(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        now = datetime.utcnow()
        is_active = bool(user.sub_end and user.sub_end > now)
        if is_active:
            sub_info = (
                f"✅ <b>Статус:</b> Активна\n"
                f"📅 <b>Действует до:</b> {user.sub_end.strftime('%d.%m.%Y %H:%M UTC')}\n"
                f"{tg_icon('gb', '📶')} <b>Доп. трафик:</b> {user.extra_gb} ГБ\n\n"
                f"🔑 <b>Ваш ключ доступа:</b>\n<code>{user.vpn_key}</code>"
            )
        else:
            sub_info = (
                f"{tg_icon('cross', '❌')} <b>Активных подписок нет</b>\n\n"
                "Оформите подписку в разделе «Купить VPN» или в миниапе."
            )
        await render_screen(
            bot, session, user,
            f"{tg_icon('subs', '🔗')} <b>Мои подписки</b>\n\n{sub_info}",
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn("Купить VPN", style="success", icon_key="buy", callback_data="menu_buy")],
                [styled_btn("Назад", callback_data="back_to_menu")],
            ]),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "menu_invite")
async def menu_invite(cb: CallbackQuery, bot: Bot):
    bot_me = await bot.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{cb.from_user.id}"
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('invite', '👥')} <b>Пригласить друзей</b>\n\n"
            f"Приглашено друзей: <b>{user.invited_count}</b>\n"
            f"Делитесь персональной ссылкой и получайте бонусные дни за каждого друга:\n\n"
            f"<code>{ref_link}</code>",
            back_kb("back_to_menu"),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "menu_gift")
async def menu_gift(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('gift', '🎁')} <b>Подарить VPN</b>\n\n"
            "Выберите тариф для подарочного сертификата — после оплаты вы получите ключ, который можно переслать другу:",
            plans_menu(),
            cb=cb
        )
    await cb.answer()

# --- Профиль (полностью по скрину 3) ---
@router.callback_query(F.data == "menu_profile")
async def menu_profile(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        now = datetime.utcnow()
        active_subs = 1 if (user.sub_end and user.sub_end > now) else 0
        display_name = cb.from_user.username or cb.from_user.first_name or str(user.id)
        text = (
            f"{tg_icon('profile', '👤')} <b>Мой профиль</b>\n\n"
            f"<b>Пользователь:</b> {display_name}\n"
            f"<b>Баланс:</b> {user.balance_rub}₽\n"
            f"<b>Приглашено друзей:</b> {user.invited_count}\n"
            f"<b>Активных подписок:</b> {active_subs}"
        )
        await render_screen(bot, session, user, text, profile_menu(), cb=cb)
    await cb.answer()

@router.callback_query(F.data == "profile_pay_methods")
async def profile_pay_methods(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('pay', '💳')} <b>Способы оплаты</b>\n\n"
            "Доступны оплата через <b>СБП (карты РФ)</b>, <b>Telegram Stars</b> и <b>криптовалюту (CryptoBot)</b>.",
            back_kb("menu_profile"),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_balance")
async def profile_balance(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('balance', '💼')} <b>Управление балансом</b>\n\n"
            f"Текущий баланс: <b>{user.balance_rub}₽</b>\n"
            f"Средства с баланса автоматически списываются при продлении подписки.",
            back_kb("menu_profile"),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_promo")
async def profile_promo(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('promo', '🎟')} <b>Активировать промокод</b>\n\n"
            "Отправьте промокод в чат командой <code>/promo КОД</code>.",
            back_kb("menu_profile"),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_lang")
async def profile_lang(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('lang', '🌐')} <b>Сменить язык / Change language</b>\n\n"
            "Текущий язык интерфейса: <b>Русский 🇷🇺</b>",
            back_kb("menu_profile"),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_rules")
async def profile_rules(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('rules', '📜')} <b>Правила сервиса GenummVPN</b>\n\n"
            "• Один ключ предназначен для личного использования.\n"
            "• Запрещены DDoS-атаки, спам и противоправная активность.\n"
            "• При нарушении правил доступ приостанавливается без возврата средств.",
            back_kb("menu_profile"),
            cb=cb
        )
    await cb.answer()

# --- Инструкции ---
@router.callback_query(F.data == "menu_guide")
async def menu_guide(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('guide', '📖')} <b>Инструкции по подключению</b>\n\n"
            "1️⃣ Оформите подписку и скопируйте ключ в разделе «Мои подписки» или в миниапе.\n"
            "2️⃣ Скачайте клиент: <b>Happ</b>, <b>Streisand</b>, <b>Hiddify</b> (iOS/macOS/Windows/Android) или <b>v2rayNG</b> (Android).\n"
            "3️⃣ Вставьте ключ из буфера обмена и включите соединение.",
            back_kb("back_to_menu"),
            cb=cb
        )
    await cb.answer()

# --- Не работает VPN? (полностью по скрину 2) ---
@router.callback_query(F.data == "menu_troubleshoot")
async def menu_troubleshoot(cb: CallbackQuery, bot: Bot):
    text = (
        f"{tg_icon('tools', '🛠')} <b>Решение проблем с VPN</b>\n\n"
        "<blockquote>"
        "1️⃣ <b>Обновите подписку</b> — в GenummVPN, Happ или Hiddify.\n"
        "2️⃣ <b>Сбросьте клиент</b> — Настройки → Сброс → Полный сброс, затем добавьте подписку заново.\n"
        "3️⃣ <b>Смените страну</b> — выберите другой сервер.\n"
        "4️⃣ <b>Попробуйте другое приложение</b> — Happ, Streisand или Hiddify.\n"
        "5️⃣ <b>Включите «Обходы»</b> — если VPN не работает через мобильный интернет.\n"
        "6️⃣ <b>Android</b> — удалите другие VPN-профили в настройках телефона.\n"
        "7️⃣ <b>Не добавляется подписка?</b> — Happ → Настройки → Подписки → User-Agent: xray.\n"
        "8️⃣ <b>Выбирайте сервер с наименьшим пингом</b> — обычно он обеспечивает наиболее стабильное подключение."
        "</blockquote>\n\n"
        f"{tg_icon('cross', '❌')} <b>Если не помогло — обратитесь в поддержку.</b>"
    )
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(bot, session, user, text, troubleshoot_menu(), cb=cb)
    await cb.answer()

# --- Оплата: Stars, СБП, Крипта в одном окне ---
@router.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(cb: CallbackQuery, bot: Bot):
    item_id = cb.data.split("_")[2]
    item = PLANS.get(item_id) or GB_PACKS.get(item_id)
    if not item:
        await cb.answer("Позиция не найдена", show_alert=True)
        return

    desc = f"Подписка на {item['days']} дней" if "days" in item else f"Пакет трафика {item['name']}"
    invoice_link = await bot.create_invoice_link(
        title=f"GenummVPN — {item['name']}",
        description=desc,
        payload=f"stars_{item_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=item["name"], amount=item["price_stars"])],
    )

    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        back_target = f"select_plan_{item_id}" if item_id in PLANS else f"select_gb_{item_id}"
        await render_screen(
            bot, session, user,
            f"{tg_icon('bolt', '⭐')} <b>Оплата Telegram Stars</b>\n"
            f"Позиция: <b>{item['name']}</b> · <b>{item['price_rub']} ₽</b> ≈ <b>{item['price_stars']} ⭐</b>",
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(f"Оплатить {item['price_stars']} ⭐", style="primary", icon_key="bolt", url=invoice_link)],
                [styled_btn("Назад", callback_data=back_target)],
            ]),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("pay_sbp_"))
async def pay_sbp(cb: CallbackQuery, bot: Bot):
    item_id = cb.data.split("_")[2]
    item = PLANS.get(item_id) or GB_PACKS.get(item_id)
    if not item:
        return
    stub_url = f"https://yoomoney.ru/checkout/stub?item={item_id}&amount={item['price_rub']}"
    back_target = f"select_plan_{item_id}" if item_id in PLANS else f"select_gb_{item_id}"
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('pay', '🏦')} <b>Оплата через СБП</b>\n"
            f"Позиция: <b>{item['name']}</b> · <b>{item['price_rub']} ₽</b>",
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(f"Оплатить {item['price_rub']} ₽", style="success", icon_key="pay", url=stub_url)],
                [styled_btn("Назад", callback_data=back_target)],
            ]),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(cb: CallbackQuery, bot: Bot):
    item_id = cb.data.split("_")[2]
    item = PLANS.get(item_id) or GB_PACKS.get(item_id)
    if not item:
        return
    stub_url = f"https://t.me/CryptoBot?start=stub_{item_id}"
    back_target = f"select_plan_{item_id}" if item_id in PLANS else f"select_gb_{item_id}"
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        await render_screen(
            bot, session, user,
            f"{tg_icon('balance', '💎')} <b>Оплата криптовалютой</b>\n"
            f"Позиция: <b>{item['name']}</b> · <b>{item['price_rub']} ₽</b>",
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn("Открыть CryptoBot", style="primary", icon_key="balance", url=stub_url)],
                [styled_btn("Назад", callback_data=back_target)],
            ]),
            cb=cb
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
    item_id = payload.split("_")[1]
    try:
        await message.delete()
    except Exception:
        pass

    async with SessionLocal() as session:
        if item_id in PLANS:
            plan = PLANS[item_id]
            user = await extend_subscription(
                session, message.from_user.id, plan["days"], item_id, "stars", plan["price_stars"]
            )
            done_text = f"✅ <b>Подписка активирована на {plan['days']} дней</b>\n\nОткройте миниап или «Мои подписки», чтобы скопировать ключ."
        elif item_id in GB_PACKS:
            pack = GB_PACKS[item_id]
            user = await add_extra_gb(
                session, message.from_user.id, pack["gb"], item_id, "stars", pack["price_stars"]
            )
            done_text = f"✅ <b>Начислено {pack['name']}</b>\n\nВсего дополнительного трафика: <b>{user.extra_gb} ГБ</b>."
        else:
            return

        await render_screen(bot, session, user, done_text, main_menu())

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
