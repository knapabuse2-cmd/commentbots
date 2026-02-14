"""
FSM states for the admin bot.

Each user interaction flow has its own state group.
States are stored in memory (MemoryStorage) — fine since bot runs as single instance.
"""

from aiogram.fsm.state import State, StatesGroup


class AccountStates(StatesGroup):
    """States for account management flows."""
    waiting_phone = State()           # Waiting for phone number input
    waiting_code = State()            # Waiting for SMS verification code
    waiting_2fa = State()             # Waiting for 2FA password
    waiting_session_file = State()    # Waiting for .session file upload
    waiting_zip_file = State()        # Waiting for ZIP archive with accounts


class CampaignStates(StatesGroup):
    """States for campaign management flows."""
    waiting_name = State()            # Waiting for campaign name
    waiting_message = State()         # Waiting for campaign message (text/photo)
    waiting_channels = State()        # Waiting for channel links (text)
    waiting_channels_file = State()   # Waiting for channels file upload


class ProxyStates(StatesGroup):
    """States for proxy management flows."""
    waiting_proxy_input = State()     # Waiting for proxy in format host:port:user:pass
