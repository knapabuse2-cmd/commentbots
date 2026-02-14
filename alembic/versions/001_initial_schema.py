"""Initial schema — all tables for CommentBot v2.0

Revision ID: 001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Enums --
    account_status = sa.Enum(
        "pending", "auth_code", "auth_2fa", "active", "paused", "banned", "error",
        name="account_status",
    )
    campaign_status = sa.Enum(
        "draft", "active", "paused", "completed",
        name="campaign_status",
    )
    channel_status = sa.Enum(
        "pending", "active", "no_access", "no_comments", "error",
        name="channel_status",
    )
    assignment_status = sa.Enum(
        "active", "blocked", "completed", "idle",
        name="assignment_status",
    )
    event_type = sa.Enum(
        "comment_posted", "comment_deleted", "comment_reposted", "comment_failed",
        "account_added", "account_authorized", "account_banned", "account_error",
        "channel_joined", "channel_access_denied", "channel_rotated", "channel_comments_disabled",
        "profile_copied", "profile_copy_failed",
        "campaign_started", "campaign_paused", "campaign_completed",
        "worker_started", "worker_stopped", "worker_error",
        "no_free_channels", "flood_wait",
        name="event_type",
    )

    # -- Users --
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger, unique=True, nullable=False, index=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("notification_prefs", JSONB, nullable=False,
                   server_default='{"comments": true, "bans": true, "errors": true, "rotations": true}'),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # -- Proxies --
    op.create_table(
        "proxies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("password", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # -- Accounts --
    op.create_table(
        "accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("session_data", sa.Text, nullable=True),
        sa.Column("tdata_path", sa.String(500), nullable=True),
        sa.Column("status", account_status, nullable=False, default="pending", index=True),
        sa.Column("phone_code_hash", sa.String(255), nullable=True),
        sa.Column("proxy_id", UUID(as_uuid=True), sa.ForeignKey("proxies.id", ondelete="SET NULL"),
                   nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("last_name", sa.String(255), nullable=True),
        sa.Column("telegram_id", sa.BigInteger, unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # -- Campaigns --
    op.create_table(
        "campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", campaign_status, nullable=False, default="draft", index=True),
        sa.Column("message_text", sa.Text, nullable=True),
        sa.Column("message_entities", JSONB, nullable=True),
        sa.Column("message_photo_id", sa.String(500), nullable=True),
        sa.Column("total_comments", sa.Integer, default=0, nullable=False),
        sa.Column("successful_comments", sa.Integer, default=0, nullable=False),
        sa.Column("failed_comments", sa.Integer, default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # -- Channels --
    op.create_table(
        "channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("link", sa.String(500), nullable=False),
        sa.Column("username", sa.String(255), nullable=True, index=True),
        sa.Column("invite_hash", sa.String(255), nullable=True),
        sa.Column("telegram_id", sa.BigInteger, nullable=True),
        sa.Column("status", channel_status, nullable=False, default="pending", index=True),
        sa.Column("comments_posted", sa.Integer, default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # -- Assignments --
    op.create_table(
        "assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("channel_id", UUID(as_uuid=True), sa.ForeignKey("channels.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("status", assignment_status, nullable=False, default="active", index=True),
        sa.Column("fail_count", sa.Integer, default=0, nullable=False),
        sa.Column("state", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Partial unique index: one active assignment per channel
    op.create_index(
        "ix_one_active_per_channel",
        "assignments",
        ["channel_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # -- Event Log --
    op.create_table(
        "event_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                   nullable=False, index=True),
        sa.Column("event_type", event_type, nullable=False, index=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("campaign_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("account_id", UUID(as_uuid=True), nullable=True),
        sa.Column("channel_id", UUID(as_uuid=True), nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                   nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("event_log")
    op.drop_index("ix_one_active_per_channel", table_name="assignments")
    op.drop_table("assignments")
    op.drop_table("channels")
    op.drop_table("campaigns")
    op.drop_table("accounts")
    op.drop_table("proxies")
    op.drop_table("users")

    # Drop enums
    sa.Enum(name="event_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="assignment_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="channel_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="campaign_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="account_status").drop(op.get_bind(), checkfirst=True)
