"""
All database models — imported here for Alembic auto-detection.
"""

from src.db.models.account import AccountModel, AccountStatus
from src.db.models.assignment import AssignmentModel, AssignmentStatus
from src.db.models.campaign import CampaignModel, CampaignStatus
from src.db.models.channel import ChannelModel, ChannelStatus
from src.db.models.event_log import EventLogModel, EventType
from src.db.models.proxy import ProxyModel
from src.db.models.user import UserModel

__all__ = [
    "UserModel",
    "AccountModel",
    "AccountStatus",
    "CampaignModel",
    "CampaignStatus",
    "ChannelModel",
    "ChannelStatus",
    "AssignmentModel",
    "AssignmentStatus",
    "ProxyModel",
    "EventLogModel",
    "EventType",
]
