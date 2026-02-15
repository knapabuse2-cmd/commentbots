"""
Proxy management handlers.

Flows:
1. Add proxy: enter host:port:user:pass → validate → save
2. List proxies: paginated list → detail → delete
3. Assign proxy to account (from account detail, future step)
"""

import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards.accounts import cancel_keyboard
from src.bot.keyboards.proxy import (
    proxy_detail_keyboard,
    proxy_list_keyboard,
    proxy_menu_keyboard,
)
from src.bot.states import ProxyStates
from src.core.logging import get_logger
from src.db.repositories.proxy_repo import ProxyRepository

log = get_logger(__name__)

router = Router(name="proxy")

PAGE_SIZE = 10


# ============================================================
# Menu
# ============================================================


@router.message(F.text == "\U0001f310 \u041f\u0440\u043e\u043a\u0441\u0438")  # 🌐 Прокси
async def proxy_menu(message: Message, state: FSMContext) -> None:
    """Show proxy management menu."""
    await state.clear()
    await message.answer(
        "\U0001f310 <b>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u043a\u0441\u0438</b>",  # 🌐 Управление прокси
        reply_markup=proxy_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "proxy:menu")
async def proxy_menu_cb(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to proxy menu (inline button)."""
    await state.clear()
    await callback.message.edit_text(
        "\U0001f310 <b>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u043a\u0441\u0438</b>",
        reply_markup=proxy_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Add proxy
# ============================================================


@router.callback_query(F.data == "proxy:add")
async def add_proxy_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Start proxy addition flow."""
    await state.set_state(ProxyStates.waiting_proxy_input)
    await callback.message.edit_text(
        "\U0001f310 \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0440\u043e\u043a\u0441\u0438 "  # 🌐 Введите прокси
        "(\u043c\u043e\u0436\u043d\u043e \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e, \u043a\u0430\u0436\u0434\u044b\u0439 \u0441 \u043d\u043e\u0432\u043e\u0439 \u0441\u0442\u0440\u043e\u043a\u0438):\n\n"  # (можно несколько, каждый с новой строки):
        "<i>\u0424\u043e\u0440\u043c\u0430\u0442:\n"  # Формат:
        "host:port\n"
        "host:port:username:password</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ProxyStates.waiting_proxy_input)
async def add_proxy_receive(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Parse proxy input and save."""
    text = message.text
    if not text or not text.strip():
        await message.answer(
            "\u274c \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0440\u043e\u043a\u0441\u0438 \u0432 \u0444\u043e\u0440\u043c\u0430\u0442\u0435 host:port[:user:pass]",  # ❌ Введите прокси в формате host:port[:user:pass]
            reply_markup=cancel_keyboard(),
        )
        return

    repo = ProxyRepository(session)
    added = 0
    skipped = 0
    errors: list[str] = []

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split(":")
        if len(parts) < 2:
            errors.append(f"{line} — \u043d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442")  # неверный формат
            continue

        host = parts[0].strip()
        try:
            port = int(parts[1].strip())
        except ValueError:
            errors.append(f"{line} — \u043d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u043e\u0440\u0442")  # неверный порт
            continue

        if port < 1 or port > 65535:
            errors.append(f"{line} — \u043f\u043e\u0440\u0442 \u0432\u043d\u0435 \u0434\u0438\u0430\u043f\u0430\u0437\u043e\u043d\u0430")  # порт вне диапазона
            continue

        username = parts[2].strip() if len(parts) > 2 else None
        password = parts[3].strip() if len(parts) > 3 else None

        # Check duplicate
        existing = await repo.find_by_address(owner_id, host, port)
        if existing:
            skipped += 1
            continue

        await repo.create(
            owner_id=owner_id,
            host=host,
            port=port,
            username=username,
            password=password,
        )
        added += 1

        log.info(
            "proxy_added",
            owner_id=str(owner_id),
            host=host,
            port=port,
            has_auth=bool(username),
        )

    await state.clear()

    result_text = f"\u2705 \u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e: <b>{added}</b>"  # ✅ Добавлено: N
    if skipped:
        result_text += f"\n\u23ed \u041f\u0440\u043e\u043f\u0443\u0449\u0435\u043d\u043e (\u0434\u0443\u0431\u043b\u0438): {skipped}"  # ⏭ Пропущено (дубли): N
    if errors:
        err_preview = "\n".join(errors[:5])
        result_text += f"\n\u274c \u041e\u0448\u0438\u0431\u043a\u0438:\n<code>{err_preview}</code>"  # ❌ Ошибки:
        if len(errors) > 5:
            result_text += f"\n... \u0438 \u0435\u0449\u0451 {len(errors) - 5}"  # ... и ещё N

    await message.answer(
        result_text,
        reply_markup=proxy_menu_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# List
# ============================================================


@router.callback_query(F.data.startswith("proxy:list:"))
async def list_proxies(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show paginated list of proxies."""
    offset = int(callback.data.split(":")[-1])
    repo = ProxyRepository(session)
    proxies = await repo.get_by_owner(owner_id, offset=offset, limit=PAGE_SIZE)
    total = await repo.count_by_owner(owner_id)

    if not proxies and offset == 0:
        await callback.message.edit_text(
            "\U0001f4cb \u041f\u0440\u043e\u043a\u0441\u0438 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.\n"  # 📋 Прокси пока нет.
            "\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043f\u0435\u0440\u0432\u044b\u0439!",  # Добавьте первый!
            reply_markup=proxy_menu_keyboard(),
        )
        await callback.answer()
        return

    text = (
        f"\U0001f4cb <b>\u041f\u0440\u043e\u043a\u0441\u0438</b> "  # 📋 Прокси
        f"({offset + 1}-{min(offset + PAGE_SIZE, total)} \u0438\u0437 {total}):"  # (X-Y из Z):
    )

    await callback.message.edit_text(
        text,
        reply_markup=proxy_list_keyboard(proxies, offset, total, PAGE_SIZE),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Detail
# ============================================================


@router.callback_query(F.data.startswith("proxy:detail:"))
async def proxy_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show proxy details."""
    proxy_id = uuid.UUID(callback.data.split(":")[-1])
    repo = ProxyRepository(session)
    proxy = await repo.get_by_id(proxy_id)

    if proxy is None or proxy.owner_id != owner_id:
        await callback.message.edit_text(
            "\u274c \u041f\u0440\u043e\u043a\u0441\u0438 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e",  # ❌ Прокси не найдено
        )
        await callback.answer()
        return

    # Count linked accounts
    from src.db.models.account import AccountModel
    from sqlalchemy import select, func

    stmt = select(func.count()).select_from(AccountModel).where(
        AccountModel.proxy_id == proxy_id
    )
    result = await session.execute(stmt)
    linked_accounts = result.scalar_one()

    auth_status = "\u2705 \u0435\u0441\u0442\u044c" if proxy.username else "\u274c \u043d\u0435\u0442"  # ✅ есть / ❌ нет

    text = (
        f"\U0001f310 <b>\u041f\u0440\u043e\u043a\u0441\u0438</b>\n\n"  # 🌐 Прокси
        f"\U0001f3e0 \u0425\u043e\u0441\u0442: <code>{proxy.host}</code>\n"  # 🏠 Хост:
        f"\U0001f6aa \u041f\u043e\u0440\u0442: <code>{proxy.port}</code>\n"  # 🚪 Порт:
        f"\U0001f512 \u0410\u0432\u0442\u043e\u0440\u0438\u0437\u0430\u0446\u0438\u044f: {auth_status}\n"  # 🔒 Авторизация:
        f"\U0001f517 \u041f\u0440\u0438\u0432\u044f\u0437\u0430\u043d\u043e \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432: {linked_accounts}\n"  # 🔗 Привязано аккаунтов:
        f"\U0001f4c5 \u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e: {proxy.created_at.strftime('%d.%m.%Y %H:%M')}"  # 📅 Добавлено:
    )

    await callback.message.edit_text(
        text,
        reply_markup=proxy_detail_keyboard(proxy.id),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Delete
# ============================================================


@router.callback_query(F.data.startswith("proxy:delete:"))
async def delete_proxy(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Delete a proxy (unlinks from accounts first)."""
    proxy_id = uuid.UUID(callback.data.split(":")[-1])
    repo = ProxyRepository(session)

    # Verify ownership before deleting
    proxy = await repo.get_by_id(proxy_id)
    if proxy is None or proxy.owner_id != owner_id:
        await callback.message.edit_text(
            "\u274c \u041f\u0440\u043e\u043a\u0441\u0438 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e",  # ❌ Прокси не найдено
        )
        await callback.answer()
        return

    # Unlink from all accounts
    from src.db.models.account import AccountModel
    from sqlalchemy import update

    stmt = (
        update(AccountModel)
        .where(AccountModel.proxy_id == proxy_id)
        .values(proxy_id=None)
    )
    await session.execute(stmt)

    deleted = await repo.delete(proxy_id)

    if deleted:
        log.info("proxy_deleted", proxy_id=str(proxy_id), owner_id=str(owner_id))
        await callback.message.edit_text(
            "\U0001f5d1 \u041f\u0440\u043e\u043a\u0441\u0438 \u0443\u0434\u0430\u043b\u0435\u043d\u043e",  # 🗑 Прокси удалено
        )
    else:
        await callback.message.edit_text(
            "\u274c \u041f\u0440\u043e\u043a\u0441\u0438 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e",  # ❌ Прокси не найдено
        )
    await callback.answer()
