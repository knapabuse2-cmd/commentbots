"""
Convert aiogram message entities (stored as JSONB) to Telethon MessageEntity objects.

When user sends a formatted message through the admin bot (aiogram),
entities are extracted as dicts and stored in campaign.message_entities.
The worker needs to convert them back to Telethon objects for formatting_entities parameter.

Supported entity types:
- bold, italic, underline, strikethrough, spoiler
- code, pre (with language)
- text_link (with url)
- text_mention (with user id)
- custom_emoji (with custom_emoji_id)
"""

from telethon.tl.types import (
    InputMessageEntityMentionName,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityCustomEmoji,
    MessageEntityItalic,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
)

from src.core.logging import get_logger

log = get_logger(__name__)

# Mapping of aiogram entity type strings to Telethon entity classes
_TYPE_MAP = {
    "bold": MessageEntityBold,
    "italic": MessageEntityItalic,
    "underline": MessageEntityUnderline,
    "strikethrough": MessageEntityStrike,
    "spoiler": MessageEntitySpoiler,
    "code": MessageEntityCode,
}


def convert_entities(entities_json: list[dict] | None) -> list | None:
    """
    Convert stored entity dicts to Telethon MessageEntity objects.

    Args:
        entities_json: List of dicts from campaign.message_entities (JSONB).
            Each dict has: type, offset, length, and optional url/language/custom_emoji_id.

    Returns:
        List of Telethon MessageEntity objects, or None if no entities.
    """
    if not entities_json:
        return None

    result = []

    for entity in entities_json:
        etype = entity.get("type")
        offset = entity.get("offset", 0)
        length = entity.get("length", 0)

        if length <= 0:
            continue

        # Simple types (bold, italic, underline, strikethrough, spoiler, code)
        if etype in _TYPE_MAP:
            result.append(_TYPE_MAP[etype](offset=offset, length=length))

        # Pre-formatted code block (with optional language)
        elif etype == "pre":
            result.append(MessageEntityPre(
                offset=offset,
                length=length,
                language=entity.get("language") or "",
            ))

        # Text link (with URL)
        elif etype == "text_link":
            url = entity.get("url")
            if url:
                result.append(MessageEntityTextUrl(
                    offset=offset,
                    length=length,
                    url=url,
                ))

        # Text mention (with user ID)
        elif etype == "text_mention":
            user_id = entity.get("user")
            if user_id:
                result.append(InputMessageEntityMentionName(
                    offset=offset,
                    length=length,
                    user_id=user_id,
                ))

        # Custom emoji
        elif etype == "custom_emoji":
            custom_emoji_id = entity.get("custom_emoji_id")
            if custom_emoji_id:
                result.append(MessageEntityCustomEmoji(
                    offset=offset,
                    length=length,
                    document_id=int(custom_emoji_id),
                ))

        else:
            # Skip unsupported types (mention, hashtag, url, email, etc.)
            # These are auto-detected by Telegram, no need to send explicitly
            log.debug(
                "entity_type_skipped",
                entity_type=etype,
                offset=offset,
                length=length,
            )

    return result if result else None
