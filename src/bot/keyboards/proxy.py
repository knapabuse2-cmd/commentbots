"""
Proxy management keyboards.
"""

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def proxy_menu_keyboard() -> InlineKeyboardMarkup:
    """Proxy management menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u2795 \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u043e\u043a\u0441\u0438",  # ➕ Добавить прокси
                    callback_data="proxy:add",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4cb \u0421\u043f\u0438\u0441\u043e\u043a \u043f\u0440\u043e\u043a\u0441\u0438",           # 📋 Список прокси
                    callback_data="proxy:list:0",
                ),
            ],
        ],
    )


def proxy_list_keyboard(
    proxies: list,
    offset: int = 0,
    total: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated proxy list."""
    buttons: list[list[InlineKeyboardButton]] = []

    for p in proxies:
        buttons.append([
            InlineKeyboardButton(
                text=f"\U0001f310 {p.address}",  # 🌐
                callback_data=f"proxy:detail:{p.id}",
            )
        ])

    nav_buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="\u25c0 \u041d\u0430\u0437\u0430\u0434", callback_data=f"proxy:list:{offset - page_size}")
        )
    if offset + page_size < total:
        nav_buttons.append(
            InlineKeyboardButton(text="\u0412\u043f\u0435\u0440\u0451\u0434 \u25b6", callback_data=f"proxy:list:{offset + page_size}")
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(text="\u2b05 \u041d\u0430\u0437\u0430\u0434", callback_data="proxy:menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def proxy_detail_keyboard(proxy_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Proxy detail — delete button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001f5d1 \u0423\u0434\u0430\u043b\u0438\u0442\u044c",  # 🗑 Удалить
                    callback_data=f"proxy:delete:{proxy_id}",
                ),
            ],
            [
                InlineKeyboardButton(text="\u2b05 \u041a \u0441\u043f\u0438\u0441\u043a\u0443", callback_data="proxy:list:0")
            ],
        ],
    )
