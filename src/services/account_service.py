"""
Account service — manages Telegram account lifecycle.

Handles:
- Phone authorization flow (send code → verify → 2FA)
- Session file import (.session)
- Session validation
- Account CRUD (list, pause, resume, delete)
- Proxy binding

All Telethon clients are created per-operation and disconnected after use.
No global client storage (was a bug source in old bot).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AccountAuthError, AccountBannedError
from src.core.logging import get_logger
from src.db.models.account import AccountModel, AccountStatus
from src.db.repositories.account_repo import AccountRepository
from src.db.repositories.event_log_repo import EventLogRepository
from src.db.models.event_log import EventType
from src.telegram.client import (
    create_client,
    decrypt_session,
    encrypt_session,
    send_auth_code,
    sign_in_with_2fa,
    sign_in_with_code,
    validate_session,
)

log = get_logger(__name__)


class AccountService:
    """Business logic for Telegram account management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AccountRepository(session)
        self.event_repo = EventLogRepository(session)

    # ---- Authorization Flow ----

    async def start_auth(
        self, owner_id: uuid.UUID, phone: str, proxy: dict | None = None
    ) -> tuple[uuid.UUID, str]:
        """
        Start phone authorization — sends SMS code.

        Args:
            owner_id: User who owns this account.
            phone: Phone number with country code.
            proxy: Optional SOCKS5 proxy dict.

        Returns:
            (account_id, phone_code_hash) — needed for next step.
        """
        # Check if account with this phone already exists
        existing = await self.repo.get_by_phone(phone, owner_id)
        if existing and existing.status == AccountStatus.ACTIVE:
            raise AccountAuthError("Account with this phone already exists and is active", phone=phone)

        # Create Telethon client and send code
        client = create_client(proxy=proxy)
        try:
            await client.connect()
            phone_code_hash = await send_auth_code(client, phone)
            # CRITICAL: save session with auth_key so verify_code can reuse it.
            # The auth code is bound to this specific auth_key — a new client
            # with a different auth_key will always get "code expired".
            partial_session = encrypt_session(client.session.save())
        finally:
            await client.disconnect()

        # Create or update account record
        if existing:
            existing.status = AccountStatus.AUTH_CODE
            existing.phone_code_hash = phone_code_hash
            existing.session_data = partial_session
            await self.session.flush()
            account_id = existing.id
        else:
            account = await self.repo.create(
                owner_id=owner_id,
                phone=phone,
                status=AccountStatus.AUTH_CODE,
                phone_code_hash=phone_code_hash,
                session_data=partial_session,
            )
            account_id = account.id

        log.info("auth_started", phone=phone, account_id=str(account_id))
        return account_id, phone_code_hash

    async def verify_code(
        self,
        account_id: uuid.UUID,
        code: str,
        proxy: dict | None = None,
    ) -> bool:
        """
        Verify SMS code. Returns True if fully authorized, raises if 2FA needed.

        Raises:
            AccountAuthError: With needs_2fa=True in context if 2FA is required.
        """
        account = await self.repo.get_by_id(account_id)
        if account is None:
            raise AccountAuthError("Account not found")

        # Restore the session that was used to send the auth code.
        # The auth_key must match or Telegram will reject the code.
        session_str = decrypt_session(account.session_data) if account.session_data else None
        client = create_client(session_string=session_str, proxy=proxy)
        try:
            await client.connect()

            encrypted_session = await sign_in_with_code(
                client, account.phone, code, account.phone_code_hash
            )

            # Success — save session and activate
            account.session_data = encrypted_session
            account.status = AccountStatus.ACTIVE
            account.phone_code_hash = None

            # Get account info from Telegram
            me = await client.get_me()
            account.telegram_id = me.id
            account.first_name = me.first_name
            account.last_name = me.last_name

            await self.session.flush()

            await self.event_repo.log_event(
                owner_id=account.owner_id,
                event_type=EventType.ACCOUNT_AUTHORIZED,
                message=f"Account {account.phone} authorized successfully",
                account_id=account.id,
            )

            log.info("auth_completed", phone=account.phone, telegram_id=me.id)
            return True

        except AccountAuthError as e:
            if e.context.get("needs_2fa"):
                # Need 2FA — save partial session for next step
                # The client is authenticated but not signed in yet
                partial_session = client.session.save()
                account.session_data = encrypt_session(partial_session)
                account.status = AccountStatus.AUTH_2FA
                await self.session.flush()
                raise  # Re-raise so handler knows to ask for password
            raise
        finally:
            await client.disconnect()

    async def verify_2fa(
        self,
        account_id: uuid.UUID,
        password: str,
        proxy: dict | None = None,
    ) -> bool:
        """Complete 2FA verification."""
        account = await self.repo.get_by_id(account_id)
        if account is None:
            raise AccountAuthError("Account not found")

        # Restore partial session
        session_str = decrypt_session(account.session_data)
        client = create_client(session_string=session_str, proxy=proxy)
        try:
            await client.connect()
            encrypted_session = await sign_in_with_2fa(client, password)

            account.session_data = encrypted_session
            account.status = AccountStatus.ACTIVE
            account.phone_code_hash = None

            me = await client.get_me()
            account.telegram_id = me.id
            account.first_name = me.first_name
            account.last_name = me.last_name

            await self.session.flush()

            await self.event_repo.log_event(
                owner_id=account.owner_id,
                event_type=EventType.ACCOUNT_AUTHORIZED,
                message=f"Account {account.phone} authorized (2FA)",
                account_id=account.id,
            )

            log.info("auth_2fa_completed", phone=account.phone)
            return True
        finally:
            await client.disconnect()

    # ---- Import ----

    async def import_session(
        self,
        owner_id: uuid.UUID,
        session_string: str,
        proxy: dict | None = None,
    ) -> AccountModel:
        """
        Import an account from a raw Telethon StringSession.

        Validates the session, gets account info, encrypts and saves.
        """
        encrypted = encrypt_session(session_string)
        proxy_dict = None
        if proxy:
            proxy_dict = proxy

        is_valid, error, user_info = await validate_session(encrypted, proxy_dict)
        if not is_valid:
            raise AccountAuthError(f"Invalid session: {error}")

        # Check duplicate by telegram_id
        if user_info and user_info.get("id"):
            existing = await self.repo.get_by_id(uuid.UUID(int=0))  # Dummy — we'll search differently
            # Search by telegram_id across all owner's accounts
            all_accounts = await self.repo.get_by_owner(owner_id, limit=1000)
            for acc in all_accounts:
                if acc.telegram_id == user_info["id"]:
                    raise AccountAuthError(
                        f"Account with Telegram ID {user_info['id']} already exists",
                        phone=user_info.get("phone"),
                    )

        account = await self.repo.create(
            owner_id=owner_id,
            phone=user_info.get("phone") if user_info else None,
            session_data=encrypted,
            status=AccountStatus.ACTIVE,
            telegram_id=user_info["id"] if user_info else None,
            first_name=user_info.get("first_name") if user_info else None,
            last_name=user_info.get("last_name") if user_info else None,
        )

        await self.event_repo.log_event(
            owner_id=owner_id,
            event_type=EventType.ACCOUNT_ADDED,
            message=f"Account imported: {account.display_name}",
            account_id=account.id,
        )

        log.info("account_imported", account_id=str(account.id), phone=account.phone)
        return account

    # ---- CRUD ----

    async def get_accounts(
        self,
        owner_id: uuid.UUID,
        *,
        status: AccountStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[AccountModel]:
        """Get accounts with optional filtering."""
        return await self.repo.get_by_owner(
            owner_id, status=status, offset=offset, limit=limit
        )

    async def count_accounts(
        self, owner_id: uuid.UUID, status: AccountStatus | None = None
    ) -> int:
        """Count accounts for an owner."""
        return await self.repo.count_by_owner(owner_id, status=status)

    async def pause_account(self, account_id: uuid.UUID) -> AccountModel | None:
        """Pause an active account."""
        return await self.repo.update_by_id(account_id, status=AccountStatus.PAUSED)

    async def resume_account(self, account_id: uuid.UUID) -> AccountModel | None:
        """Resume a paused account."""
        account = await self.repo.get_by_id(account_id)
        if account and account.session_data:
            return await self.repo.update_by_id(account_id, status=AccountStatus.ACTIVE)
        return None

    async def delete_account(self, account_id: uuid.UUID) -> bool:
        """Delete an account and all its assignments."""
        return await self.repo.delete(account_id)

    async def bind_proxy(
        self, account_id: uuid.UUID, proxy_id: uuid.UUID | None
    ) -> AccountModel | None:
        """Bind or unbind a proxy to an account."""
        return await self.repo.update_by_id(account_id, proxy_id=proxy_id)
