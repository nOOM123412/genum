bot_code = '''import asyncio
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
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "epitabh").lstrip("@") or "epitabh"

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com").strip()
if not WEBAPP_URL.startswith("http"):
    WEBAPP_URL = f"https://{WEBAPP_URL}"

BANNER_PATH = "1.jpeg"
LOGO_PATH = "logo_g.png"

EMOJI = {
    "wave":     os.getenv("EMOJI_WAVE",     "5472055112702629499"),
    "bolt":     os.getenv("EMOJI_BOLT",     "5431449001532594346"),
    "buy":      os.getenv("EMOJI_BUY",      "5258096772776991776"),
    "gb":       os.getenv("EMOJI_GB",       "5258011929993026890"),
    "subs":     os.getenv("EMOJI_SUBS",     "5271604874419647061"),
    "invite":   os.getenv("EMOJI_INVITE",   "5258093637450866522"),
    "gift":     os.getenv("EMOJI_GIFT",     "5258503720928288433"),
    "profile":  os.getenv("EMOJI_PROFILE",  "5258011929993026890"),
    "guide":    os.getenv("EMOJI_GUIDE",    "5258391025281408576"),
    "app":      os.getenv("EMOJI_APP",      "5445284980978621387"),
    "support":  os.getenv("EMOJI_SUPPORT",  "5258093637450866522"),
    "pay":      os.getenv("EMOJI_PAY",      "5258503720928288433"),
    "balance":  os.getenv("EMOJI_BALANCE",  "5258096772776991776"),
    "promo":    os.getenv("EMOJI_PROMO",    "5271604874419647061"),
    "lang":     os.getenv("EMOJI_LANG",     "5258391025281408576"),
    "rules":    os.getenv("EMOJI_RULES",    "5334882760735598374"),
    "tools":    os.getenv("EMOJI_TOOLS",    "5341715473882955310"),
    "cross":    os.getenv("EMOJI_CROSS",    "5210952531676504517"),
}

def tg_icon(key: str, fallback: str) -> str:
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

def operator_url() -> str:
    return f"https://t.me/{SUPPORT_USERNAME}"

PLANS = {
    "1m": {"name_ru": "1 месяц",  "name_en": "1 Month",  "price_rub": 299,  "price_stars": 150,  "days": 30},
    "3m": {"name_ru": "3 месяца", "name_en": "3 Months", "price_rub": 799,  "price_stars": 400,  "days": 90},
    "1y": {"name_ru": "1 год",    "name_en": "1 Year",   "price_rub": 2490, "price_stars": 1200, "days": 365},
}

GB_PACKS = {
    "gb50":  {"name_ru": "+50 ГБ",  "name_en": "+50 GB",  "price_rub": 149, "price_stars": 75,  "gb": 50},
    "gb150": {"name_ru": "+150 ГБ", "name_en": "+150 GB", "price_rub": 349, "price_stars": 175, "gb": 150},
    "gb500": {"name_ru": "+500 ГБ", "name_en": "+500 GB", "price_rub": 790, "price_stars": 395, "gb": 500},
}

# 🌐 ПЕРЕВОДЫ (ПО УМОЛЧАНИЮ РУССКИЙ)
TEXTS = {
    "ru": {
        "welcome": (
            f"{tg_icon('wave', '👋')} <b>Добро пожаловать в главное меню!</b>\n\n"
            f"{tg_icon('bolt', '⚡')} <b>GenummVPN</b> — <i>Speed up your freedom</i>\n\n"
            f"Выберите действие:"
        ),
        "btn_buy": "Купить VPN",
        "btn_gb": "Пополнить ГБ",
        "btn_subs": "Мои подписки",
        "btn_invite": "Пригласить друзей",
        "btn_gift": "Подарить VPN",
        "btn_profile": "Мой профиль",
        "btn_guide": "Инструкции",
        "btn_app": "Открыть миниап",
        "btn_troubleshoot": "Не работает VPN?",
        "btn_back": "Назад",
        "btn_main_menu": "Главное меню",
        "btn_operator": f"Написать оператору (@{SUPPORT_USERNAME})",
        "buy_title": f"{tg_icon('buy', '🛍')} <b>Купить VPN</b>\n\nВыберите подходящий тариф:",
        "plan_1m": "1 месяц · 299 ₽",
        "plan_3m": "3 месяца · 799 ₽ (−15%)",
        "plan_1y": "1 год · 2 490 ₽ (−30%)",
        "gb_title": lambda gb: (
            f"{tg_icon('gb', '📶')} <b>Пополнить ГБ</b>\n"
            f"Дополнительный пакет: <b>{gb} ГБ</b>\n\n"
            f"Выберите объём трафика:"
        ),
        "gb_50": "+50 ГБ · 149 ₽",
        "gb_150": "+150 ГБ · 349 ₽",
        "gb_500": "+500 ГБ · 790 ₽",
        "checkout_plan": lambda name, rub: (
            f"{tg_icon('pay', '💳')} <b>Оплата подписки</b>\n"
            f"Тариф: <b>{name}</b> · <b>{rub} ₽</b>\n\n"
            f"Выберите способ оплаты:"
        ),
        "checkout_gb": lambda name, rub: (
            f"{tg_icon('gb', '📶')} <b>Пополнение трафика</b>\n"
            f"Пакет: <b>{name}</b> · <b>{rub} ₽</b>\n\n"
            f"Выберите способ оплаты:"
        ),
        "pay_stars_btn": lambda stars: f"Telegram Stars · {stars} ⭐",
        "pay_sbp_btn": lambda rub: f"СБП через оператора (@{SUPPORT_USERNAME}) · {rub} ₽",
        "pay_crypto_btn": "Криптовалюта",
        "sbp_operator_text": lambda name, rub: (
            f"{tg_icon('pay', '💳')} <b>Оплата через СБП</b>\n\n"
            f"Позиция: <b>{name}</b> · <b>{rub} ₽</b>\n\n"
            f"Оплата через СБП сейчас проходит напрямую через оператора <b>@{SUPPORT_USERNAME}</b>. "
            f"Нажмите кнопку ниже — откроется чат с оператором."
        ),
        "sbp_open_btn": lambda rub: f"Перейти к оператору @{SUPPORT_USERNAME} ({rub} ₽)",
        "crypto_text": lambda name, rub: (
            f"{tg_icon('balance', '💎')} <b>Оплата криптовалютой</b>\n\n"
            f"Позиция: <b>{name}</b> · <b>{rub} ₽</b>\n"
            f"Для проведения криптоплатежа напишите оператору <b>@{SUPPORT_USERNAME}</b>."
        ),
        "crypto_btn": f"Оплатить через @{SUPPORT_USERNAME}",
        "subs_active": lambda dt, gb, key: (
            f"{tg_icon('subs', '🔗')} <b>Мои подписки</b>\n\n"
            f"✅ <b>Статус:</b> Активна\n"
            f"📅 <b>Действует до:</b> {dt}\n"
            f"{tg_icon('gb', '📶')} <b>Доп. трафик:</b> {gb} ГБ\n\n"
            f"🔑 <b>Ваш ключ доступа:</b>\n<code>{key}</code>"
        ),
        "subs_none": (
            f"{tg_icon('subs', '🔗')} <b>Мои подписки</b>\n\n"
            f"{tg_icon('cross', '❌')} <b>Активных подписок нет</b>\n\n"
            "Оформите подписку в разделе «Купить VPN» или в миниапе."
        ),
        "invite_text": lambda cnt, link: (
            f"{tg_icon('invite', '👥')} <b>Пригласить друзей</b>\n\n"
            f"Приглашено друзей: <b>{cnt}</b>\n"
            f"Делитесь персональной ссылкой:\n\n"
            f"<code>{link}</code>"
        ),
        "gift_text": (
            f"{tg_icon('gift', '🎁')} <b>Подарить VPN</b>\n\n"
            "Выберите тариф для подарочного сертификата:"
        ),
        "profile_text": lambda name, bal, inv, active: (
            f"{tg_icon('profile', '👤')} <b>Мой профиль</b>\n\n"
            f"<b>Пользователь:</b> {name}\n"
            f"<b>Баланс:</b> {bal}₽\n"
            f"<b>Приглашено друзей:</b> {inv}\n"
            f"<b>Активных подписок:</b> {active}"
        ),
        "btn_pay_methods": "Способы оплаты",
        "btn_balance": "Управление балансом",
        "btn_promo": "Активировать промокод",
        "btn_lang": "Сменить язык / Change language",
        "btn_rules": "Правила",
        "pay_methods_text": (
            f"{tg_icon('pay', '💳')} <b>Способы оплаты</b>\n\n"
            f"• <b>Telegram Stars</b> — мгновенно в боте\n"
            f"• <b>СБП / Криптовалюта</b> — через оператора <b>@{SUPPORT_USERNAME}</b>"
        ),
        "balance_text": lambda bal: (
            f"{tg_icon('balance', '💼')} <b>Управление балансом</b>\n\n"
            f"Текущий баланс: <b>{bal}₽</b>\n"
            f"Пополнение и списание баланса — через оператора <b>@{SUPPORT_USERNAME}</b>."
        ),
        "promo_text": (
            f"{tg_icon('promo', '🎟')} <b>Активировать промокод</b>\n\n"
            f"Отправьте промокод оператору <b>@{SUPPORT_USERNAME}</b>."
        ),
        "lang_text": (
            f"{tg_icon('lang', '🌐')} <b>Сменить язык / Change language</b>\n\n"
            f"Текущий язык: <b>Русский 🇷🇺</b>\n"
            f"Выберите язык интерфейса:"
        ),
        "btn_set_ru": "Русский 🇷🇺",
        "btn_set_en": "English 🇬🇧",
        "lang_changed_ru": "Язык переключён на Русский 🇷🇺",
        "lang_changed_en": "Interface language switched to English 🇬🇧",
        "rules_text": (
            f"{tg_icon('rules', '📜')} <b>Правила сервиса GenummVPN</b>\n\n"
            "• Один ключ предназначен для личного использования.\n"
            "• Запрещены DDoS-атаки, спам и противоправная активность.\n"
            "• При нарушении правил доступ приостанавливается без возврата средств."
        ),
        "guide_menu_text": (
            f"{tg_icon('guide', '📖')} <b>Инструкции по подключению</b>\n\n"
            "Выберите ваше устройство:"
        ),
        "dev_ios": "iPhone / iPad",
        "dev_android": "Android",
        "dev_windows": "Windows",
        "dev_macos": "macOS",
        "dev_tv": "Android TV",
        "guide_ios": (
            f"📱 <b>Инструкция для iPhone / iPad (iOS)</b>\n\n"
            "1. Оформите подписку и скопируйте ключ в разделе «Мои подписки» или в миниапе.\n"
            "2. Скачайте из App Store приложение <b>Streisand</b> или <b>Happ</b>.\n"
            "3. Откройте приложение — оно предложит вставить ключ из буфера обмена (или нажмите <b>+</b> → «Вставить из буфера»).\n"
            "4. Нажмите кнопку подключения и разрешите добавление VPN-профиля в iOS."
        ),
        "guide_android": (
            f"🤖 <b>Инструкция для Android</b>\n\n"
            "1. Скопируйте ваш ключ доступа из раздела «Мои подписки» или миниапа.\n"
            "2. Установите <b>v2rayNG</b> или <b>Hiddify</b>.\n"
            "3. В приложении нажмите <b>+</b> → «Импорт из буфера обмена».\n"
            "4. Выберите добавленный профиль и нажмите кнопку запуска подключения."
        ),
        "guide_windows": (
            f"💻 <b>Инструкция для Windows</b>\n\n"
            "1. Скопируйте ключ доступа в разделе «Мои подписки» или в миниапе.\n"
            "2. Скачайте и установите клиент <b>Hiddify</b> или <b>v2rayN</b> для Windows.\n"
            "3. Вставьте ключ из буфера обмена (или нажмите «Добавить подписку»).\n"
            "4. Включите системный прокси / TUN-режим и запустите подключение."
        ),
        "guide_macos": (
            f"🖥️ <b>Инструкция для macOS</b>\n\n"
            "1. Скопируйте ваш ключ доступа в разделе «Мои подписки» или в миниапе.\n"
            "2. Установите <b>Streisand</b>, <b>Happ</b> или <b>Hiddify</b> для macOS.\n"
            "3. Добавьте конфигурацию из буфера обмена и разрешите VPN в «Системных настройках».\n"
            "4. Включите подключение и проверьте доступ в сеть."
        ),
        "guide_tv": (
            f"📺 <b>Инструкция для Android TV</b>\n\n"
            "1. Установите <b>v2rayNG</b> или <b>Hiddify</b> на Android TV (через Google Play или APK).\n"
            "2. Импортируйте ключ доступа (QR-код или ссылка подписки).\n"
            "3. Выберите сервер и включите VPN-соединение пультом."
        ),
        "troubleshoot_text": (
            f"{tg_icon('tools', '🛠')} <b>Решение проблем с VPN</b>\n\n"
            "<blockquote>"
            "1. <b>Обновите подписку</b> — в миниапе, Happ или Hiddify.\n"
            "2. <b>Сбросьте клиент</b> — Настройки → Сброс → Полный сброс, затем добавьте подписку заново.\n"
            "3. <b>Смените страну</b> — выберите другой сервер.\n"
            "4. <b>Попробуйте другое приложение</b> — Happ, Streisand или Hiddify.\n"
            "5. <b>Включите обход блокировок</b> — если VPN не работает через мобильный интернет.\n"
            "6. <b>Android</b> — удалите конфликтующие VPN-профили в настройках телефона.\n"
            "7. <b>Не добавляется подписка?</b> — проверьте User-Agent в настройках клиента.\n"
            "8. <b>Выбирайте сервер с наименьшим пингом</b> для стабильной скорости."
            "</blockquote>\n\n"
            f"{tg_icon('cross', '❌')} <b>Если не помогло — напишите оператору @{SUPPORT_USERNAME}.</b>"
        ),
        "paid_sub": lambda days: f"✅ <b>Подписка активирована на {days} дней</b>\n\nОткройте миниап или «Мои подписки», чтобы скопировать ключ.",
        "paid_gb": lambda name, gb: f"✅ <b>Начислено {name}</b>\n\nВсего дополнительного трафика: <b>{gb} ГБ</b>.",
    },
    "en": {
        "welcome": (
            f"{tg_icon('wave', '👋')} <b>Welcome to the main menu!</b>\n\n"
            f"{tg_icon('bolt', '⚡')} <b>GenummVPN</b> — <i>Speed up your freedom</i>\n\n"
            f"Choose an action:"
        ),
        "btn_buy": "Buy VPN",
        "btn_gb": "Top up GB",
        "btn_subs": "My subscriptions",
        "btn_invite": "Invite friends",
        "btn_gift": "Gift VPN",
        "btn_profile": "My profile",
        "btn_guide": "Guides",
        "btn_app": "Open Mini App",
        "btn_troubleshoot": "VPN not working?",
        "btn_back": "Back",
        "btn_main_menu": "Main menu",
        "btn_operator": f"Message operator (@{SUPPORT_USERNAME})",
        "buy_title": f"{tg_icon('buy', '🛍')} <b>Buy VPN</b>\n\nSelect a plan:",
        "plan_1m": "1 Month · 299 ₽",
        "plan_3m": "3 Months · 799 ₽ (−15%)",
        "plan_1y": "1 Year · 2 490 ₽ (−30%)",
        "gb_title": lambda gb: (
            f"{tg_icon('gb', '📶')} <b>Top up GB</b>\n"
            f"Extra traffic: <b>{gb} GB</b>\n\n"
            f"Select a traffic pack:"
        ),
        "gb_50": "+50 GB · 149 ₽",
        "gb_150": "+150 GB · 349 ₽",
        "gb_500": "+500 GB · 790 ₽",
        "checkout_plan": lambda name, rub: (
            f"{tg_icon('pay', '💳')} <b>Subscription Checkout</b>\n"
            f"Plan: <b>{name}</b> · <b>{rub} ₽</b>\n\n"
            f"Choose a payment method:"
        ),
        "checkout_gb": lambda name, rub: (
            f"{tg_icon('gb', '📶')} <b>Traffic Top-up Checkout</b>\n"
            f"Pack: <b>{name}</b> · <b>{rub} ₽</b>\n\n"
            f"Choose a payment method:"
        ),
        "pay_stars_btn": lambda stars: f"Telegram Stars · {stars} ⭐",
        "pay_sbp_btn": lambda rub: f"SBP via operator (@{SUPPORT_USERNAME}) · {rub} ₽",
        "pay_crypto_btn": "Cryptocurrency",
        "sbp_operator_text": lambda name, rub: (
            f"{tg_icon('pay', '💳')} <b>SBP Payment</b>\n\n"
            f"Item: <b>{name}</b> · <b>{rub} ₽</b>\n\n"
            f"SBP payments are currently processed directly via operator <b>@{SUPPORT_USERNAME}</b>. "
            f"Tap the button below to open a chat with the operator."
        ),
        "sbp_open_btn": lambda rub: f"Open chat with @{SUPPORT_USERNAME} ({rub} ₽)",
        "crypto_text": lambda name, rub: (
            f"{tg_icon('balance', '💎')} <b>Crypto Payment</b>\n\n"
            f"Item: <b>{name}</b> · <b>{rub} ₽</b>\n"
            f"To pay with cryptocurrency, message operator <b>@{SUPPORT_USERNAME}</b>."
        ),
        "crypto_btn": f"Pay via @{SUPPORT_USERNAME}",
        "subs_active": lambda dt, gb, key: (
            f"{tg_icon('subs', '🔗')} <b>My subscriptions</b>\n\n"
            f"✅ <b>Status:</b> Active\n"
            f"📅 <b>Valid until:</b> {dt}\n"
            f"{tg_icon('gb', '📶')} <b>Extra traffic:</b> {gb} GB\n\n"
            f"🔑 <b>Your access key:</b>\n<code>{key}</code>"
        ),
        "subs_none": (
            f"{tg_icon('subs', '🔗')} <b>My subscriptions</b>\n\n"
            f"{tg_icon('cross', '❌')} <b>No active subscriptions</b>\n\n"
            "Purchase a plan in Buy VPN or in the Mini App."
        ),
        "invite_text": lambda cnt, link: (
            f"{tg_icon('invite', '👥')} <b>Invite friends</b>\n\n"
            f"Invited friends: <b>{cnt}</b>\n"
            f"Share your personal referral link:\n\n"
            f"<code>{link}</code>"
        ),
        "gift_text": (
            f"{tg_icon('gift', '🎁')} <b>Gift VPN</b>\n\n"
            "Select a plan for a gift pass:"
        ),
        "profile_text": lambda name, bal, inv, active: (
            f"{tg_icon('profile', '👤')} <b>My profile</b>\n\n"
            f"<b>User:</b> {name}\n"
            f"<b>Balance:</b> {bal}₽\n"
            f"<b>Invited friends:</b> {inv}\n"
            f"<b>Active subscriptions:</b> {active}"
        ),
        "btn_pay_methods": "Payment methods",
        "btn_balance": "Manage balance",
        "btn_promo": "Activate promo code",
        "btn_lang": "Сменить язык / Change language",
        "btn_rules": "Rules",
        "pay_methods_text": (
            f"{tg_icon('pay', '💳')} <b>Payment methods</b>\n\n"
            f"• <b>Telegram Stars</b> — instant in bot\n"
            f"• <b>SBP / Cryptocurrency</b> — via operator <b>@{SUPPORT_USERNAME}</b>"
        ),
        "balance_text": lambda bal: (
            f"{tg_icon('balance', '💼')} <b>Manage balance</b>\n\n"
            f"Current balance: <b>{bal}₽</b>\n"
            f"Balance top-ups and adjustments — via operator <b>@{SUPPORT_USERNAME}</b>."
        ),
        "promo_text": (
            f"{tg_icon('promo', '🎟')} <b>Activate promo code</b>\n\n"
            f"Send your promo code to operator <b>@{SUPPORT_USERNAME}</b>."
        ),
        "lang_text": (
            f"{tg_icon('lang', '🌐')} <b>Сменить язык / Change language</b>\n\n"
            f"Current language: <b>English 🇬🇧</b>\n"
            f"Select interface language:"
        ),
        "btn_set_ru": "Русский 🇷🇺",
        "btn_set_en": "English 🇬🇧",
        "lang_changed_ru": "Язык переключён на Русский 🇷🇺",
        "lang_changed_en": "Interface language switched to English 🇬🇧",
        "rules_text": (
            f"{tg_icon('rules', '📜')} <b>GenummVPN Terms & Rules</b>\n\n"
            "• Each key is intended for personal use.\n"
            "• DDoS attacks, spam, and illegal activity are strictly prohibited.\n"
            "• Rule violations result in access suspension without refund."
        ),
        "guide_menu_text": (
            f"{tg_icon('guide', '📖')} <b>Connection Guides</b>\n\n"
            "Select your device:"
        ),
        "dev_ios": "iPhone / iPad",
        "dev_android": "Android",
        "dev_windows": "Windows",
        "dev_macos": "macOS",
        "dev_tv": "Android TV",
        "guide_ios": (
            f"📱 <b>iPhone / iPad (iOS) Guide</b>\n\n"
            "1. Get a subscription and copy your access key from My subscriptions or the Mini App.\n"
            "2. Download <b>Streisand</b> or <b>Happ</b> from the App Store.\n"
            "3. Open the app and paste the key from your clipboard (or tap <b>+</b> → Import from clipboard).\n"
            "4. Tap Connect and allow adding the VPN profile in iOS."
        ),
        "guide_android": (
            f"🤖 <b>Android Guide</b>\n\n"
            "1. Copy your access key from My subscriptions or the Mini App.\n"
            "2. Install <b>v2rayNG</b> or <b>Hiddify</b>.\n"
            "3. Tap <b>+</b> in the app → Import from clipboard.\n"
            "4. Select the added profile and tap Connect."
        ),
        "guide_windows": (
            f"💻 <b>Windows Guide</b>\n\n"
            "1. Copy your access key from My subscriptions or the Mini App.\n"
            "2. Download and install <b>Hiddify</b> or <b>v2rayN</b> for Windows.\n"
            "3. Import the key from clipboard (or add subscription).\n"
            "4. Enable system proxy / TUN mode and start connecting."
        ),
        "guide_macos": (
            f"🖥️ <b>macOS Guide</b>\n\n"
            "1. Copy your access key from My subscriptions or the Mini App.\n"
            "2. Install <b>Streisand</b>, <b>Happ</b>, or <b>Hiddify</b> for macOS.\n"
            "3. Add the configuration from clipboard and approve the VPN profile in System Settings.\n"
            "4. Turn on the connection and verify network access."
        ),
        "guide_tv": (
            f"📺 <b>Android TV Guide</b>\n\n"
            "1. Install <b>v2rayNG</b> or <b>Hiddify</b> on your Android TV device.\n"
            "2. Import your access key or subscription link.\n"
            "3. Pick a server and enable the VPN connection with your remote."
        ),
        "troubleshoot_text": (
            f"{tg_icon('tools', '🛠')} <b>Troubleshooting VPN</b>\n\n"
            "<blockquote>"
            "1. <b>Update subscription</b> — in the Mini App, Happ, or Hiddify.\n"
            "2. <b>Reset client</b> — Settings → Reset → Full reset, then re-add your subscription.\n"
            "3. <b>Switch country</b> — pick another server.\n"
            "4. <b>Try another client</b> — Happ, Streisand, or Hiddify.\n"
            "5. <b>Enable bypass options</b> — if VPN fails over mobile data.\n"
            "6. <b>Android</b> — remove conflicting VPN profiles in phone settings.\n"
            "7. <b>Subscription won't add?</b> — check User-Agent in client subscription settings.\n"
            "8. <b>Choose lowest ping server</b> for more stable performance."
            "</blockquote>\n\n"
            f"{tg_icon('cross', '❌')} <b>Still not working? Message operator @{SUPPORT_USERNAME}.</b>"
        ),
        "paid_sub": lambda days: f"✅ <b>Subscription activated for {days} days</b>\n\nOpen the Mini App or My subscriptions to copy your key.",
        "paid_gb": lambda name, gb: f"✅ <b>Added {name}</b>\n\nTotal extra traffic: <b>{gb} GB</b>.",
    },
}

def t(lang: str, key: str, *args):
    pack = TEXTS.get(lang) or TEXTS["ru"]
    val = pack.get(key, TEXTS["ru"].get(key))
    return val(*args) if callable(val) else val

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("genumm")

# 🗄️ БАЗА ДАННЫХ
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="ru")
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
        # Безопасная миграция колонки lang при обновлении существующей БД
        res = await conn.exec_driver_sql("PRAGMA table_info(users)")
        cols = [row[1] for row in res.fetchall()]
        if "lang" not in cols:
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN lang VARCHAR(8) DEFAULT 'ru'")

async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None = None):
    user = await session.get(User, tg_id)
    if not user:
        user = User(id=tg_id, username=username, lang="ru")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if not user.lang:
            user.lang = "ru"
            changed = True
        if changed:
            await session.commit()
    return user

async def extend_subscription(session: AsyncSession, tg_id: int, days: int, plan_id: str, method: str, amount: int, status: str = "paid"):
    user = await session.get(User, tg_id)
    if not user:
        user = User(id=tg_id, lang="ru")
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
        user = User(id=tg_id, lang="ru")
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
        data_check_string = "\\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
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
def main_menu(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "btn_buy"), style="success", icon_key="buy", callback_data="menu_buy")],
        [styled_btn(t(lang, "btn_gb"), style="success", icon_key="gb", callback_data="menu_gb")],
        [styled_btn(t(lang, "btn_subs"), style="primary", icon_key="subs", callback_data="menu_subs")],
        [styled_btn(t(lang, "btn_invite"), style="primary", icon_key="invite", callback_data="menu_invite")],
        [styled_btn(t(lang, "btn_gift"), style="primary", icon_key="gift", callback_data="menu_gift")],
        [
            styled_btn(t(lang, "btn_profile"), style="primary", icon_key="profile", callback_data="menu_profile"),
            styled_btn(t(lang, "btn_guide"), style="primary", icon_key="guide", callback_data="menu_guide"),
        ],
        [styled_btn(t(lang, "btn_app"), style="primary", icon_key="app", web_app=WebAppInfo(url=WEBAPP_URL))],
        [styled_btn(t(lang, "btn_troubleshoot"), callback_data="menu_troubleshoot")],
    ])

def profile_menu(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "btn_pay_methods"), style="success", icon_key="pay", callback_data="profile_pay_methods")],
        [styled_btn(t(lang, "btn_balance"), style="primary", icon_key="balance", callback_data="profile_balance")],
        [styled_btn(t(lang, "btn_promo"), style="primary", icon_key="promo", callback_data="profile_promo")],
        [styled_btn(t(lang, "btn_lang"), style="primary", icon_key="lang", callback_data="profile_lang")],
        [styled_btn(t(lang, "btn_rules"), style="primary", icon_key="rules", callback_data="profile_rules")],
        [styled_btn(t(lang, "btn_back"), callback_data="back_to_menu")],
    ])

def guide_device_menu(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "dev_ios"), style="success", callback_data="guide_dev_ios")],
        [styled_btn(t(lang, "dev_android"), style="success", callback_data="guide_dev_android")],
        [styled_btn(t(lang, "dev_windows"), style="primary", callback_data="guide_dev_windows")],
        [styled_btn(t(lang, "dev_macos"), style="primary", callback_data="guide_dev_macos")],
        [styled_btn(t(lang, "dev_tv"), style="primary", callback_data="guide_dev_tv")],
        [styled_btn(t(lang, "btn_back"), callback_data="back_to_menu")],
    ])

def troubleshoot_menu(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "btn_operator"), style="success", icon_key="support", url=operator_url())],
        [styled_btn(t(lang, "btn_main_menu"), callback_data="back_to_menu")],
    ])

def plans_menu(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "plan_1m"), style="success", icon_key="bolt", callback_data="select_plan_1m")],
        [styled_btn(t(lang, "plan_3m"), style="success", icon_key="bolt", callback_data="select_plan_3m")],
        [styled_btn(t(lang, "plan_1y"), style="primary", icon_key="bolt", callback_data="select_plan_1y")],
        [styled_btn(t(lang, "btn_back"), callback_data="back_to_menu")],
    ])

def gb_menu(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "gb_50"), style="success", icon_key="gb", callback_data="select_gb_gb50")],
        [styled_btn(t(lang, "gb_150"), style="success", icon_key="gb", callback_data="select_gb_gb150")],
        [styled_btn(t(lang, "gb_500"), style="primary", icon_key="gb", callback_data="select_gb_gb500")],
        [styled_btn(t(lang, "btn_back"), callback_data="back_to_menu")],
    ])

def item_display_name(lang: str, item_id: str) -> str:
    if item_id in PLANS:
        return PLANS[item_id]["name_en"] if lang == "en" else PLANS[item_id]["name_ru"]
    return GB_PACKS[item_id]["name_en"] if lang == "en" else GB_PACKS[item_id]["name_ru"]

def checkout_text(lang: str, item_id: str) -> str:
    name = item_display_name(lang, item_id)
    if item_id in PLANS:
        return t(lang, "checkout_plan", name, PLANS[item_id]["price_rub"])
    return t(lang, "checkout_gb", name, GB_PACKS[item_id]["price_rub"])

def pay_methods_kb(lang: str, item_id: str):
    item = PLANS.get(item_id) or GB_PACKS[item_id]
    back_cb = "menu_buy" if item_id in PLANS else "menu_gb"
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "pay_stars_btn", item["price_stars"]), style="primary", icon_key="bolt", callback_data=f"pay_stars_{item_id}")],
        [styled_btn(t(lang, "pay_sbp_btn", item["price_rub"]), style="success", icon_key="pay", callback_data=f"pay_sbp_{item_id}")],
        [styled_btn(t(lang, "pay_crypto_btn"), style="primary", icon_key="balance", callback_data=f"pay_crypto_{item_id}")],
        [styled_btn(t(lang, "btn_back"), callback_data=back_cb)],
    ])

def back_kb(lang: str, target: str = "back_to_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_btn(t(lang, "btn_back"), callback_data=target)],
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

# 🌐 ВЕБ-СЕРВЕР (API + FRONTEND + ЛОГО СО СКРИНА 2)
async def handle_index(request: web.Request):
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="index.html не найден.", status=404)

async def handle_logo(request: web.Request):
    if os.path.exists(LOGO_PATH):
        return web.FileResponse(LOGO_PATH, headers={"Content-Type": "image/png"})
    if os.path.exists(BANNER_PATH):
        return web.FileResponse(BANNER_PATH, headers={"Content-Type": "image/jpeg"})
    return web.Response(status=404)

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
            "lang": user.lang or "ru",
            "support_url": operator_url(),
            "support_username": SUPPORT_USERNAME,
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
            lang = user.lang or "ru"
            await render_screen(
                bot, session, user,
                text=checkout_text(lang, plan_id),
                reply_markup=pay_methods_kb(lang, plan_id)
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
                f"📩 <b>Обращение в поддержку (@{SUPPORT_USERNAME})</b>\\n"
                f"👤 @{username} (ID: <code>{tg_id}</code>)\\n\\n"
                f"{text}\\n\\n"
                f"<i>Ответьте (Reply) на это сообщение.</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            log.error(f"Не удалось отправить админу {admin_id}: {e}")

    return web.json_response({"ok": True, "operator_url": operator_url()})

def create_web_app(bot: Bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", handle_index)
    app.router.add_get("/logo_g.png", handle_logo)
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
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        lang = user.lang or "ru"
        await render_screen(
            bot, session, user,
            t(lang, "welcome"),
            main_menu(lang),
            delete_previous=False
        )

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, t(lang, "welcome"), main_menu(lang), cb=cb)
    await cb.answer()

@router.callback_query(F.data == "menu_buy")
async def menu_buy(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, t(lang, "buy_title"), plans_menu(lang), cb=cb)
    await cb.answer()

@router.callback_query(F.data == "menu_gb")
async def menu_gb(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(
            bot, session, user,
            t(lang, "gb_title", user.extra_gb),
            gb_menu(lang),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("select_plan_"))
async def select_plan(cb: CallbackQuery, bot: Bot):
    plan_id = cb.data.split("_")[2]
    if plan_id not in PLANS:
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, checkout_text(lang, plan_id), pay_methods_kb(lang, plan_id), cb=cb)
    await cb.answer()

@router.callback_query(F.data.startswith("select_gb_"))
async def select_gb(cb: CallbackQuery, bot: Bot):
    pack_id = cb.data.split("_")[2]
    if pack_id not in GB_PACKS:
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, checkout_text(lang, pack_id), pay_methods_kb(lang, pack_id), cb=cb)
    await cb.answer()

@router.callback_query(F.data == "menu_subs")
async def menu_subs(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        now = datetime.utcnow()
        if user.sub_end and user.sub_end > now:
            body = t(lang, "subs_active", user.sub_end.strftime("%d.%m.%Y %H:%M UTC"), user.extra_gb, user.vpn_key)
        else:
            body = t(lang, "subs_none")
        await render_screen(
            bot, session, user, body,
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(t(lang, "btn_buy"), style="success", icon_key="buy", callback_data="menu_buy")],
                [styled_btn(t(lang, "btn_back"), callback_data="back_to_menu")],
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
        lang = user.lang or "ru"
        await render_screen(
            bot, session, user,
            t(lang, "invite_text", user.invited_count, ref_link),
            back_kb(lang, "back_to_menu"),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "menu_gift")
async def menu_gift(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, t(lang, "gift_text"), plans_menu(lang), cb=cb)
    await cb.answer()

# --- Профиль и язык ---
@router.callback_query(F.data == "menu_profile")
async def menu_profile(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        now = datetime.utcnow()
        active_subs = 1 if (user.sub_end and user.sub_end > now) else 0
        display_name = cb.from_user.username or cb.from_user.first_name or str(user.id)
        await render_screen(
            bot, session, user,
            t(lang, "profile_text", display_name, user.balance_rub, user.invited_count, active_subs),
            profile_menu(lang),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_pay_methods")
async def profile_pay_methods(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(
            bot, session, user,
            t(lang, "pay_methods_text"),
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(t(lang, "btn_operator"), style="success", icon_key="support", url=operator_url())],
                [styled_btn(t(lang, "btn_back"), callback_data="menu_profile")],
            ]),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_balance")
async def profile_balance(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(
            bot, session, user,
            t(lang, "balance_text", user.balance_rub),
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(t(lang, "btn_operator"), style="success", icon_key="support", url=operator_url())],
                [styled_btn(t(lang, "btn_back"), callback_data="menu_profile")],
            ]),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_promo")
async def profile_promo(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(
            bot, session, user,
            t(lang, "promo_text"),
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(t(lang, "btn_operator"), style="success", icon_key="support", url=operator_url())],
                [styled_btn(t(lang, "btn_back"), callback_data="menu_profile")],
            ]),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data == "profile_lang")
async def profile_lang(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(
            bot, session, user,
            t(lang, "lang_text"),
            InlineKeyboardMarkup(inline_keyboard=[
                [
                    styled_btn(t(lang, "btn_set_ru"), style="success" if lang == "ru" else "primary", callback_data="set_lang_ru"),
                    styled_btn(t(lang, "btn_set_en"), style="success" if lang == "en" else "primary", callback_data="set_lang_en"),
                ],
                [styled_btn(t(lang, "btn_back"), callback_data="menu_profile")],
            ]),
            cb=cb
        )
    await cb.answer()

@router.callback_query(F.data.in_({"set_lang_ru", "set_lang_en"}))
async def set_lang(cb: CallbackQuery, bot: Bot):
    new_lang = "en" if cb.data.endswith("_en") else "ru"
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        user.lang = new_lang
        await session.commit()
        now = datetime.utcnow()
        active_subs = 1 if (user.sub_end and user.sub_end > now) else 0
        display_name = cb.from_user.username or cb.from_user.first_name or str(user.id)
        await render_screen(
            bot, session, user,
            t(new_lang, "profile_text", display_name, user.balance_rub, user.invited_count, active_subs),
            profile_menu(new_lang),
            cb=cb
        )
    await cb.answer(t(new_lang, "lang_changed_en" if new_lang == "en" else "lang_changed_ru"))

@router.callback_query(F.data == "profile_rules")
async def profile_rules(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, t(lang, "rules_text"), back_kb(lang, "menu_profile"), cb=cb)
    await cb.answer()

# --- Меню инструкций по устройствам (скрин 3) ---
@router.callback_query(F.data == "menu_guide")
async def menu_guide(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, t(lang, "guide_menu_text"), guide_device_menu(lang), cb=cb)
    await cb.answer()

@router.callback_query(F.data.startswith("guide_dev_"))
async def guide_device_detail(cb: CallbackQuery, bot: Bot):
    dev = cb.data.replace("guide_dev_", "")
    mapping = {
        "ios": "guide_ios",
        "android": "guide_android",
        "windows": "guide_windows",
        "macos": "guide_macos",
        "tv": "guide_tv",
    }
    key = mapping.get(dev)
    if not key:
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, t(lang, key), back_kb(lang, "menu_guide"), cb=cb)
    await cb.answer()

# --- Не работает VPN? (перевод на @epitabh) ---
@router.callback_query(F.data == "menu_troubleshoot")
async def menu_troubleshoot(cb: CallbackQuery, bot: Bot):
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        await render_screen(bot, session, user, t(lang, "troubleshoot_text"), troubleshoot_menu(lang), cb=cb)
    await cb.answer()

# --- Оплата: Stars, СБП через @epitabh, Крипта ---
@router.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(cb: CallbackQuery, bot: Bot):
    item_id = cb.data.split("_")[2]
    item = PLANS.get(item_id) or GB_PACKS.get(item_id)
    if not item:
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        name = item_display_name(lang, item_id)
        desc = f"{item['days']} days" if "days" in item else name
        invoice_link = await bot.create_invoice_link(
            title=f"GenummVPN — {name}",
            description=desc,
            payload=f"stars_{item_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=name, amount=item["price_stars"])],
        )
        back_target = f"select_plan_{item_id}" if item_id in PLANS else f"select_gb_{item_id}"
        await render_screen(
            bot, session, user,
            f"{tg_icon('bolt', '⭐')} <b>Telegram Stars</b>\n<b>{name}</b> · <b>{item['price_stars']} ⭐</b>",
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(t(lang, "pay_stars_btn", item["price_stars"]), style="primary", icon_key="bolt", url=invoice_link)],
                [styled_btn(t(lang, "btn_back"), callback_data=back_target)],
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
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        name = item_display_name(lang, item_id)
        back_target = f"select_plan_{item_id}" if item_id in PLANS else f"select_gb_{item_id}"
        await render_screen(
            bot, session, user,
            t(lang, "sbp_operator_text", name, item["price_rub"]),
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(t(lang, "sbp_open_btn", item["price_rub"]), style="success", icon_key="support", url=operator_url())],
                [styled_btn(t(lang, "btn_back"), callback_data=back_target)],
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
    async with SessionLocal() as session:
        user = await get_or_create_user(session, cb.from_user.id, cb.from_user.username)
        lang = user.lang or "ru"
        name = item_display_name(lang, item_id)
        back_target = f"select_plan_{item_id}" if item_id in PLANS else f"select_gb_{item_id}"
        await render_screen(
            bot, session, user,
            t(lang, "crypto_text", name, item["price_rub"]),
            InlineKeyboardMarkup(inline_keyboard=[
                [styled_btn(t(lang, "crypto_btn"), style="primary", icon_key="support", url=operator_url())],
                [styled_btn(t(lang, "btn_back"), callback_data=back_target)],
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
            lang = user.lang or "ru"
            done_text = t(lang, "paid_sub", plan["days"])
        elif item_id in GB_PACKS:
            pack = GB_PACKS[item_id]
            user = await add_extra_gb(
                session, message.from_user.id, pack["gb"], item_id, "stars", pack["price_stars"]
            )
            lang = user.lang or "ru"
            name = item_display_name(lang, item_id)
            done_text = t(lang, "paid_gb", name, user.extra_gb)
        else:
            return

        await render_screen(bot, session, user, done_text, main_menu(lang))

# --- Ответ админа пользователю ---
@router.message(F.reply_to_message)
async def admin_reply(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot_me = await message.bot.get_me()
    if not message.reply_to_message.from_user or message.reply_to_message.from_user.id != bot_me.id:
        return

    source_text = message.reply_to_message.html_text or message.reply_to_message.text or ""
    match = re.search(r"ID:\\s*(?:<code>)?(\\d+)(?:</code>)?", source_text)
    if match:
        user_id = int(match.group(1))
        try:
            await message.bot.send_message(
                user_id,
                f"💬 <b>Ответ поддержки (@{SUPPORT_USERNAME}):</b>\\n\\n{message.text}",
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
        "👑 <b>Админ-панель</b>\\nОтветьте (Reply) на сообщение клиента из поддержки.",
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
'''

with open("bot.py", "w", encoding="utf-8") as f:
    f.write(bot_code.strip() + "\n")

# Update index.html: remove VLESS 10 Gbps badge, use logo_g.png from screen 2,
# make all support & SBP actions redirect straight to https://t.me/epitabh (no popup),
# and support RU (default) + EN translation across the mini app.
html_code = '''<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Genumm VPN</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    :root {
      --bg: #07090d;
      --card: #12151c;
      --card-2: #181c26;
      --border: rgba(255, 255, 255, 0.08);
      --text: #f3f5f8;
      --muted: #98a2b3;
      --accent: #00e5ff;
      --accent-soft: rgba(0, 229, 255, 0.14);
      --warm: #ff7a2f;
      --warm-soft: rgba(255, 122, 47, 0.16);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
      min-height: 100vh;
      padding: 16px 16px 92px;
    }
    .container { max-width: 520px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
    .header {
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 12px 14px; background: var(--card); border: 1px solid var(--border); border-radius: 18px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-logo {
      width: 44px; height: 44px; border-radius: 12px; object-fit: cover;
      border: 1px solid rgba(0, 229, 255, 0.35); background: #05070a;
    }
    .brand-title { font-size: 16px; font-weight: 700; letter-spacing: 0.08em; }
    .brand-sub { font-size: 11px; color: var(--muted); letter-spacing: 0.06em; text-transform: uppercase; }
    .lang-switch {
      background: var(--card-2); color: var(--text); border: 1px solid var(--border);
      border-radius: 10px; padding: 6px 10px; font-size: 12px; font-weight: 600; cursor: pointer;
    }
    .card {
      background: var(--card); border: 1px solid var(--border); border-radius: 18px;
      padding: 16px; display: flex; flex-direction: column; gap: 10px;
    }
    .card-label { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--warm); }
    .status-sub { font-size: 13px; color: var(--muted); }
    .status-main { font-size: 18px; font-weight: 700; }
    .key-box {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      background: var(--card-2); border: 1px solid var(--border); border-radius: 12px; padding: 12px;
      font-size: 13px; color: var(--muted); word-break: break-all;
    }
    .copy-btn {
      background: var(--accent-soft); color: var(--accent); border: 1px solid rgba(0, 229, 255, 0.35);
      border-radius: 10px; padding: 8px 12px; font-size: 12px; font-weight: 600; cursor: pointer; flex-shrink: 0;
    }
    .btn-primary {
      width: 100%; border: none; border-radius: 14px; padding: 13px 16px;
      font-size: 14px; font-weight: 700; cursor: pointer; text-decoration: none;
      display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    }
    .btn-operator { background: var(--card-2); color: var(--text); border: 1px solid var(--border); }
    .btn-buy { background: var(--warm); color: #fff; margin-top: 8px; }
    .plan-item {
      display: flex; align-items: center; justify-content: space-between;
      background: var(--card-2); border: 1px solid var(--border); border-radius: 14px; padding: 14px;
    }
    .plan-name { font-weight: 700; font-size: 15px; }
    .plan-price { color: var(--accent); font-weight: 700; font-size: 14px; }
    .page { display: none; flex-direction: column; gap: 14px; }
    .page.active { display: flex; }
    .nav {
      position: fixed; left: 0; right: 0; bottom: 0;
      background: rgba(12, 15, 21, 0.94); backdrop-filter: blur(12px);
      border-top: 1px solid var(--border); padding: 10px 16px;
    }
    .nav-inner { max-width: 520px; margin: 0 auto; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .nav-btn {
      background: var(--card-2); color: var(--muted); border: 1px solid var(--border);
      border-radius: 12px; padding: 10px; font-size: 13px; font-weight: 700; cursor: pointer;
    }
    .nav-btn.active { background: var(--warm-soft); color: var(--warm); border-color: rgba(255, 122, 47, 0.4); }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="brand">
        <img class="brand-logo" src="/logo_g.png" alt="Genumm VPN" />
        <div>
          <div class="brand-title">GENUMM</div>
          <div class="brand-sub">SPEED UP YOUR FREEDOM</div>
        </div>
      </div>
      <button class="lang-switch" id="langBtn" onclick="toggleLang()">EN</button>
    </div>

    <div id="page-cabinet" class="page active">
      <div class="card">
        <div class="card-label" id="lblStatusTitle">СТАТУС ЗАЩИТЫ</div>
        <div class="status-sub" id="lblValidUntil">Действует до</div>
        <div class="status-main" id="subStatusText">Нет активной подписки</div>
      </div>

      <div class="card">
        <div class="card-label" id="lblKeyTitle">ПЕРСОНАЛЬНЫЙ КЛЮЧ ДОСТУПА</div>
        <div class="key-box">
          <span id="vpnKeyText">Ключ появится сразу после оплаты</span>
          <button class="copy-btn" id="copyBtn" onclick="copyKey()">Copy</button>
        </div>
      </div>

      <div class="card">
        <div class="card-label" id="lblSupportTitle">ПОДДЕРЖКА И ОПЛАТА ЧЕРЕЗ ОПЕРАТОРА</div>
        <p class="status-sub" id="supportDesc">
          Помощь с подключением и оплата через СБП проходят напрямую через оператора @epitabh.
        </p>
        <a class="btn-primary btn-operator" id="operatorLink" href="https://t.me/epitabh" target="_blank">
          <span id="operatorBtnText">Написать оператору (@epitabh)</span>
        </a>
      </div>
    </div>

    <div id="page-tariffs" class="page">
      <div class="card">
        <div class="card-label" id="lblTariffsTitle">ТАРИФЫ И СБП</div>
        <div class="plan-item">
          <div>
            <div class="plan-name" id="p1Name">1 месяц</div>
            <div class="plan-price">299 ₽</div>
          </div>
          <a class="copy-btn" id="p1Btn" href="https://t.me/epitabh" target="_blank">СБП · @epitabh</a>
        </div>
        <div class="plan-item">
          <div>
            <div class="plan-name" id="p3Name">3 месяца (−15%)</div>
            <div class="plan-price">799 ₽</div>
          </div>
          <a class="copy-btn" id="p3Btn" href="https://t.me/epitabh" target="_blank">СБП · @epitabh</a>
        </div>
        <div class="plan-item">
          <div>
            <div class="plan-name" id="p12Name">1 год (−30%)</div>
            <div class="plan-price">2 490 ₽</div>
          </div>
          <a class="copy-btn" id="p12Btn" href="https://t.me/epitabh" target="_blank">СБП · @epitabh</a>
        </div>
        <button class="btn-primary btn-buy" id="starsCheckoutBtn" onclick="checkoutStars('1m')">
          Оплатить 1 месяц в боте (Stars)
        </button>
      </div>
    </div>
  </div>

  <div class="nav">
    <div class="nav-inner">
      <button class="nav-btn active" id="navCabinet" onclick="showPage('cabinet')">Кабинет</button>
      <button class="nav-btn" id="navTariffs" onclick="showPage('tariffs')">Тарифы</button>
    </div>
  </div>

  <script>
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg) { tg.ready(); tg.expand(); }

    let lang = "ru";
    let currentKey = "";

    const I18N = {
      ru: {
        toggle: "EN",
        statusTitle: "СТАТУС ЗАЩИТЫ",
        validUntil: "Действует до",
        noSub: "Нет активной подписки",
        keyTitle: "ПЕРСОНАЛЬНЫЙ КЛЮЧ ДОСТУПА",
        noKey: "Ключ появится сразу после оплаты",
        copied: "Скопировано",
        supportTitle: "ПОДДЕРЖКА И ОПЛАТА ЧЕРЕЗ ОПЕРАТОРА",
        supportDesc: "Помощь с подключением и оплата через СБП проходят напрямую через оператора @epitabh.",
        opBtn: "Написать оператору (@epitabh)",
        tariffsTitle: "ТАРИФЫ И СБП",
        p1: "1 месяц",
        p3: "3 месяца (−15%)",
        p12: "1 год (−30%)",
        sbpTag: "СБП · @epitabh",
        starsBtn: "Оплатить 1 месяц в боте (Stars)",
        navCab: "Кабинет",
        navTar: "Тарифы"
      },
      en: {
        toggle: "RU",
        statusTitle: "PROTECTION STATUS",
        validUntil: "Valid until",
        noSub: "No active subscription",
        keyTitle: "PERSONAL ACCESS KEY",
        noKey: "Key appears right after payment",
        copied: "Copied",
        supportTitle: "SUPPORT & OPERATOR PAYMENTS",
        supportDesc: "Connection help and SBP payments go directly through operator @epitabh.",
        opBtn: "Message operator (@epitabh)",
        tariffsTitle: "PLANS & SBP",
        p1: "1 Month",
        p3: "3 Months (−15%)",
        p12: "1 Year (−30%)",
        sbpTag: "SBP · @epitabh",
        starsBtn: "Pay 1 Month in bot (Stars)",
        navCab: "Cabinet",
        navTar: "Plans"
      }
    };

    function applyLang() {
      const d = I18N[lang];
      document.getElementById("langBtn").textContent = d.toggle;
      document.getElementById("lblStatusTitle").textContent = d.statusTitle;
      document.getElementById("lblValidUntil").textContent = d.validUntil;
      if (!currentKey) {
        document.getElementById("subStatusText").textContent = d.noSub;
        document.getElementById("vpnKeyText").textContent = d.noKey;
      }
      document.getElementById("lblKeyTitle").textContent = d.keyTitle;
      document.getElementById("lblSupportTitle").textContent = d.supportTitle;
      document.getElementById("supportDesc").textContent = d.supportDesc;
      document.getElementById("operatorBtnText").textContent = d.opBtn;
      document.getElementById("lblTariffsTitle").textContent = d.tariffsTitle;
      document.getElementById("p1Name").textContent = d.p1;
      document.getElementById("p3Name").textContent = d.p3;
      document.getElementById("p12Name").textContent = d.p12;
      document.getElementById("p1Btn").textContent = d.sbpTag;
      document.getElementById("p3Btn").textContent = d.sbpTag;
      document.getElementById("p12Btn").textContent = d.sbpTag;
      document.getElementById("starsCheckoutBtn").textContent = d.starsBtn;
      document.getElementById("navCabinet").textContent = d.navCab;
      document.getElementById("navTariffs").textContent = d.navTar;
    }

    function toggleLang() {
      lang = lang === "ru" ? "en" : "ru";
      applyLang();
    }

    function showPage(name) {
      document.getElementById("page-cabinet").classList.toggle("active", name === "cabinet");
      document.getElementById("page-tariffs").classList.toggle("active", name === "tariffs");
      document.getElementById("navCabinet").classList.toggle("active", name === "cabinet");
      document.getElementById("navTariffs").classList.toggle("active", name === "tariffs");
    }

    function copyKey() {
      if (!currentKey) return;
      navigator.clipboard.writeText(currentKey);
      const btn = document.getElementById("copyBtn");
      const prev = btn.textContent;
      btn.textContent = I18N[lang].copied;
      setTimeout(() => { btn.textContent = prev; }, 1200);
    }

    async function checkoutStars(plan) {
      if (tg && tg.initData) {
        await fetch("/api/checkout", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Telegram-Init-Data": tg.initData
          },
          body: JSON.stringify({ plan })
        });
        tg.close();
      }
    }

    async function loadProfile() {
      if (!tg || !tg.initData) return;
      try {
        const res = await fetch("/api/profile", {
          headers: { "X-Telegram-Init-Data": tg.initData }
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.lang === "en" || data.lang === "ru") {
          lang = data.lang;
        }
        if (data.vpn_key) {
          currentKey = data.vpn_key;
          document.getElementById("vpnKeyText").textContent = data.vpn_key;
        }
        if (data.sub_end) {
          document.getElementById("subStatusText").textContent = new Date(data.sub_end).toLocaleString();
        }
        applyLang();
      } catch (_) {}
    }

    applyLang();
    loadProfile();
  </script>
</body>
</html>
'''

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_code.strip() + "\n")

print("Updated bot.py and index.html successfully.")
