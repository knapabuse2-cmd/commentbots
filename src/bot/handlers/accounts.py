"""
Account management handlers.

Flows:
1. Add by phone: enter phone → receive code → enter code → (optional 2FA) → done
2. Import session: upload .session file → validate → done
3. List accounts: paginated list → detail view → pause/resume/delete
"""

import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards.accounts import (
    account_detail_keyboard,
    account_list_keyboard,
    accounts_menu_keyboard,
    cancel_keyboard,
)
from src.bot.keyboards.main import main_menu_keyboard
from src.bot.states import AccountStates
from src.core.exceptions import AccountAuthError, AccountBannedError, AccountFloodWaitError, OwnershipError
from src.core.logging import get_logger
from src.services.account_service import AccountService

log = get_logger(__name__)

router = Router(name="accounts")

PAGE_SIZE = 10


# ============================================================
# Menu entry point
# ============================================================


@router.message(F.text == "\U0001f4f1 \u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b")  # 📱 Аккаунты
async def accounts_menu(message: Message, state: FSMContext) -> None:
    """Show accounts management menu."""
    await state.clear()
    await message.answer(
        "\U0001f4f1 <b>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430\u043c\u0438</b>",  # 📱 Управление аккаунтами
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "acc:menu")
async def accounts_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to accounts menu (from inline button)."""
    await state.clear()
    await callback.message.edit_text(
        "\U0001f4f1 <b>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430\u043c\u0438</b>",
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Add by phone
# ============================================================


@router.callback_query(F.data == "acc:add_phone")
async def add_phone_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Start phone authorization flow."""
    await state.set_state(AccountStates.waiting_phone)
    await callback.message.edit_text(
        "\U0001f4f1 \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430 "  # 📱 Введите номер телефона
        "(\u0441 \u043a\u043e\u0434\u043e\u043c \u0441\u0442\u0440\u0430\u043d\u044b, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 <code>+380123456789</code>):",  # (с кодом страны, например ...)
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AccountStates.waiting_phone)
async def add_phone_receive(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Receive phone number and send auth code."""
    phone = message.text.strip()

    # Basic validation
    if not phone.startswith("+") or len(phone) < 10:
        await message.answer(
            "\u274c \u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442. "  # ❌ Неверный формат.
            "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440 \u0441 <code>+</code> \u0438 \u043a\u043e\u0434\u043e\u043c \u0441\u0442\u0440\u0430\u043d\u044b:",  # Введите номер с + и кодом страны:
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    svc = AccountService(session)

    try:
        account_id, phone_code_hash = await svc.start_auth(owner_id, phone)

        await state.update_data(account_id=str(account_id), phone=phone)
        await state.set_state(AccountStates.waiting_code)

        await message.answer(
            f"\U0001f4e9 \u041a\u043e\u0434 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d \u043d\u0430 <code>{phone}</code>\n"  # 📩 Код отправлен на ...
            "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u0434 \u0438\u0437 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f:",  # Введите код из сообщения:
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )

    except AccountAuthError as e:
        await message.answer(f"\u274c {e.message}", reply_markup=cancel_keyboard())  # ❌
        await state.clear()
    except AccountBannedError as e:
        await message.answer(f"\U0001f6ab {e.message}", reply_markup=cancel_keyboard())  # 🚫
        await state.clear()
    except AccountFloodWaitError as e:
        await message.answer(
            f"\u23f3 Telegram \u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0435. "  # ⏳ Telegram ограничение.
            f"\u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435 {e.seconds} \u0441\u0435\u043a.",  # Подождите N сек.
            reply_markup=cancel_keyboard(),
        )
        await state.clear()


@router.message(AccountStates.waiting_code)
async def add_phone_verify_code(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Receive and verify SMS code."""
    code = message.text.strip()
    data = await state.get_data()
    account_id = uuid.UUID(data["account_id"])

    svc = AccountService(session)

    try:
        await svc.verify_code(account_id, code, owner_id=owner_id)

        await state.clear()
        await message.answer(
            "\u2705 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0430\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d!",  # ✅ Аккаунт успешно авторизован!
            reply_markup=main_menu_keyboard(),
        )

    except AccountAuthError as e:
        if e.context.get("needs_2fa"):
            await state.set_state(AccountStates.waiting_2fa)
            await message.answer(
                "\U0001f512 \u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u043f\u0430\u0440\u043e\u043b\u044c 2FA.\n"  # 🔒 Требуется пароль 2FA.
                "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0430\u0440\u043e\u043b\u044c:",  # Введите пароль:
                reply_markup=cancel_keyboard(),
            )
        else:
            await message.answer(f"\u274c {e.message}", reply_markup=cancel_keyboard())  # ❌
            await state.clear()
    except AccountFloodWaitError as e:
        await message.answer(
            f"\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435 {e.seconds} \u0441\u0435\u043a.",  # ⏳ Подождите N сек.
            reply_markup=cancel_keyboard(),
        )


@router.message(AccountStates.waiting_2fa)
async def add_phone_verify_2fa(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Receive and verify 2FA password."""
    password = message.text.strip()
    data = await state.get_data()
    account_id = uuid.UUID(data["account_id"])

    svc = AccountService(session)

    try:
        await svc.verify_2fa(account_id, password, owner_id=owner_id)

        await state.clear()
        await message.answer(
            "\u2705 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0430\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d (2FA)!",  # ✅ Аккаунт успешно авторизован (2FA)!
            reply_markup=main_menu_keyboard(),
        )

    except AccountAuthError as e:
        await message.answer(f"\u274c {e.message}", reply_markup=cancel_keyboard())  # ❌
    except AccountFloodWaitError as e:
        await message.answer(f"\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435 {e.seconds} \u0441\u0435\u043a.")  # ⏳


# ============================================================
# Import session file
# ============================================================


@router.callback_query(F.data == "acc:add_session")
async def add_session_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Start session file import flow."""
    await state.set_state(AccountStates.waiting_session_file)
    await callback.message.edit_text(
        "\U0001f4c1 \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0444\u0430\u0439\u043b <code>.session</code> "  # 📁 Отправьте файл .session
        "\u0438\u043b\u0438 \u0441\u0442\u0440\u043e\u043a\u0443 StringSession:",  # или строку StringSession:
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AccountStates.waiting_session_file)
async def add_session_receive(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Receive session file or string and import."""
    session_string = None

    # Check if it's a text message (StringSession string)
    if message.text:
        session_string = message.text.strip()

    # Check if it's a document (.session file)
    elif message.document:
        try:
            file = await message.bot.download(message.document)
            content = file.read().decode("utf-8", errors="ignore").strip()
            if content:
                session_string = content
        except Exception as e:
            await message.answer(
                f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0447\u0442\u0435\u043d\u0438\u044f \u0444\u0430\u0439\u043b\u0430: {e}",  # ❌ Ошибка чтения файла:
                reply_markup=cancel_keyboard(),
            )
            return

    if not session_string:
        await message.answer(
            "\u274c \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u0442\u0440\u043e\u043a\u0443 StringSession \u0438\u043b\u0438 \u0444\u0430\u0439\u043b .session",  # ❌ Отправьте строку StringSession или файл .session
            reply_markup=cancel_keyboard(),
        )
        return

    svc = AccountService(session)

    try:
        account = await svc.import_session(owner_id, session_string)
        await state.clear()
        await message.answer(
            f"\u2705 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0438\u043c\u043f\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u043d: <b>{account.display_name}</b>",  # ✅ Аккаунт импортирован: ...
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
    except AccountAuthError as e:
        await message.answer(f"\u274c {e.message}", reply_markup=cancel_keyboard())  # ❌
    except Exception as e:
        log.error("session_import_error", error=str(e))
        await message.answer(
            f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0438\u043c\u043f\u043e\u0440\u0442\u0430: {e}",  # ❌ Ошибка импорта:
            reply_markup=cancel_keyboard(),
        )


# ============================================================
# List accounts
# ============================================================


@router.callback_query(F.data.startswith("acc:list:"))
async def list_accounts(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show paginated list of accounts."""
    offset = int(callback.data.split(":")[-1])

    svc = AccountService(session)
    accounts = await svc.get_accounts(owner_id, offset=offset, limit=PAGE_SIZE)
    total = await svc.count_accounts(owner_id)

    if not accounts and offset == 0:
        await callback.message.edit_text(
            "\U0001f4cb \u0410\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.\n"  # 📋 Аккаунтов пока нет.
            "\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043f\u0435\u0440\u0432\u044b\u0439 \u0430\u043a\u043a\u0430\u0443\u043d\u0442!",  # Добавьте первый аккаунт!
            reply_markup=accounts_menu_keyboard(),
        )
        await callback.answer()
        return

    text = (
        f"\U0001f4cb <b>\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b</b> "  # 📋 Аккаунты
        f"({offset + 1}-{min(offset + PAGE_SIZE, total)} \u0438\u0437 {total}):"  # (X-Y из Z):
    )

    await callback.message.edit_text(
        text,
        reply_markup=account_list_keyboard(accounts, offset, total, PAGE_SIZE),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Account detail
# ============================================================


@router.callback_query(F.data.startswith("acc:detail:"))
async def account_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show details for a single account."""
    account_id = uuid.UUID(callback.data.split(":")[-1])

    svc = AccountService(session)
    try:
        account = await svc.get_account(account_id, owner_id=owner_id)
    except OwnershipError:
        await callback.message.edit_text("\u274c \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")  # ❌ Аккаунт не найден
        await callback.answer()
        return

    # Status emoji
    status_labels = {
        "active": "\u2705 \u0410\u043a\u0442\u0438\u0432\u0435\u043d",       # ✅ Активен
        "paused": "\u23f8 \u041d\u0430 \u043f\u0430\u0443\u0437\u0435",      # ⏸ На паузе
        "banned": "\U0001f6ab \u0417\u0430\u0431\u0430\u043d\u0435\u043d",    # 🚫 Забанен
        "error": "\u274c \u041e\u0448\u0438\u0431\u043a\u0430",              # ❌ Ошибка
        "pending": "\u23f3 \u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435", # ⏳ Ожидание
        "auth_code": "\U0001f4e9 \u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435 \u043a\u043e\u0434\u0430",  # 📩 Ожидание кода
        "auth_2fa": "\U0001f512 \u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435 2FA",   # 🔒 Ожидание 2FA
    }

    status_text = status_labels.get(account.status.value, account.status.value)

    phone_display = account.phone or "\u043d\u0435\u0442"
    name_display = account.first_name or "\u043d\u0435\u0442"
    text = (
        f"\U0001f464 <b>\u0410\u043a\u043a\u0430\u0443\u043d\u0442</b>\n\n"  # 👤 Аккаунт
        f"\U0001f4f1 \u0422\u0435\u043b\u0435\u0444\u043e\u043d: <code>{phone_display}</code>\n"  # 📱 Телефон:
        f"\U0001f464 \u0418\u043c\u044f: {name_display}\n"  # 👤 Имя:
        f"\U0001f4ca \u0421\u0442\u0430\u0442\u0443\u0441: {status_text}\n"  # 📊 Статус:
        f"\U0001f4c5 \u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d: {account.created_at.strftime('%d.%m.%Y %H:%M')}"  # 📅 Добавлен:
    )

    await callback.message.edit_text(
        text,
        reply_markup=account_detail_keyboard(account.id, account.status.value),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Pause / Resume / Delete
# ============================================================


@router.callback_query(F.data.startswith("acc:pause:"))
async def pause_account(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Pause an active account."""
    account_id = uuid.UUID(callback.data.split(":")[-1])
    svc = AccountService(session)
    try:
        account = await svc.pause_account(account_id, owner_id=owner_id)
    except OwnershipError:
        await callback.message.edit_text("\u274c \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        await callback.answer()
        return

    if account:
        await callback.message.edit_text(
            f"\u23f8 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 <b>{account.display_name}</b> \u043f\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d \u043d\u0430 \u043f\u0430\u0443\u0437\u0443",  # ⏸ Аккаунт ... поставлен на паузу
            reply_markup=account_detail_keyboard(account.id, account.status.value),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("acc:resume:"))
async def resume_account(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Resume a paused account."""
    account_id = uuid.UUID(callback.data.split(":")[-1])
    svc = AccountService(session)
    try:
        account = await svc.resume_account(account_id, owner_id=owner_id)
    except OwnershipError:
        await callback.message.edit_text("\u274c \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        await callback.answer()
        return

    if account:
        await callback.message.edit_text(
            f"\u25b6 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 <b>{account.display_name}</b> \u0432\u043e\u0437\u043e\u0431\u043d\u043e\u0432\u043b\u0451\u043d",  # ▶ Аккаунт ... возобновлён
            reply_markup=account_detail_keyboard(account.id, account.status.value),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("acc:delete:"))
async def delete_account(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Delete an account."""
    account_id = uuid.UUID(callback.data.split(":")[-1])
    svc = AccountService(session)
    try:
        deleted = await svc.delete_account(account_id, owner_id=owner_id)
    except OwnershipError:
        await callback.message.edit_text("\u274c \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        await callback.answer()
        return

    if deleted:
        await callback.message.edit_text(
            "\U0001f5d1 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0443\u0434\u0430\u043b\u0451\u043d",  # 🗑 Аккаунт удалён
        )
    else:
        await callback.message.edit_text("\u274c \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")  # ❌ Аккаунт не найден
    await callback.answer()
