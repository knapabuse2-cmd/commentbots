"""
Bulk import service --- converts and imports multiple accounts from a ZIP archive.

Converts:
- .session SQLite files -> Telethon StringSession
- tdata folders -> Telethon StringSession (via opentele, optional)

Then delegates to AccountService.import_session() for each.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from src.core.exceptions import AccountAuthError
from src.core.logging import get_logger
from src.db.repositories.proxy_repo import ProxyRepository
from src.services.account_service import AccountService

log = get_logger(__name__)


@dataclass
class DiscoveredAccount:
    """An account found inside the ZIP archive."""

    name: str                           # Display name for progress
    type: str                           # "session" or "tdata"
    session_path: Path | None = None    # Path to .session SQLite file
    json_path: Path | None = None       # Path to paired .json metadata
    tdata_path: Path | None = None      # Path to tdata folder
    json_data: dict = field(default_factory=dict)  # Parsed JSON content


@dataclass
class ImportResult:
    """Result of importing a single account."""

    account_name: str
    success: bool
    error: str | None = None
    phone: str | None = None
    telegram_id: int | None = None


def discover_accounts(extract_dir: Path) -> list[DiscoveredAccount]:
    """
    Recursively scan extracted ZIP contents for importable accounts.

    Detection rules:
    1. .session + .json pair: files with same stem (e.g., "acc1.session" + "acc1.json")
    2. .session without .json: standalone Telethon session files
    3. tdata folder: directory named "tdata" with key_data or key_dri files inside
    """
    accounts: list[DiscoveredAccount] = []
    seen_paths: set[str] = set()

    # Find all .session files recursively
    for session_file in sorted(extract_dir.rglob("*.session")):
        path_key = str(session_file)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)

        stem = session_file.stem
        parent = session_file.parent

        acc = DiscoveredAccount(
            name=stem,
            type="session",
            session_path=session_file,
        )

        # Look for paired .json file (same directory, same stem)
        json_file = parent / f"{stem}.json"
        if json_file.exists():
            acc.json_path = json_file
            try:
                acc.json_data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                log.warning("json_parse_error", file=str(json_file), error=str(e))

        accounts.append(acc)

    # Find tdata folders
    for tdata_dir in sorted(extract_dir.rglob("tdata")):
        if not tdata_dir.is_dir():
            continue

        # Validate it looks like a real tdata directory
        has_key_files = any(
            f.name.startswith("key_") for f in tdata_dir.iterdir() if f.is_file()
        )

        if has_key_files:
            acc_name = tdata_dir.parent.name or tdata_dir.name
            accounts.append(DiscoveredAccount(
                name=f"tdata:{acc_name}",
                type="tdata",
                tdata_path=tdata_dir,
            ))

    return accounts


class BulkImportService:
    """Orchestrates bulk account import from extracted ZIP contents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.account_svc = AccountService(session)
        self.proxy_repo = ProxyRepository(session)

    async def import_single_account(
        self,
        owner_id: uuid.UUID,
        discovered: DiscoveredAccount,
    ) -> ImportResult:
        """
        Import a single discovered account.

        Steps:
        1. Convert .session SQLite or tdata to StringSession
        2. Parse proxy from JSON metadata (if present)
        3. Create or find proxy record (if proxy specified)
        4. Validate session by connecting to Telegram (via AccountService.import_session)
        5. Create account record
        6. Bind proxy if resolved
        """
        try:
            # Step 1: Convert to StringSession
            if discovered.type == "session":
                session_string = self._convert_session_file(discovered.session_path)
            elif discovered.type == "tdata":
                session_string = await self._convert_tdata(discovered.tdata_path)
            else:
                return ImportResult(
                    account_name=discovered.name,
                    success=False,
                    error=f"Unknown type: {discovered.type}",
                )

            if not session_string:
                return ImportResult(
                    account_name=discovered.name,
                    success=False,
                    error="Failed to extract session from file",
                )

            # Step 2: Parse proxy from JSON (if present)
            proxy_dict = None
            proxy_id = None
            if discovered.json_data:
                proxy_str = discovered.json_data.get("proxy")
                if proxy_str:
                    proxy_dict, proxy_id = await self._resolve_proxy(owner_id, proxy_str)

            # Step 3: Import via existing AccountService
            account = await self.account_svc.import_session(
                owner_id, session_string, proxy=proxy_dict
            )

            # Step 4: Link proxy to account (if resolved)
            if proxy_id and account:
                await self.account_svc.bind_proxy(account.id, proxy_id)

            log.info(
                "bulk_import_account_ok",
                name=discovered.name,
                phone=account.phone,
                telegram_id=account.telegram_id,
            )

            return ImportResult(
                account_name=discovered.name,
                success=True,
                phone=account.phone,
                telegram_id=account.telegram_id,
            )

        except AccountAuthError as e:
            log.warning(
                "bulk_import_account_auth_error",
                name=discovered.name,
                error=e.message,
            )
            return ImportResult(
                account_name=discovered.name,
                success=False,
                error=e.message,
            )
        except Exception as e:
            log.error(
                "bulk_import_account_error",
                name=discovered.name,
                error=str(e),
                error_type=type(e).__name__,
            )
            return ImportResult(
                account_name=discovered.name,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def _convert_session_file(self, session_path: Path) -> str | None:
        """
        Convert a Telethon .session SQLite file to a StringSession string.

        Telethon .session files are SQLite databases with a 'sessions' table:
        (dc_id INTEGER, server_address TEXT, port INTEGER, auth_key BLOB)

        We read the raw data and construct a StringSession from it.
        """
        try:
            conn = sqlite3.connect(str(session_path))
            cursor = conn.cursor()

            # Telethon session table structure
            cursor.execute(
                "SELECT dc_id, server_address, port, auth_key FROM sessions"
            )
            row = cursor.fetchone()
            conn.close()

            if row is None:
                log.warning("session_file_empty", path=str(session_path))
                return None

            dc_id, server_address, port, auth_key = row

            if not auth_key or len(auth_key) != 256:
                log.warning(
                    "session_file_invalid_key",
                    path=str(session_path),
                    key_len=len(auth_key) if auth_key else 0,
                )
                return None

            # Build StringSession from raw session data
            ss = StringSession()
            ss._dc_id = dc_id
            ss._server_address = server_address
            ss._port = port
            ss._auth_key = AuthKey(auth_key)

            return ss.save()

        except sqlite3.DatabaseError as e:
            log.error("session_file_sqlite_error", path=str(session_path), error=str(e))
            return None
        except Exception as e:
            log.error("session_file_convert_error", path=str(session_path), error=str(e))
            return None

    async def _convert_tdata(self, tdata_path: Path) -> str | None:
        """
        Convert Telegram Desktop tdata to StringSession via opentele.

        opentele is an optional dependency. If not installed, returns None.
        """
        try:
            from opentele.td import TDesktop
            from opentele.api import UseCurrentSession
        except ImportError:
            log.warning("opentele_not_installed")
            return None

        try:
            tdesk = TDesktop(str(tdata_path))

            if not tdesk.isLoaded():
                log.warning("tdata_not_loaded", path=str(tdata_path))
                return None

            if not tdesk.accounts:
                log.warning("tdata_no_accounts", path=str(tdata_path))
                return None

            # Convert first account to Telethon session
            account = tdesk.accounts[0]
            client = await account.ToTelethon(
                session=StringSession(),
                flag=UseCurrentSession,
            )

            session_string = client.session.save()
            return session_string

        except Exception as e:
            log.error("tdata_convert_error", path=str(tdata_path), error=str(e))
            return None

    async def _resolve_proxy(
        self,
        owner_id: uuid.UUID,
        proxy_string: str,
    ) -> tuple[dict | None, uuid.UUID | None]:
        """
        Parse proxy string and find or create ProxyModel.

        Format: "host:port:user:pass" or "host:port"
        Returns: (proxy_dict_for_telethon, proxy_db_id)
        """
        parts = proxy_string.strip().split(":")
        if len(parts) < 2:
            log.warning("proxy_parse_error", proxy=proxy_string, reason="need at least host:port")
            return None, None

        host = parts[0].strip()
        try:
            port = int(parts[1].strip())
        except ValueError:
            log.warning("proxy_parse_error", proxy=proxy_string, reason="invalid port")
            return None, None

        username = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
        password = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None

        # Check for existing proxy
        existing = await self.proxy_repo.find_by_address(owner_id, host, port)
        if existing:
            proxy_id = existing.id
        else:
            proxy = await self.proxy_repo.create(
                owner_id=owner_id,
                host=host,
                port=port,
                username=username,
                password=password,
            )
            proxy_id = proxy.id

        proxy_dict = {"host": host, "port": port}
        if username:
            proxy_dict["username"] = username
        if password:
            proxy_dict["password"] = password

        return proxy_dict, proxy_id
