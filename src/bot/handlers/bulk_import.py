"""
Bulk account import via ZIP archives.

Supports:
- .session + .json pairs (Telethon SQLite sessions with metadata)
- tdata folders (Telegram Desktop sessions, converted via opentele)

Flow: User sends ZIP -> bot extracts -> discovers accounts -> validates -> imports
"""

import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards.accounts import accounts_menu_keyboard, cancel_keyboard
from src.bot.states import AccountStates
from src.core.logging import get_logger
from src.services.bulk_import_service import (
    BulkImportService,
    ImportResult,
    discover_accounts,
)

log = get_logger(__name__)

router = Router(name="bulk_import")

MAX_ZIP_SIZE = 100 * 1024 * 1024  # 100 MB safety limit


@router.callback_query(F.data == "acc:add_zip")
async def add_zip_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Start ZIP import flow."""
    await state.set_state(AccountStates.waiting_zip_file)
    await callback.message.edit_text(
        "\U0001f4e6 <b>\u0418\u043c\u043f\u043e\u0440\u0442 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432 \u0438\u0437 ZIP</b>\n\n"
        "\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0435 \u0444\u043e\u0440\u043c\u0430\u0442\u044b:\n"
        "\u2022 <code>.session</code> + <code>.json</code> (\u0444\u0430\u0439\u043b\u044b Telethon)\n"
        "\u2022 <code>tdata</code> (\u043f\u0430\u043f\u043a\u0438 Telegram Desktop)\n\n"
        "JSON \u0444\u043e\u0440\u043c\u0430\u0442:\n"
        "<code>{\n"
        '  "phone": "+380123456789",\n'
        '  "proxy": "host:port:user:pass"\n'
        "}</code>\n\n"
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 ZIP-\u0430\u0440\u0445\u0438\u0432:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AccountStates.waiting_zip_file, F.document)
async def add_zip_receive(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Receive ZIP file, extract, discover accounts, validate, import."""
    doc = message.document

    # 1. Validate file extension
    if not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await message.answer(
            "\u274c \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0444\u0430\u0439\u043b \u0441 \u0440\u0430\u0441\u0448\u0438\u0440\u0435\u043d\u0438\u0435\u043c <code>.zip</code>",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    # 2. Validate file size
    if doc.file_size and doc.file_size > MAX_ZIP_SIZE:
        size_mb = doc.file_size // 1024 // 1024
        await message.answer(
            f"\u274c \u0424\u0430\u0439\u043b \u0441\u043b\u0438\u0448\u043a\u043e\u043c \u0431\u043e\u043b\u044c\u0448\u043e\u0439 ({size_mb} \u041c\u0411, \u043c\u0430\u043a\u0441. 100 \u041c\u0411)",
            reply_markup=cancel_keyboard(),
        )
        return

    # 3. Send initial progress message and clear state
    progress_msg = await message.answer("\u23f3 \u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0430\u0440\u0445\u0438\u0432\u0430...")
    await state.clear()

    try:
        # 4. Download ZIP
        file = await message.bot.download(doc)
        zip_bytes = file.read()

        # 5. Extract to temp dir and process
        with tempfile.TemporaryDirectory(prefix="commentbot_zip_") as tmpdir:
            tmp_path = Path(tmpdir)
            zip_file_path = tmp_path / "archive.zip"
            zip_file_path.write_bytes(zip_bytes)

            # Extract ZIP with security checks
            try:
                with zipfile.ZipFile(zip_file_path, "r") as zf:
                    # Security: check for path traversal
                    for member in zf.namelist():
                        member_path = Path(member)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            await progress_msg.edit_text(
                                "\u274c \u0410\u0440\u0445\u0438\u0432 \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 \u043d\u0435\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u044b\u0435 \u043f\u0443\u0442\u0438"
                            )
                            return
                    zf.extractall(tmp_path / "extracted")
            except zipfile.BadZipFile:
                await progress_msg.edit_text("\u274c \u041f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d\u043d\u044b\u0439 ZIP-\u0430\u0440\u0445\u0438\u0432")
                return

            extract_dir = tmp_path / "extracted"

            # 6. Discover accounts inside the extracted archive
            discovered = discover_accounts(extract_dir)

            if not discovered:
                await progress_msg.edit_text(
                    "\u274c \u0412 \u0430\u0440\u0445\u0438\u0432\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432\n\n"
                    "\u041e\u0436\u0438\u0434\u0430\u0435\u043c\u044b\u0435 \u0444\u043e\u0440\u043c\u0430\u0442\u044b:\n"
                    "\u2022 <code>.session</code> + <code>.json</code>\n"
                    "\u2022 \u041f\u0430\u043f\u043a\u0438 <code>tdata</code>",
                    parse_mode="HTML",
                )
                return

            await progress_msg.edit_text(
                f"\U0001f50d \u041d\u0430\u0439\u0434\u0435\u043d\u043e \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432: <b>{len(discovered)}</b>\n"
                "\u041d\u0430\u0447\u0438\u043d\u0430\u044e \u0438\u043c\u043f\u043e\u0440\u0442...",
                parse_mode="HTML",
            )

            # 7. Import accounts with progress tracking
            svc = BulkImportService(session)
            results: list[ImportResult] = []
            last_edit_time = 0.0

            for i, acc in enumerate(discovered, 1):
                # Use savepoint so one failure doesn't roll back others
                try:
                    async with session.begin_nested():
                        result = await svc.import_single_account(owner_id, acc)
                except Exception as e:
                    # Savepoint rolled back, create error result
                    result = ImportResult(
                        account_name=acc.name,
                        success=False,
                        error=f"DB error: {e}",
                    )

                results.append(result)

                # Throttle progress message edits (every 3 seconds min)
                now = time.monotonic()
                if now - last_edit_time >= 3 or i == len(discovered):
                    success_count = sum(1 for r in results if r.success)
                    fail_count = sum(1 for r in results if not r.success)
                    try:
                        await progress_msg.edit_text(
                            f"\u23f3 \u0418\u043c\u043f\u043e\u0440\u0442: {i}/{len(discovered)}\n"
                            f"\u2705 \u0423\u0441\u043f\u0435\u0448\u043d\u043e: {success_count}\n"
                            f"\u274c \u041e\u0448\u0438\u0431\u043e\u043a: {fail_count}",
                        )
                        last_edit_time = now
                    except Exception:
                        pass  # MessageNotModified or rate limit

            # 8. Show final summary
            success_count = sum(1 for r in results if r.success)
            fail_count = sum(1 for r in results if not r.success)

            summary = (
                f"\U0001f4e6 <b>\u0418\u043c\u043f\u043e\u0440\u0442 \u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043d</b>\n\n"
                f"\u2705 \u0418\u043c\u043f\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u043e: <b>{success_count}</b>\n"
            )

            if fail_count:
                summary += f"\u274c \u041e\u0448\u0438\u0431\u043a\u0438: <b>{fail_count}</b>\n\n"
                for r in results:
                    if not r.success:
                        summary += f"\u2022 {r.account_name}: {r.error}\n"

            if success_count:
                summary += f"\n\U0001f4f1 \u0423\u0441\u043f\u0435\u0448\u043d\u043e \u0438\u043c\u043f\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u044b:\n"
                for r in results:
                    if r.success:
                        phone_display = r.phone or "\u043d\u0435\u0442"
                        summary += f"\u2022 {r.account_name} \u2014 {phone_display}\n"

            await progress_msg.edit_text(
                summary,
                reply_markup=accounts_menu_keyboard(),
                parse_mode="HTML",
            )

            log.info(
                "bulk_import_completed",
                total=len(discovered),
                success=success_count,
                failed=fail_count,
                owner_id=str(owner_id),
            )

    except Exception as e:
        log.error("bulk_import_fatal_error", error=str(e), error_type=type(e).__name__)
        try:
            await progress_msg.edit_text(
                f"\u274c \u041a\u0440\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u043e\u0448\u0438\u0431\u043a\u0430 \u0438\u043c\u043f\u043e\u0440\u0442\u0430: {e}",
                reply_markup=accounts_menu_keyboard(),
            )
        except Exception:
            await message.answer(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0438\u043c\u043f\u043e\u0440\u0442\u0430: {e}")


@router.message(AccountStates.waiting_zip_file)
async def add_zip_wrong_input(message: Message) -> None:
    """Handle non-document messages in ZIP upload state."""
    await message.answer(
        "\u274c \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 ZIP-\u0430\u0440\u0445\u0438\u0432 \u0441 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430\u043c\u0438",
        reply_markup=cancel_keyboard(),
    )
