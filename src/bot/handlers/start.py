"""
Start handler — /start command and main menu navigation.
"""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards.main import main_menu_keyboard

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Handle /start command — show main menu."""
    await state.clear()
    await message.answer(
        "\U0001f916 <b>CommentBot v2.0</b>\n\n"  # 🤖
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435:",  # Выберите действие:
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel any ongoing FSM flow."""
    await state.clear()
    await callback.message.edit_text("\u274c \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.")  # ❌ Отменено.
    await callback.answer()
