"""
Settings handlers — notification preferences management.

Users can toggle 4 notification categories:
- comments: comment posted/deleted/reposted/failed
- bans: account banned, channel access denied
- errors: critical errors, worker failures, flood waits
- rotations: account rotated to new channel, no free channels
"""

import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards.settings import settings_menu_keyboard
from src.core.logging import get_logger
from src.db.repositories.user_repo import UserRepository

log = get_logger(__name__)

router = Router(name="settings")

# Valid notification categories
_VALID_CATEGORIES = {"comments", "bans", "errors", "rotations"}

# Category labels for display
_CATEGORY_LABELS: dict[str, str] = {
    "comments": "\U0001f4ac \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438",  # 💬 Комментарии
    "bans": "\U0001f6ab \u0411\u0430\u043d\u044b",                                                  # 🚫 Баны
    "errors": "\U0001f534 \u041e\u0448\u0438\u0431\u043a\u0438",                                    # 🔴 Ошибки
    "rotations": "\U0001f504 \u0420\u043e\u0442\u0430\u0446\u0438\u0438",                           # 🔄 Ротации
}


# ============================================================
# Menu
# ============================================================


@router.message(F.text == "\u2699\ufe0f \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438")  # ⚙️ Настройки
async def settings_menu(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show settings menu with current notification preferences."""
    await state.clear()

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(owner_id)
    if user is None:
        await message.answer("\u274c \u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")  # ❌ Пользователь не найден
        return

    prefs = user.notification_prefs or {
        "comments": True, "bans": True, "errors": True, "rotations": True
    }

    text = _build_settings_text(prefs)

    await message.answer(
        text,
        reply_markup=settings_menu_keyboard(prefs),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "settings:menu")
async def settings_menu_cb(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Return to settings menu (inline button)."""
    await state.clear()

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(owner_id)
    if user is None:
        await callback.answer("\u041e\u0448\u0438\u0431\u043a\u0430")  # Ошибка
        return

    prefs = user.notification_prefs or {
        "comments": True, "bans": True, "errors": True, "rotations": True
    }

    await callback.message.edit_text(
        _build_settings_text(prefs),
        reply_markup=settings_menu_keyboard(prefs),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Toggle notification category
# ============================================================


@router.callback_query(F.data.startswith("settings:toggle:"))
async def toggle_notification(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Toggle a notification category on/off."""
    category = callback.data.split(":")[-1]

    if category not in _VALID_CATEGORIES:
        await callback.answer("\u274c \u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f")  # ❌ Неизвестная категория
        return

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(owner_id)
    if user is None:
        await callback.answer("\u041e\u0448\u0438\u0431\u043a\u0430")  # Ошибка
        return

    # Toggle the preference
    prefs = dict(user.notification_prefs or {
        "comments": True, "bans": True, "errors": True, "rotations": True
    })
    current_value = prefs.get(category, True)
    prefs[category] = not current_value

    # Save
    await user_repo.update_notification_prefs(owner_id, prefs)

    # Feedback
    label = _CATEGORY_LABELS.get(category, category)
    new_state = "\u2705 \u0412\u043a\u043b" if prefs[category] else "\u274c \u0412\u044b\u043a\u043b"  # ✅ Вкл / ❌ Выкл

    log.info(
        "notification_pref_toggled",
        owner_id=str(owner_id)[:8],
        category=category,
        enabled=prefs[category],
    )

    # Update the menu with new state
    await callback.message.edit_text(
        _build_settings_text(prefs),
        reply_markup=settings_menu_keyboard(prefs),
        parse_mode="HTML",
    )
    await callback.answer(f"{label}: {new_state}")


# ============================================================
# Helpers
# ============================================================


def _build_settings_text(prefs: dict) -> str:
    """Build the settings menu text showing current preferences."""
    lines = [
        "\u2699\ufe0f <b>\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u0443\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u0439</b>\n",  # ⚙️ Настройки уведомлений
        "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043d\u0430 \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044e, "  # Нажмите на категорию,
        "\u0447\u0442\u043e\u0431\u044b \u0432\u043a\u043b\u044e\u0447\u0438\u0442\u044c/\u0432\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c:\n",  # чтобы включить/выключить:
    ]

    for cat_key, cat_label in _CATEGORY_LABELS.items():
        enabled = prefs.get(cat_key, True)
        status = "\u2705 \u0412\u043a\u043b" if enabled else "\u274c \u0412\u044b\u043a\u043b"  # ✅ Вкл / ❌ Выкл
        lines.append(f"  {cat_label}: {status}")

    return "\n".join(lines)
