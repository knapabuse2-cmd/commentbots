"""
Statistics handlers — dashboard with event counts, campaign stats, event log.

Flows:
1. Overview: total accounts, campaigns, events summary for last 24h
2. Campaign stats: per-campaign comment/fail/ban counters
3. Event log: paginated list of recent events
"""

import uuid
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards.stats import (
    campaign_stats_detail_keyboard,
    campaign_stats_list_keyboard,
    events_list_keyboard,
    stats_menu_keyboard,
)
from src.core.logging import get_logger
from src.db.models.account import AccountStatus
from src.db.models.campaign import CampaignStatus
from src.db.models.event_log import EventType
from src.db.repositories.account_repo import AccountRepository
from src.db.repositories.assignment_repo import AssignmentRepository
from src.db.repositories.campaign_repo import CampaignRepository
from src.db.repositories.event_log_repo import EventLogRepository

log = get_logger(__name__)

router = Router(name="stats")

PAGE_SIZE = 10
EVENTS_PAGE_SIZE = 20


# ============================================================
# Event type emoji + labels for display
# ============================================================

_EVENT_DISPLAY: dict[str, tuple[str, str]] = {
    # (emoji, short label)
    "comment_posted": ("\U0001f4ac", "\u041a\u043e\u043c\u043c\u0435\u043d\u0442"),                    # 💬 Коммент
    "comment_deleted": ("\U0001f5d1", "\u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435"),              # 🗑 Удаление
    "comment_reposted": ("\U0001f504", "\u0420\u0435\u043f\u043e\u0441\u0442"),                         # 🔄 Репост
    "comment_failed": ("\u274c", "\u041e\u0448\u0438\u0431\u043a\u0430"),                               # ❌ Ошибка
    "account_added": ("\u2795", "\u0410\u043a\u043a\u0430\u0443\u043d\u0442+"),                         # ➕ Аккаунт+
    "account_authorized": ("\u2705", "\u0410\u0432\u0442\u043e\u0440\u0438\u0437\u0430\u0446\u0438\u044f"),  # ✅ Авторизация
    "account_banned": ("\U0001f6ab", "\u0411\u0430\u043d"),                                             # 🚫 Бан
    "account_error": ("\U0001f534", "\u041e\u0448\u0438\u0431\u043a\u0430 \u0430\u043a\u043a"),         # 🔴 Ошибка акк
    "channel_joined": ("\U0001f4e5", "\u0412\u0441\u0442\u0443\u043f\u043b\u0435\u043d\u0438\u0435"),   # 📥 Вступление
    "channel_access_denied": ("\U0001f6ab", "\u0414\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043a\u0440\u044b\u0442"),  # 🚫 Доступ закрыт
    "channel_rotated": ("\U0001f504", "\u0420\u043e\u0442\u0430\u0446\u0438\u044f"),                    # 🔄 Ротация
    "channel_comments_disabled": ("\u26a0", "\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u044b \u043e\u0444\u0444"),  # ⚠ Комменты офф
    "profile_copied": ("\U0001f464", "\u041f\u0440\u043e\u0444\u0438\u043b\u044c"),                     # 👤 Профиль
    "profile_copy_failed": ("\u26a0", "\u041f\u0440\u043e\u0444\u0438\u043b\u044c \u043e\u0448"),       # ⚠ Профиль ош
    "campaign_started": ("\u25b6", "\u0421\u0442\u0430\u0440\u0442"),                                   # ▶ Старт
    "campaign_paused": ("\u23f8", "\u041f\u0430\u0443\u0437\u0430"),                                    # ⏸ Пауза
    "campaign_completed": ("\u2705", "\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e"),         # ✅ Завершено
    "worker_started": ("\U0001f7e2", "\u0412\u043e\u0440\u043a\u0435\u0440+"),                          # 🟢 Воркер+
    "worker_stopped": ("\U0001f534", "\u0412\u043e\u0440\u043a\u0435\u0440-"),                          # 🔴 Воркер-
    "worker_error": ("\U0001f534", "\u041e\u0448. \u0432\u043e\u0440\u043a\u0435\u0440\u0430"),         # 🔴 Ош. воркера
    "no_free_channels": ("\u26a0", "\u041d\u0435\u0442 \u043a\u0430\u043d\u0430\u043b\u043e\u0432"),    # ⚠ Нет каналов
    "flood_wait": ("\u23f3", "FloodWait"),                                                              # ⏳ FloodWait
}


def _format_event_line(event) -> str:
    """Format a single event log entry for display."""
    etype = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
    emoji, label = _EVENT_DISPLAY.get(etype, ("\u2139", etype))  # ℹ default
    time_str = event.created_at.strftime("%H:%M:%S") if event.created_at else "??:??:??"

    # Truncate message to 60 chars for readability
    msg = event.message or ""
    if len(msg) > 60:
        msg = msg[:57] + "..."

    return f"<code>{time_str}</code> {emoji} {msg}"


# ============================================================
# Menu
# ============================================================


@router.message(F.text == "\U0001f4ca \u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430")  # 📊 Статистика
async def stats_menu(message: Message, state: FSMContext) -> None:
    """Show statistics menu."""
    await state.clear()
    await message.answer(
        "\U0001f4ca <b>\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430</b>",  # 📊 Статистика
        reply_markup=stats_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "stats:menu")
async def stats_menu_cb(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to stats menu (inline button)."""
    await state.clear()
    await callback.message.edit_text(
        "\U0001f4ca <b>\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430</b>",
        reply_markup=stats_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Overview — general dashboard
# ============================================================


@router.callback_query(F.data == "stats:overview")
async def stats_overview(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show general statistics overview for last 24 hours."""
    account_repo = AccountRepository(session)
    campaign_repo = CampaignRepository(session)
    event_repo = EventLogRepository(session)

    # Count accounts by status
    total_accounts = await account_repo.count_by_owner(owner_id)
    active_accounts = await account_repo.count_by_owner(owner_id, status=AccountStatus.ACTIVE)
    banned_accounts = await account_repo.count_by_owner(owner_id, status=AccountStatus.BANNED)
    error_accounts = await account_repo.count_by_owner(owner_id, status=AccountStatus.ERROR)

    # Count campaigns by status
    total_campaigns = await campaign_repo.count_by_owner(owner_id)
    active_campaigns = await campaign_repo.count_by_owner(owner_id, status=CampaignStatus.ACTIVE)

    # Event summary for last 24h
    event_summary = await event_repo.get_stats_summary(owner_id, hours=24)

    comments_posted = event_summary.get("comment_posted", 0)
    comments_reposted = event_summary.get("comment_reposted", 0)
    comments_failed = event_summary.get("comment_failed", 0)
    bans = event_summary.get("account_banned", 0) + event_summary.get("channel_access_denied", 0)
    rotations = event_summary.get("channel_rotated", 0)
    errors = event_summary.get("worker_error", 0)
    floods = event_summary.get("flood_wait", 0)

    text = (
        "\U0001f4ca <b>\u041e\u0431\u0449\u0430\u044f \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430</b>\n\n"  # 📊 Общая статистика

        "\U0001f4f1 <b>\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b:</b>\n"  # 📱 Аккаунты:
        f"  \u0412\u0441\u0435\u0433\u043e: {total_accounts}\n"                   # Всего:
        f"  \u2705 \u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0445: {active_accounts}\n"     # ✅ Активных:
        f"  \U0001f6ab \u0417\u0430\u0431\u0430\u043d\u0435\u043d\u043d\u044b\u0445: {banned_accounts}\n"   # 🚫 Забаненных:
        f"  \U0001f534 \u0421 \u043e\u0448\u0438\u0431\u043a\u043e\u0439: {error_accounts}\n\n"  # 🔴 С ошибкой:

        "\U0001f4ac <b>\u041a\u0430\u043c\u043f\u0430\u043d\u0438\u0438:</b>\n"  # 💬 Кампании:
        f"  \u0412\u0441\u0435\u0433\u043e: {total_campaigns}\n"                  # Всего:
        f"  \u25b6 \u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0445: {active_campaigns}\n\n"   # ▶ Активных:

        "\U0001f4c8 <b>\u0417\u0430 24 \u0447\u0430\u0441\u0430:</b>\n"  # 📈 За 24 часа:
        f"  \U0001f4ac \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0435\u0432: {comments_posted}\n"  # 💬 Комментариев:
        f"  \U0001f504 \u0420\u0435\u043f\u043e\u0441\u0442\u043e\u0432: {comments_reposted}\n"  # 🔄 Репостов:
        f"  \u274c \u041e\u0448\u0438\u0431\u043e\u043a \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u043e\u0432: {comments_failed}\n"  # ❌ Ошибок комментов:
        f"  \U0001f6ab \u0411\u0430\u043d\u043e\u0432: {bans}\n"                  # 🚫 Банов:
        f"  \U0001f504 \u0420\u043e\u0442\u0430\u0446\u0438\u0439: {rotations}\n"  # 🔄 Ротаций:
        f"  \U0001f534 \u041e\u0448\u0438\u0431\u043e\u043a \u0432\u043e\u0440\u043a\u0435\u0440\u043e\u0432: {errors}\n"  # 🔴 Ошибок воркеров:
        f"  \u23f3 FloodWait: {floods}"  # ⏳
    )

    await callback.message.edit_text(
        text,
        reply_markup=stats_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Campaign stats list
# ============================================================


@router.callback_query(F.data.startswith("stats:campaigns:"))
async def stats_campaign_list(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show paginated list of campaigns for stats viewing."""
    offset = int(callback.data.split(":")[-1])
    campaign_repo = CampaignRepository(session)

    campaigns = await campaign_repo.get_by_owner(owner_id, offset=offset, limit=PAGE_SIZE)
    total = await campaign_repo.count_by_owner(owner_id)

    if not campaigns and offset == 0:
        await callback.message.edit_text(
            "\U0001f4cb \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u0439 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.",  # 📋 Кампаний пока нет.
            reply_markup=stats_menu_keyboard(),
        )
        await callback.answer()
        return

    text = (
        f"\U0001f4ac <b>\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 \u043f\u043e \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044f\u043c</b> "  # 💬 Статистика по кампаниям
        f"({offset + 1}-{min(offset + PAGE_SIZE, total)} \u0438\u0437 {total}):"  # (X-Y из Z):
    )

    await callback.message.edit_text(
        text,
        reply_markup=campaign_stats_list_keyboard(campaigns, offset, total, PAGE_SIZE),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Campaign detail stats
# ============================================================


@router.callback_query(F.data.startswith("stats:campaign:"))
async def stats_campaign_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show detailed statistics for a specific campaign."""
    # Parse: stats:campaign:<uuid>
    campaign_id = uuid.UUID(callback.data.split(":")[-1])

    campaign_repo = CampaignRepository(session)
    assignment_repo = AssignmentRepository(session)
    event_repo = EventLogRepository(session)

    campaign = await campaign_repo.get_with_details(campaign_id)
    if campaign is None:
        await callback.message.edit_text(
            "\u274c \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430",  # ❌ Кампания не найдена
        )
        await callback.answer()
        return

    # Status label
    status_labels = {
        "draft": "\u2712 \u0427\u0435\u0440\u043d\u043e\u0432\u0438\u043a",        # ✒ Черновик
        "active": "\u25b6 \u0410\u043a\u0442\u0438\u0432\u043d\u0430",              # ▶ Активна
        "paused": "\u23f8 \u041d\u0430 \u043f\u0430\u0443\u0437\u0435",             # ⏸ На паузе
        "completed": "\u2705 \u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430",  # ✅ Завершена
    }
    status_text = status_labels.get(campaign.status.value, campaign.status.value)

    # Count channels and assignments
    total_channels = len(campaign.channels) if campaign.channels else 0
    active_assignments = await assignment_repo.count_active_for_campaign(campaign_id)

    # Event summary for this campaign (24h)
    # We need a campaign-scoped summary
    events_24h = await event_repo.get_recent(
        owner_id, campaign_id=campaign_id, hours=24, limit=200
    )

    # Count by type manually
    type_counts: dict[str, int] = {}
    for e in events_24h:
        etype = e.event_type.value if hasattr(e.event_type, 'value') else str(e.event_type)
        type_counts[etype] = type_counts.get(etype, 0) + 1

    comments = type_counts.get("comment_posted", 0)
    reposts = type_counts.get("comment_reposted", 0)
    fails = type_counts.get("comment_failed", 0)
    bans = type_counts.get("channel_access_denied", 0)
    rotations = type_counts.get("channel_rotated", 0)

    # Success rate
    total_attempts = campaign.successful_comments + campaign.failed_comments
    success_rate = (
        f"{campaign.successful_comments / total_attempts * 100:.1f}%"
        if total_attempts > 0
        else "\u2014"  # —
    )

    text = (
        f"\U0001f4ca <b>{campaign.name}</b>\n\n"  # 📊

        f"\U0001f4cc \u0421\u0442\u0430\u0442\u0443\u0441: {status_text}\n"  # 📌 Статус:
        f"\U0001f4e2 \u041a\u0430\u043d\u0430\u043b\u043e\u0432: {total_channels}\n"  # 📢 Каналов:
        f"\U0001f464 \u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0432\u043e\u0440\u043a\u0435\u0440\u043e\u0432: {active_assignments}\n\n"  # 👤 Активных воркеров:

        "\U0001f4c8 <b>\u0412\u0441\u0435\u0433\u043e:</b>\n"  # 📈 Всего:
        f"  \U0001f4ac \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u043e\u0432: {campaign.total_comments}\n"  # 💬 Комментов:
        f"  \u2705 \u0423\u0441\u043f\u0435\u0448\u043d\u044b\u0445: {campaign.successful_comments}\n"  # ✅ Успешных:
        f"  \u274c \u041e\u0448\u0438\u0431\u043e\u043a: {campaign.failed_comments}\n"  # ❌ Ошибок:
        f"  \U0001f4af \u0423\u0441\u043f\u0435\u0448\u043d\u043e\u0441\u0442\u044c: {success_rate}\n\n"  # 💯 Успешность:

        "\U0001f4c5 <b>\u0417\u0430 24\u0447:</b>\n"  # 📅 За 24ч:
        f"  \U0001f4ac \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u043e\u0432: {comments}\n"  # 💬
        f"  \U0001f504 \u0420\u0435\u043f\u043e\u0441\u0442\u043e\u0432: {reposts}\n"  # 🔄
        f"  \u274c \u041e\u0448\u0438\u0431\u043e\u043a: {fails}\n"  # ❌
        f"  \U0001f6ab \u0411\u0430\u043d\u043e\u0432: {bans}\n"  # 🚫
        f"  \U0001f504 \u0420\u043e\u0442\u0430\u0446\u0438\u0439: {rotations}"  # 🔄
    )

    await callback.message.edit_text(
        text,
        reply_markup=campaign_stats_detail_keyboard(campaign_id),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Event log (global)
# ============================================================


@router.callback_query(F.data.startswith("stats:events:"))
async def stats_events(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show paginated global event log."""
    # Parse: stats:events:<offset>
    parts = callback.data.split(":")
    offset = int(parts[-1])

    event_repo = EventLogRepository(session)

    # Get events with limit+1 to check if there are more
    events = await event_repo.get_recent(
        owner_id, hours=168, limit=EVENTS_PAGE_SIZE + 1
    )

    # Apply manual offset (get_recent doesn't support offset natively)
    # For simplicity, we fetch a larger window and paginate in memory
    # For a production app with 1000+ events, we'd add offset to the repo query
    all_events = await _get_events_with_offset(event_repo, owner_id, offset, EVENTS_PAGE_SIZE)

    has_more = len(all_events) > EVENTS_PAGE_SIZE
    display_events = all_events[:EVENTS_PAGE_SIZE]

    if not display_events and offset == 0:
        await callback.message.edit_text(
            "\U0001f4c3 \u0421\u043e\u0431\u044b\u0442\u0438\u0439 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.",  # 📃 Событий пока нет.
            reply_markup=stats_menu_keyboard(),
        )
        await callback.answer()
        return

    lines = [
        "\U0001f4c3 <b>\u0416\u0443\u0440\u043d\u0430\u043b \u0441\u043e\u0431\u044b\u0442\u0438\u0439</b> "  # 📃 Журнал событий
        f"(\u0437\u0430 7 \u0434\u043d\u0435\u0439)\n",  # (за 7 дней)
    ]

    for event in display_events:
        lines.append(_format_event_line(event))

    text = "\n".join(lines)
    # Telegram message limit is 4096 chars
    if len(text) > 4000:
        text = text[:3997] + "..."

    await callback.message.edit_text(
        text,
        reply_markup=events_list_keyboard(has_more, offset, EVENTS_PAGE_SIZE),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Event log (per-campaign)
# ============================================================


@router.callback_query(F.data.startswith("stats:campaign_events:"))
async def stats_campaign_events(
    callback: CallbackQuery,
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Show paginated event log for a specific campaign."""
    # Parse: stats:campaign_events:<campaign_id>:<offset>
    parts = callback.data.split(":")
    campaign_id = uuid.UUID(parts[2])
    offset = int(parts[3])

    event_repo = EventLogRepository(session)

    all_events = await _get_events_with_offset(
        event_repo, owner_id, offset, EVENTS_PAGE_SIZE,
        campaign_id=campaign_id,
    )

    has_more = len(all_events) > EVENTS_PAGE_SIZE
    display_events = all_events[:EVENTS_PAGE_SIZE]

    if not display_events and offset == 0:
        await callback.message.edit_text(
            "\U0001f4c3 \u0421\u043e\u0431\u044b\u0442\u0438\u0439 \u043f\u043e \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438 \u043d\u0435\u0442.",  # 📃 Событий по кампании нет.
            reply_markup=campaign_stats_detail_keyboard(campaign_id),
        )
        await callback.answer()
        return

    lines = [
        "\U0001f4c3 <b>\u0421\u043e\u0431\u044b\u0442\u0438\u044f \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438</b> "  # 📃 События кампании
        f"(\u0437\u0430 7 \u0434\u043d\u0435\u0439)\n",  # (за 7 дней)
    ]

    for event in display_events:
        lines.append(_format_event_line(event))

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3997] + "..."

    await callback.message.edit_text(
        text,
        reply_markup=events_list_keyboard(
            has_more, offset, EVENTS_PAGE_SIZE, campaign_id=campaign_id
        ),
        parse_mode="HTML",
    )
    await callback.answer()


# ============================================================
# Helper
# ============================================================


async def _get_events_with_offset(
    event_repo: EventLogRepository,
    owner_id: uuid.UUID,
    offset: int,
    limit: int,
    *,
    campaign_id: uuid.UUID | None = None,
) -> list:
    """
    Get events with offset support.

    EventLogRepository.get_recent doesn't support offset natively,
    so we fetch offset+limit+1 events and slice.
    """
    total_needed = offset + limit + 1
    events = await event_repo.get_recent(
        owner_id,
        campaign_id=campaign_id,
        hours=168,  # 7 days
        limit=total_needed,
    )
    return events[offset:]
