"""
All repositories — one-stop import.
"""

from src.db.repositories.account_repo import AccountRepository
from src.db.repositories.assignment_repo import AssignmentRepository
from src.db.repositories.campaign_repo import CampaignRepository
from src.db.repositories.channel_repo import ChannelRepository
from src.db.repositories.event_log_repo import EventLogRepository
from src.db.repositories.proxy_repo import ProxyRepository
from src.db.repositories.user_repo import UserRepository

__all__ = [
    "UserRepository",
    "AccountRepository",
    "CampaignRepository",
    "ChannelRepository",
    "AssignmentRepository",
    "ProxyRepository",
    "EventLogRepository",
]
