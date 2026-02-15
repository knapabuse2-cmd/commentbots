"""
Campaign management keyboards.
"""

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def campaigns_menu_keyboard() -> InlineKeyboardMarkup:
    """Campaigns main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u2795 \u0421\u043e\u0437\u0434\u0430\u0442\u044c \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044e",
                    callback_data="camp:create",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4cb \u0421\u043f\u0438\u0441\u043e\u043a \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0439",
                    callback_data="camp:list:0",
                ),
            ],
        ],
    )


def campaign_list_keyboard(
    campaigns: list,
    offset: int = 0,
    total: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated campaign list."""
    buttons: list[list[InlineKeyboardButton]] = []

    status_emoji = {
        "draft": "\U0001f4dd",      # 📝
        "active": "\u25b6\ufe0f",   # ▶️
        "paused": "\u23f8",         # ⏸
        "completed": "\u2705",      # ✅
    }

    for camp in campaigns:
        emoji = status_emoji.get(camp.status.value, "\u2753")
        label = f"{emoji} {camp.name}"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"camp:detail:{camp.id}",
            )
        ])

    # Pagination
    nav_buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="\u25c0 \u041d\u0430\u0437\u0430\u0434", callback_data=f"camp:list:{offset - page_size}")
        )
    if offset + page_size < total:
        nav_buttons.append(
            InlineKeyboardButton(text="\u0412\u043f\u0435\u0440\u0451\u0434 \u25b6", callback_data=f"camp:list:{offset + page_size}")
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(text="\u2b05 \u041d\u0430\u0437\u0430\u0434", callback_data="camp:menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def campaign_detail_keyboard(
    campaign_id: uuid.UUID, status: str
) -> InlineKeyboardMarkup:
    """Campaign detail view with all actions."""
    cid = str(campaign_id)
    buttons: list[list[InlineKeyboardButton]] = []

    # Message
    buttons.append([
        InlineKeyboardButton(
            text="\U0001f4dd \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435",     # 📝 Сообщение
            callback_data=f"camp:msg:{cid}",
        ),
    ])

    # Channels
    buttons.append([
        InlineKeyboardButton(
            text="\U0001f4fa \u041a\u0430\u043d\u0430\u043b\u044b",                        # 📺 Каналы
            callback_data=f"camp:channels:{cid}:0",
        ),
        InlineKeyboardButton(
            text="\u2795 \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b\u044b",  # ➕ Добавить каналы
            callback_data=f"camp:add_channels:{cid}",
        ),
    ])

    # Accounts
    buttons.append([
        InlineKeyboardButton(
            text="\U0001f464 \u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b",            # 👤 Аккаунты
            callback_data=f"camp:accounts:{cid}",
        ),
        InlineKeyboardButton(
            text="\U0001f504 \u0420\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c",  # 🔄 Распределить
            callback_data=f"camp:distribute:{cid}",
        ),
    ])

    # Profile bio
    buttons.append([
        InlineKeyboardButton(
            text="\U0001f4cb \u0411\u0438\u043e \u043f\u0440\u043e\u0444\u0438\u043b\u044f",  # 📋 Био профиля
            callback_data=f"camp:bio:{cid}",
        ),
    ])

    # Start / Pause
    if status in ("draft", "paused"):
        buttons.append([
            InlineKeyboardButton(
                text="\u25b6 \u0417\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c",      # ▶ Запустить
                callback_data=f"camp:start:{cid}",
            ),
        ])
    elif status == "active":
        buttons.append([
            InlineKeyboardButton(
                text="\u23f8 \u041f\u0430\u0443\u0437\u0430",                               # ⏸ Пауза
                callback_data=f"camp:pause:{cid}",
            ),
        ])

    # Delete
    if status != "active":
        buttons.append([
            InlineKeyboardButton(
                text="\U0001f5d1 \u0423\u0434\u0430\u043b\u0438\u0442\u044c",              # 🗑 Удалить
                callback_data=f"camp:delete:{cid}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(text="\u2b05 \u041a \u0441\u043f\u0438\u0441\u043a\u0443", callback_data="camp:list:0")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def campaign_accounts_keyboard(
    campaign_id: uuid.UUID,
    available_accounts: list,
    assigned_account_ids: set[uuid.UUID],
) -> InlineKeyboardMarkup:
    """Account selection for a campaign — toggle add/remove.

    Uses short hex IDs to stay within Telegram's 64-byte callback_data limit.
    campaign_id is stored in FSM state by the handler.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    for acc in available_accounts:
        # Use .hex (no dashes, 32 chars) to keep callback_data short
        aid = acc.id.hex
        is_assigned = acc.id in assigned_account_ids
        if is_assigned:
            label = f"\u2705 {acc.display_name}"  # ✅
            action = f"ca:rm:{aid}"
        else:
            label = f"\u2b1c {acc.display_name}"   # ⬜
            action = f"ca:add:{aid}"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=action)
        ])

    cid = str(campaign_id)
    buttons.append([
        InlineKeyboardButton(text="\u2b05 \u041d\u0430\u0437\u0430\u0434", callback_data=f"camp:detail:{cid}")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
