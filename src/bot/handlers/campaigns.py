"""
Campaign management handlers.

Flows:
1. Create campaign: enter name → created in DRAFT
2. Set message: send text/photo → saved with entities
3. Add channels: send links (one per line) or file
4. Manage accounts: toggle accounts on/off for campaign
5. Distribute: assign channels to accounts
6. Start / Pause / Delete
"""

import os
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards.accounts import cancel_keyboard
from src.bot.keyboards.campaigns import (
    campaign_accounts_keyboard,
    campaign_detail_keyboard,
    campaign_list_keyboard,
    campaigns_menu_keyboard,
)
from src.bot.keyboards.main import main_menu_keyboard
from src.bot.states import CampaignStates
from src.core.exceptions import CampaignError, OwnershipError
from src.core.logging import get_logger
from src.db.models.account import AccountStatus
from src.db.models.assignment import AssignmentStatus
from src.db.repositories.account_repo import AccountRepository
from src.db.repositories.assignment_repo import AssignmentRepository
from src.services.campaign_service import CampaignService
from src.services.channel_service import ChannelService
from src.services.distributor import DistributorService

log = get_logger(__name__)

router = Router(name="campaigns")

PAGE_SIZE = 10


# ============================================================
# Menu
# ============================================================


@router.message(F.text == "\U0001f4ac \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u0438")  # 💬 Кампании
async def campaigns_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "\U0001f4ac <b>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044f\u043c\u0438</b>",
        reply_markup=campaigns_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "camp:menu")
async def campaigns_menu_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "\U0001f4ac <b>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044f\u043c\u0438</b>",
        reply_markup=campaigns_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Create
# ============================================================


@router.callback_query(F.data == "camp:create")
async def create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CampaignStates.waiting_name)
    await callback.message.edit_text(
        "\U0001f4dd \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438:",  # 📝 Введите название кампании:
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CampaignStates.waiting_name)
async def create_receive_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    name = message.text.strip()
    if not name or len(name) > 200:
        await message.answer(
            "\u274c \u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043e\u0442 1 \u0434\u043e 200 \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432",
            reply_markup=cancel_keyboard(),
        )
        return

    svc = CampaignService(session)
    campaign = await svc.create_campaign(owner_id, name)
    await state.clear()

    await message.answer(
        f"\u2705 \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f <b>{name}</b> \u0441\u043e\u0437\u0434\u0430\u043d\u0430!\n\n"
        "\u0422\u0435\u043f\u0435\u0440\u044c \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u0442\u0435 \u0435\u0451:",
        reply_markup=campaign_detail_keyboard(campaign.id, campaign.status.value),
        parse_mode="HTML",
    )


# ============================================================
# List
# ============================================================


@router.callback_query(F.data.startswith("camp:list:"))
async def list_campaigns(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    offset = int(callback.data.split(":")[-1])
    svc = CampaignService(session)
    campaigns = await svc.get_campaigns(owner_id, offset=offset, limit=PAGE_SIZE)
    total = await svc.count_campaigns(owner_id)

    if not campaigns and offset == 0:
        await callback.message.edit_text(
            "\U0001f4cb \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u0439 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.",
            reply_markup=campaigns_menu_keyboard(),
        )
        await callback.answer()
        return

    text = f"\U0001f4cb <b>\u041a\u0430\u043c\u043f\u0430\u043d\u0438\u0438</b> ({offset + 1}-{min(offset + PAGE_SIZE, total)} \u0438\u0437 {total}):"
    await callback.message.edit_text(
        text,
        reply_markup=campaign_list_keyboard(campaigns, offset, total, PAGE_SIZE),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Detail
# ============================================================


@router.callback_query(F.data.startswith("camp:detail:"))
async def campaign_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    campaign_id = uuid.UUID(callback.data.split(":")[-1])
    svc = CampaignService(session)

    try:
        campaign = await svc.get_campaign_details(campaign_id, owner_id=owner_id)
    except OwnershipError:
        await callback.message.edit_text("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")
        await callback.answer()
        return

    if campaign is None:
        await callback.message.edit_text("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")
        await callback.answer()
        return

    status_labels = {
        "draft": "\U0001f4dd \u0427\u0435\u0440\u043d\u043e\u0432\u0438\u043a",
        "active": "\u25b6\ufe0f \u0410\u043a\u0442\u0438\u0432\u043d\u0430",
        "paused": "\u23f8 \u041d\u0430 \u043f\u0430\u0443\u0437\u0435",
        "completed": "\u2705 \u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430",
    }

    ch_count = len(campaign.channels) if campaign.channels else 0
    acc_count = len({a.account_id for a in campaign.assignments}) if campaign.assignments else 0
    has_msg = "\u2705" if campaign.message_text else "\u274c"
    has_photo = " + \U0001f4f7" if campaign.message_photo_id else ""

    text = (
        f"\U0001f4ac <b>{campaign.name}</b>\n\n"
        f"\U0001f4ca \u0421\u0442\u0430\u0442\u0443\u0441: {status_labels.get(campaign.status.value, campaign.status.value)}\n"
        f"\U0001f4dd \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435: {has_msg}{has_photo}\n"
        f"\U0001f4fa \u041a\u0430\u043d\u0430\u043b\u043e\u0432: {ch_count}\n"
        f"\U0001f464 \u0410\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432: {acc_count}\n\n"
        f"\U0001f4c8 \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0435\u0432: {campaign.successful_comments} / {campaign.total_comments}"
    )

    await callback.message.edit_text(
        text,
        reply_markup=campaign_detail_keyboard(campaign.id, campaign.status.value),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Set message
# ============================================================


@router.callback_query(F.data.startswith("camp:msg:"))
async def set_message_start(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, owner_id: uuid.UUID,
) -> None:
    campaign_id = callback.data.split(":")[-1]

    # Verify ownership before storing in FSM
    svc = CampaignService(session)
    try:
        await svc.get_campaign(uuid.UUID(campaign_id), owner_id=owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    await state.update_data(campaign_id=campaign_id)
    await state.set_state(CampaignStates.waiting_message)
    await callback.message.edit_text(
        "\U0001f4dd \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0434\u043b\u044f \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438.\n\n"
        "\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u0442\u0441\u044f:\n"
        "\u2022 \u0422\u0435\u043a\u0441\u0442 \u0441 \u0444\u043e\u0440\u043c\u0430\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435\u043c (bold, italic, code, \u0441\u0441\u044b\u043b\u043a\u0438)\n"
        "\u2022 \u0422\u0435\u043a\u0441\u0442 + \u0444\u043e\u0442\u043e\n\n"
        "\u0424\u043e\u0440\u043c\u0430\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0431\u0443\u0434\u0435\u0442 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e 1:1.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CampaignStates.waiting_message)
async def set_message_receive(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    data = await state.get_data()
    campaign_id = uuid.UUID(data["campaign_id"])

    svc = CampaignService(session)

    # Extract text and entities
    text_content = None
    entities_list = None
    photo_id = None

    if message.photo:
        # Photo message — take caption and its entities
        photo_id = message.photo[-1].file_id  # Largest size
        text_content = message.caption or ""
        if message.caption_entities:
            entities_list = [
                {
                    "type": e.type,
                    "offset": e.offset,
                    "length": e.length,
                    "url": e.url,
                    "user": e.user.id if e.user else None,
                    "language": e.language,
                    "custom_emoji_id": e.custom_emoji_id,
                }
                for e in message.caption_entities
            ]

        # Download photo to local storage (Bot API file_id != Telethon file_id)
        photos_dir = Path("data/photos")
        photos_dir.mkdir(parents=True, exist_ok=True)
        photo_path = photos_dir / f"{campaign_id}.jpg"
        await message.bot.download(message.photo[-1], destination=photo_path)
        log.info("campaign_photo_downloaded", campaign_id=str(campaign_id), path=str(photo_path))
    elif message.text:
        text_content = message.text
        if message.entities:
            entities_list = [
                {
                    "type": e.type,
                    "offset": e.offset,
                    "length": e.length,
                    "url": e.url,
                    "user": e.user.id if e.user else None,
                    "language": e.language,
                    "custom_emoji_id": e.custom_emoji_id,
                }
                for e in message.entities
            ]
    else:
        await message.answer(
            "\u274c \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0442\u0435\u043a\u0441\u0442 \u0438\u043b\u0438 \u0444\u043e\u0442\u043e \u0441 \u043f\u043e\u0434\u043f\u0438\u0441\u044c\u044e",
            reply_markup=cancel_keyboard(),
        )
        return

    await svc.set_message(campaign_id, text_content, entities_list, photo_id, owner_id=owner_id)
    await state.clear()

    photo_label = " + \U0001f4f7 \u0444\u043e\u0442\u043e" if photo_id else ""
    fmt_label = f" + \U0001f3a8 \u0444\u043e\u0440\u043c\u0430\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435" if entities_list else ""

    await message.answer(
        f"\u2705 \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e!{photo_label}{fmt_label}",
        reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
        parse_mode="HTML",
    )


# ============================================================
# Add channels
# ============================================================


@router.callback_query(F.data.startswith("camp:add_channels:"))
async def add_channels_start(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, owner_id: uuid.UUID,
) -> None:
    campaign_id = callback.data.split(":")[-1]

    # Verify ownership before storing in FSM
    svc = CampaignService(session)
    try:
        await svc.get_campaign(uuid.UUID(campaign_id), owner_id=owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    await state.update_data(campaign_id=campaign_id)
    await state.set_state(CampaignStates.waiting_channels)
    await callback.message.edit_text(
        "\U0001f4fa \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u0441\u044b\u043b\u043a\u0438 \u043d\u0430 \u043a\u0430\u043d\u0430\u043b\u044b "
        "(\u043a\u0430\u0436\u0434\u0430\u044f \u0441 \u043d\u043e\u0432\u043e\u0439 \u0441\u0442\u0440\u043e\u043a\u0438):\n\n"
        "<i>\u041f\u0440\u0438\u043c\u0435\u0440:\n"
        "@channel1\n"
        "t.me/channel2\n"
        "https://t.me/+invite_hash</i>\n\n"
        "\u0418\u043b\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 .txt \u0444\u0430\u0439\u043b.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(CampaignStates.waiting_channels)
async def add_channels_receive(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    data = await state.get_data()
    campaign_id = uuid.UUID(data["campaign_id"])

    links_text = None

    # Text message
    if message.text:
        links_text = message.text

    # File upload
    elif message.document:
        try:
            file = await message.bot.download(message.document)
            links_text = file.read().decode("utf-8", errors="ignore")
        except Exception as e:
            await message.answer(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0447\u0442\u0435\u043d\u0438\u044f \u0444\u0430\u0439\u043b\u0430: {e}", reply_markup=cancel_keyboard())
            return

    if not links_text or not links_text.strip():
        await message.answer(
            "\u274c \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u0441\u044b\u043b\u043a\u0438 \u0438\u043b\u0438 \u0444\u0430\u0439\u043b",
            reply_markup=cancel_keyboard(),
        )
        return

    ch_svc = ChannelService(session)
    added, skipped, errors = await ch_svc.add_channels_bulk(campaign_id, links_text, owner_id=owner_id)

    await state.clear()

    result_text = f"\u2705 \u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e: <b>{added}</b>"
    if skipped:
        result_text += f"\n\u23ed \u041f\u0440\u043e\u043f\u0443\u0449\u0435\u043d\u043e (\u0434\u0443\u0431\u043b\u0438): {skipped}"
    if errors:
        err_preview = "\n".join(errors[:5])
        result_text += f"\n\u274c \u041e\u0448\u0438\u0431\u043a\u0438:\n<code>{err_preview}</code>"
        if len(errors) > 5:
            result_text += f"\n... \u0438 \u0435\u0449\u0451 {len(errors) - 5}"

    await message.answer(
        result_text,
        reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
        parse_mode="HTML",
    )


# ============================================================
# View channels
# ============================================================


@router.callback_query(F.data.startswith("camp:channels:"))
async def view_channels(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    parts = callback.data.split(":")
    campaign_id = uuid.UUID(parts[2])
    offset = int(parts[3])

    ch_svc = ChannelService(session)
    try:
        channels = await ch_svc.get_channels(campaign_id, offset=offset, limit=PAGE_SIZE, owner_id=owner_id)
        total = await ch_svc.count_channels(campaign_id, owner_id=owner_id)
    except OwnershipError:
        await callback.message.edit_text("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")
        await callback.answer()
        return

    if not channels:
        await callback.message.edit_text(
            "\U0001f4fa \u041a\u0430\u043d\u0430\u043b\u043e\u0432 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.",
            reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
        )
        await callback.answer()
        return

    lines = [f"\U0001f4fa <b>\u041a\u0430\u043d\u0430\u043b\u044b</b> ({offset + 1}-{min(offset + PAGE_SIZE, total)} \u0438\u0437 {total}):\n"]
    for ch in channels:
        status_emoji = {
            "pending": "\u23f3", "active": "\u2705",
            "no_access": "\U0001f6ab", "no_comments": "\u26a0", "error": "\u274c",
        }
        emoji = status_emoji.get(ch.status.value, "\u2753")
        lines.append(f"{emoji} {ch.display_name}")

    # Navigation
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="\u25c0", callback_data=f"camp:channels:{campaign_id}:{offset - PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="\u25b6", callback_data=f"camp:channels:{campaign_id}:{offset + PAGE_SIZE}"))

    kb_rows: list[list[InlineKeyboardButton]] = []
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="\u2b05 \u041d\u0430\u0437\u0430\u0434", callback_data=f"camp:detail:{campaign_id}")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Manage accounts
# ============================================================


@router.callback_query(F.data.startswith("camp:accounts:"))
async def manage_accounts(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    campaign_id = uuid.UUID(callback.data.split(":")[-1])

    # Verify campaign ownership
    svc = CampaignService(session)
    try:
        await svc.get_campaign(campaign_id, owner_id=owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    # Store campaign_id in FSM state so account toggle buttons don't need it
    await state.update_data(acc_campaign_id=str(campaign_id))

    # Get all ACTIVE accounts for this owner
    acc_repo = AccountRepository(session)
    all_accounts = await acc_repo.get_by_owner(owner_id, status=AccountStatus.ACTIVE, limit=100)

    # Get already assigned accounts
    assign_repo = AssignmentRepository(session)
    assignments = await assign_repo.get_active_for_campaign(campaign_id)
    assigned_ids = {a.account_id for a in assignments}

    if not all_accounts:
        await callback.message.edit_text(
            "\u274c \u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432.\n"
            "\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0434\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b.",
            reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "\U0001f464 <b>\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438</b>\n\n"
        "\u2705 = \u043f\u0440\u0438\u0432\u044f\u0437\u0430\u043d, \u2b1c = \u0441\u0432\u043e\u0431\u043e\u0434\u0435\u043d\n"
        "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u0447\u0442\u043e\u0431\u044b \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c/\u0443\u0431\u0440\u0430\u0442\u044c:",
        reply_markup=campaign_accounts_keyboard(campaign_id, all_accounts, assigned_ids),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ca:add:"))
async def add_account_to_campaign(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    data = await state.get_data()
    campaign_id = uuid.UUID(data["acc_campaign_id"])
    account_id = uuid.UUID(callback.data.split(":")[-1])

    # Verify ownership of both campaign and account
    from src.services.account_service import AccountService
    try:
        await CampaignService(session).get_campaign(campaign_id, owner_id=owner_id)
        await AccountService(session).get_account(account_id, owner_id=owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e", show_alert=True)
        return

    # Create a dummy assignment (no channel yet — will be assigned by distributor)
    ch_svc = ChannelService(session)
    channels = await ch_svc.get_channels(campaign_id, limit=1)

    if not channels:
        await callback.answer("\u274c \u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0434\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043a\u0430\u043d\u0430\u043b\u044b!", show_alert=True)
        return

    # Find a free channel for this account
    from src.db.repositories.channel_repo import ChannelRepository
    from sqlalchemy.exc import IntegrityError

    channel_repo = ChannelRepository(session)
    free = await channel_repo.get_free_channels(campaign_id, exclude_account_id=account_id)

    assign_repo = AssignmentRepository(session)

    assigned = False
    if free:
        # Try each free channel — another account may grab it between query and insert
        for ch in free:
            try:
                async with session.begin_nested():
                    await assign_repo.create(
                        campaign_id=campaign_id,
                        account_id=account_id,
                        channel_id=ch.id,
                        status=AssignmentStatus.ACTIVE,
                        state={},
                    )
                assigned = True
                break
            except IntegrityError:
                # Channel was grabbed by another account (race condition) — try next
                continue

    if not assigned:
        # No free channel — create idle assignment (will be reassigned by distributor)
        await assign_repo.create(
            campaign_id=campaign_id,
            account_id=account_id,
            channel_id=channels[0].id,
            status=AssignmentStatus.IDLE,
            state={},
        )

    await callback.answer("\u2705 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d")

    # Refresh the accounts view
    acc_repo = AccountRepository(session)
    all_accounts = await acc_repo.get_by_owner(owner_id, status=AccountStatus.ACTIVE, limit=100)
    assignments = await assign_repo.get_active_for_campaign(campaign_id)
    # Include IDLE assignments too
    idle_assignments = await assign_repo.get_by_account_and_campaign(
        account_id, campaign_id, status=AssignmentStatus.IDLE
    )
    assigned_ids = {a.account_id for a in assignments}
    for a in idle_assignments:
        assigned_ids.add(a.account_id)

    # Re-fetch all assignments for accurate display
    from src.db.models.assignment import AssignmentModel
    from sqlalchemy import select
    stmt = select(AssignmentModel).where(
        AssignmentModel.campaign_id == campaign_id,
        AssignmentModel.status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.IDLE]),
    )
    result = await session.execute(stmt)
    all_assigns = list(result.scalars().all())
    assigned_ids = {a.account_id for a in all_assigns}

    await callback.message.edit_text(
        "\U0001f464 <b>\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438</b>\n\n"
        "\u2705 = \u043f\u0440\u0438\u0432\u044f\u0437\u0430\u043d, \u2b1c = \u0441\u0432\u043e\u0431\u043e\u0434\u0435\u043d\n"
        "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u0447\u0442\u043e\u0431\u044b \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c/\u0443\u0431\u0440\u0430\u0442\u044c:",
        reply_markup=campaign_accounts_keyboard(campaign_id, all_accounts, assigned_ids),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ca:rm:"))
async def remove_account_from_campaign(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    data = await state.get_data()
    campaign_id = uuid.UUID(data["acc_campaign_id"])
    account_id = uuid.UUID(callback.data.split(":")[-1])

    # Verify ownership
    try:
        await CampaignService(session).get_campaign(campaign_id, owner_id=owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e", show_alert=True)
        return

    # Remove all assignments for this account in this campaign
    assign_repo = AssignmentRepository(session)
    assignments = await assign_repo.get_by_account_and_campaign(account_id, campaign_id)
    for a in assignments:
        await assign_repo.delete(a.id)

    await callback.answer("\U0001f5d1 \u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0443\u0431\u0440\u0430\u043d")

    # Refresh
    acc_repo = AccountRepository(session)
    all_accounts = await acc_repo.get_by_owner(owner_id, status=AccountStatus.ACTIVE, limit=100)

    from src.db.models.assignment import AssignmentModel
    from sqlalchemy import select
    stmt = select(AssignmentModel).where(
        AssignmentModel.campaign_id == campaign_id,
        AssignmentModel.status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.IDLE]),
    )
    result = await session.execute(stmt)
    all_assigns = list(result.scalars().all())
    assigned_ids = {a.account_id for a in all_assigns}

    await callback.message.edit_text(
        "\U0001f464 <b>\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438</b>\n\n"
        "\u2705 = \u043f\u0440\u0438\u0432\u044f\u0437\u0430\u043d, \u2b1c = \u0441\u0432\u043e\u0431\u043e\u0434\u0435\u043d\n"
        "\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u0447\u0442\u043e\u0431\u044b \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c/\u0443\u0431\u0440\u0430\u0442\u044c:",
        reply_markup=campaign_accounts_keyboard(campaign_id, all_accounts, assigned_ids),
        parse_mode="HTML",
    )


# ============================================================
# Profile bio
# ============================================================


@router.callback_query(F.data.startswith("camp:bio:"))
async def set_bio_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Start bio update flow — ask user for bio text."""
    campaign_id = callback.data.split(":")[-1]

    svc = CampaignService(session)
    try:
        await svc.get_campaign(uuid.UUID(campaign_id), owner_id=owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    await state.update_data(campaign_id=campaign_id)
    await state.set_state(CampaignStates.waiting_bio)
    await callback.message.edit_text(
        "\U0001f4cb <b>\u0411\u0438\u043e \u043f\u0440\u043e\u0444\u0438\u043b\u044f</b>\n\n"
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0442\u0435\u043a\u0441\u0442 \u0431\u0438\u043e, "
        "\u043a\u043e\u0442\u043e\u0440\u044b\u0439 \u0431\u0443\u0434\u0435\u0442 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d "
        "\u043d\u0430 <b>\u0432\u0441\u0435\u0445</b> \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430\u0445 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438.\n\n"
        "<i>\u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c 70 \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432 (Telegram \u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0435)</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(CampaignStates.waiting_bio)
async def set_bio_receive(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Receive bio text and apply to all campaign accounts."""
    bio_text = (message.text or "").strip()

    if not bio_text:
        await message.answer(
            "\u274c \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435",
            reply_markup=cancel_keyboard(),
        )
        return

    if len(bio_text) > 70:
        await message.answer(
            f"\u274c \u0421\u043b\u0438\u0448\u043a\u043e\u043c \u0434\u043b\u0438\u043d\u043d\u043e\u0435 \u0431\u0438\u043e ({len(bio_text)}/70).\n"
            "\u0421\u043e\u043a\u0440\u0430\u0442\u0438\u0442\u0435 \u0434\u043e 70 \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432.",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    campaign_id = uuid.UUID(data["campaign_id"])
    await state.clear()

    # Get all accounts assigned to the campaign
    svc = CampaignService(session)
    try:
        accounts = await svc.get_campaign_accounts(campaign_id, owner_id=owner_id)
    except OwnershipError:
        await message.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")
        return

    if not accounts:
        await message.answer(
            "\u274c \u0412 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438 \u043d\u0435\u0442 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432",
            reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
        )
        return

    progress_msg = await message.answer(
        f"\u23f3 \u041e\u0431\u043d\u043e\u0432\u043b\u044f\u044e \u0431\u0438\u043e \u043d\u0430 {len(accounts)} \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430\u0445..."
    )

    from src.telegram.client import create_client, decrypt_session
    from src.db.repositories.proxy_repo import ProxyRepository
    from telethon.tl.functions.account import UpdateProfileRequest

    proxy_repo = ProxyRepository(session)
    success = 0
    failed = 0
    errors: list[str] = []

    for account in accounts:
        if not account.session_data:
            failed += 1
            errors.append(f"{account.display_name}: \u043d\u0435\u0442 \u0441\u0435\u0441\u0441\u0438\u0438")
            continue

        client = None
        try:
            session_str = decrypt_session(account.session_data)

            proxy = None
            if account.proxy_id:
                proxy_model = await proxy_repo.get_by_id(account.proxy_id)
                if proxy_model:
                    proxy = {
                        "host": proxy_model.host,
                        "port": proxy_model.port,
                    }
                    if proxy_model.username:
                        proxy["username"] = proxy_model.username
                    if proxy_model.password:
                        proxy["password"] = proxy_model.password

            client = create_client(session_string=session_str, proxy=proxy)
            await client.connect()
            await client(UpdateProfileRequest(about=bio_text))
            success += 1
            log.info("account_bio_updated", account=account.display_name, bio=bio_text)

        except Exception as e:
            failed += 1
            err_msg = str(e)[:60]
            errors.append(f"{account.display_name}: {err_msg}")
            log.warning("account_bio_update_failed", account=account.display_name, error=str(e))
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    result = (
        f"\U0001f4cb <b>\u0411\u0438\u043e \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e</b>\n\n"
        f"\u0422\u0435\u043a\u0441\u0442: <i>{bio_text}</i>\n\n"
        f"\u2705 \u0423\u0441\u043f\u0435\u0448\u043d\u043e: {success}/{len(accounts)}\n"
    )
    if failed:
        result += f"\u274c \u041e\u0448\u0438\u0431\u043a\u0438: {failed}\n"
        for err in errors[:5]:
            result += f"\u2022 {err}\n"
        if len(errors) > 5:
            result += f"... \u0438 \u0435\u0449\u0451 {len(errors) - 5}\n"

    try:
        await progress_msg.edit_text(
            result,
            reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
            parse_mode="HTML",
        )
    except Exception:
        await message.answer(
            result,
            reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
            parse_mode="HTML",
        )


# ============================================================
# Distribute
# ============================================================


@router.callback_query(F.data.startswith("camp:distribute:"))
async def distribute_channels(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    campaign_id = uuid.UUID(callback.data.split(":")[-1])

    # Get assigned accounts
    assign_repo = AssignmentRepository(session)
    assignments = await assign_repo.get_active_for_campaign(campaign_id)
    account_ids = list({a.account_id for a in assignments})

    if not account_ids:
        await callback.answer(
            "\u274c \u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0434\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b!",
            show_alert=True,
        )
        return

    dist_svc = DistributorService(session)
    try:
        assigned = await dist_svc.distribute_initial(campaign_id, account_ids, owner_id=owner_id)
        stats = await dist_svc.get_distribution_stats(campaign_id, owner_id=owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    await callback.message.edit_text(
        f"\U0001f504 <b>\u0420\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u0438\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e</b>\n\n"
        f"\u2705 \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u043e: {assigned}\n"
        f"\U0001f4fa \u0412\u0441\u0435\u0433\u043e \u043a\u0430\u043d\u0430\u043b\u043e\u0432: {stats['total_channels']}\n"
        f"\U0001f464 \u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0441\u0432\u044f\u0437\u043e\u043a: {stats['active_assignments']}\n"
        f"\U0001f7e2 \u0421\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0445: {stats['free_channels']}",
        reply_markup=campaign_detail_keyboard(campaign_id, "draft"),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Start / Pause / Delete
# ============================================================


@router.callback_query(F.data.startswith("camp:start:"))
async def start_campaign(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    campaign_id = uuid.UUID(callback.data.split(":")[-1])
    svc = CampaignService(session)

    try:
        campaign = await svc.start_campaign(campaign_id, owner_id)
        await callback.message.edit_text(
            f"\u25b6 \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f <b>{campaign.name}</b> \u0437\u0430\u043f\u0443\u0449\u0435\u043d\u0430!",
            reply_markup=campaign_detail_keyboard(campaign.id, campaign.status.value),
            parse_mode="HTML",
        )
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return
    except CampaignError as e:
        await callback.answer(f"\u274c {e.message}", show_alert=True)
        return

    await callback.answer()


@router.callback_query(F.data.startswith("camp:pause:"))
async def pause_campaign(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    campaign_id = uuid.UUID(callback.data.split(":")[-1])
    svc = CampaignService(session)

    try:
        campaign = await svc.pause_campaign(campaign_id, owner_id)
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    if campaign:
        await callback.message.edit_text(
            f"\u23f8 \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f <b>{campaign.name}</b> \u043d\u0430 \u043f\u0430\u0443\u0437\u0435",
            reply_markup=campaign_detail_keyboard(campaign.id, campaign.status.value),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("camp:delete:"))
async def delete_campaign(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    campaign_id = uuid.UUID(callback.data.split(":")[-1])
    svc = CampaignService(session)

    try:
        deleted = await svc.delete_campaign(campaign_id, owner_id=owner_id)
        if deleted:
            await callback.message.edit_text("\U0001f5d1 \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0430")
        else:
            await callback.message.edit_text("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")
    except OwnershipError:
        await callback.answer("\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return
    except CampaignError as e:
        await callback.answer(f"\u274c {e.message}", show_alert=True)
        return

    await callback.answer()
