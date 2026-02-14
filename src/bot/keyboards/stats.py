"""
Statistics keyboards.
"""

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def stats_menu_keyboard() -> InlineKeyboardMarkup:
    """Main statistics menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001f4ca \u041e\u0431\u0449\u0430\u044f \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430",  # 📊 Общая статистика
                    callback_data="stats:overview",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4ac \u041f\u043e \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044f\u043c",  # 💬 По кампаниям
                    callback_data="stats:campaigns:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4c3 \u0416\u0443\u0440\u043d\u0430\u043b \u0441\u043e\u0431\u044b\u0442\u0438\u0439",  # 📃 Журнал событий
                    callback_data="stats:events:0",
                ),
            ],
        ],
    )


def stats_period_keyboard(base_callback: str) -> InlineKeyboardMarkup:
    """Choose period for statistics."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1\u0447",  # 1ч
                    callback_data=f"{base_callback}:1",
                ),
                InlineKeyboardButton(
                    text="6\u0447",  # 6ч
                    callback_data=f"{base_callback}:6",
                ),
                InlineKeyboardButton(
                    text="24\u0447",  # 24ч
                    callback_data=f"{base_callback}:24",
                ),
                InlineKeyboardButton(
                    text="7\u0434",  # 7д
                    callback_data=f"{base_callback}:168",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\u2b05 \u041d\u0430\u0437\u0430\u0434",  # ⬅ Назад
                    callback_data="stats:menu",
                ),
            ],
        ],
    )


def campaign_stats_list_keyboard(
    campaigns: list,
    offset: int = 0,
    total: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated campaign list for statistics."""
    buttons: list[list[InlineKeyboardButton]] = []

    for c in campaigns:
        status_icon = {
            "draft": "\u2712",       # ✒
            "active": "\u25b6",      # ▶
            "paused": "\u23f8",      # ⏸
            "completed": "\u2705",   # ✅
        }.get(c.status.value, "\u2753")  # ❓

        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {c.name}",
                callback_data=f"stats:campaign:{c.id}",
            )
        ])

    # Pagination
    nav_buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="\u25c0 \u041d\u0430\u0437\u0430\u0434",  # ◀ Назад
                callback_data=f"stats:campaigns:{offset - page_size}",
            )
        )
    if offset + page_size < total:
        nav_buttons.append(
            InlineKeyboardButton(
                text="\u0412\u043f\u0435\u0440\u0451\u0434 \u25b6",  # Вперёд ▶
                callback_data=f"stats:campaigns:{offset + page_size}",
            )
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(
            text="\u2b05 \u041d\u0430\u0437\u0430\u0434",  # ⬅ Назад
            callback_data="stats:menu",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def campaign_stats_detail_keyboard(campaign_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Campaign detail stats — period selector + event log."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001f4c3 \u0421\u043e\u0431\u044b\u0442\u0438\u044f \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438",  # 📃 События кампании
                    callback_data=f"stats:campaign_events:{campaign_id}:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\u2b05 \u041a \u0441\u043f\u0438\u0441\u043a\u0443",  # ⬅ К списку
                    callback_data="stats:campaigns:0",
                ),
            ],
        ],
    )


def events_list_keyboard(
    has_more: bool,
    offset: int,
    page_size: int = 20,
    *,
    campaign_id: uuid.UUID | None = None,
) -> InlineKeyboardMarkup:
    """Pagination for event log."""
    buttons: list[list[InlineKeyboardButton]] = []

    nav_buttons: list[InlineKeyboardButton] = []

    if campaign_id:
        back_callback = f"stats:campaign:{campaign_id}"
        next_callback = f"stats:campaign_events:{campaign_id}:{offset + page_size}"
        prev_callback = f"stats:campaign_events:{campaign_id}:{max(0, offset - page_size)}"
    else:
        back_callback = "stats:menu"
        next_callback = f"stats:events:{offset + page_size}"
        prev_callback = f"stats:events:{max(0, offset - page_size)}"

    if offset > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="\u25c0 \u041d\u0430\u0437\u0430\u0434",  # ◀ Назад
                callback_data=prev_callback,
            )
        )
    if has_more:
        nav_buttons.append(
            InlineKeyboardButton(
                text="\u0412\u043f\u0435\u0440\u0451\u0434 \u25b6",  # Вперёд ▶
                callback_data=next_callback,
            )
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(
            text="\u2b05 \u041d\u0430\u0437\u0430\u0434",  # ⬅ Назад
            callback_data=back_callback,
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
