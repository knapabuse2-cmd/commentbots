"""
Base repository with common CRUD operations.

All entity-specific repositories inherit from this.
Provides: get_by_id, get_all, create, create_many, update, delete.
Pagination built-in for large datasets (300+ channels).
"""

import uuid
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Base CRUD repository for any SQLAlchemy model."""

    def __init__(self, session: AsyncSession, model_class: type[ModelT]) -> None:
        self.session = session
        self.model_class = model_class

    async def get_by_id(self, id: uuid.UUID) -> ModelT | None:
        """Get a single record by primary key."""
        return await self.session.get(self.model_class, id)

    async def get_all(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        order_by: Any = None,
    ) -> list[ModelT]:
        """
        Get all records with pagination.

        Args:
            offset: Skip first N records.
            limit: Return at most N records (default 100, max 1000).
            order_by: SQLAlchemy column or expression to order by.
        """
        limit = min(limit, 1000)  # Safety cap
        stmt = select(self.model_class).offset(offset).limit(limit)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self) -> int:
        """Get total number of records."""
        stmt = select(func.count()).select_from(self.model_class)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def create(self, **kwargs: Any) -> ModelT:
        """Create a single record."""
        instance = self.model_class(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def create_many(self, items: list[dict[str, Any]]) -> list[ModelT]:
        """
        Bulk create records. Efficient for importing 300+ channels at once.

        Args:
            items: List of dicts with column values.

        Returns:
            List of created model instances.
        """
        instances = [self.model_class(**item) for item in items]
        self.session.add_all(instances)
        await self.session.flush()
        return instances

    async def update_by_id(self, id: uuid.UUID, **kwargs: Any) -> ModelT | None:
        """Update a single record by ID. Returns updated instance or None."""
        instance = await self.get_by_id(id)
        if instance is None:
            return None
        for key, value in kwargs.items():
            setattr(instance, key, value)
        await self.session.flush()
        return instance

    async def bulk_update(self, ids: list[uuid.UUID], **kwargs: Any) -> int:
        """
        Bulk update multiple records by IDs. Returns count of updated rows.

        Efficient for mass operations (e.g., pause all accounts).
        """
        stmt = (
            update(self.model_class)
            .where(self.model_class.id.in_(ids))
            .values(**kwargs)
        )
        result = await self.session.execute(stmt)
        return result.rowcount

    async def delete(self, id: uuid.UUID) -> bool:
        """Delete a record by ID. Returns True if deleted, False if not found."""
        instance = await self.get_by_id(id)
        if instance is None:
            return False
        await self.session.delete(instance)
        await self.session.flush()
        return True
