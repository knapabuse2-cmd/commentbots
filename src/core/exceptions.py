"""
Custom exception hierarchy for the application.

All exceptions inherit from CommentBotError so they can be caught uniformly.
Each layer has its own exception types for clean error handling.
"""


class CommentBotError(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str = "", **context: object) -> None:
        self.message = message
        self.context = context
        super().__init__(message)


# === Account Errors ===


class AccountError(CommentBotError):
    """Base for account-related errors."""


class AccountAuthError(AccountError):
    """Authentication failed (wrong code, 2FA, expired session)."""


class AccountBannedError(AccountError):
    """Account is banned by Telegram."""


class AccountFloodWaitError(AccountError):
    """Account hit Telegram flood wait limit."""

    def __init__(self, seconds: int, **context: object) -> None:
        self.seconds = seconds
        super().__init__(f"Flood wait: {seconds}s", **context)


# === Channel Errors ===


class ChannelError(CommentBotError):
    """Base for channel-related errors."""


class ChannelAccessDeniedError(ChannelError):
    """Account cannot access this channel (banned, kicked, muted)."""


class ChannelCommentsDisabledError(ChannelError):
    """Comments are disabled in this channel."""


class ChannelNotFoundError(ChannelError):
    """Channel does not exist or link is invalid."""


# === Campaign Errors ===


class CampaignError(CommentBotError):
    """Base for campaign-related errors."""


class CampaignNoAccountsError(CampaignError):
    """No available accounts for this campaign."""


class CampaignNoChannelsError(CampaignError):
    """No available channels for this campaign."""


# === Comment Errors ===


class CommentError(CommentBotError):
    """Base for commenting errors."""


class CommentPostFailedError(CommentError):
    """Failed to post a comment."""


class CommentDeleteFailedError(CommentError):
    """Failed to delete a comment."""


# === Database Errors ===


class DatabaseError(CommentBotError):
    """Database operation failed."""


# === Encryption Errors ===


class EncryptionError(CommentBotError):
    """Session encryption/decryption failed."""


# === Access Control Errors ===


class OwnershipError(CommentBotError):
    """Resource does not belong to the requesting user."""
