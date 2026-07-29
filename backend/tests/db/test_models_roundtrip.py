"""Round-trip and drift-guard tests for ``backend/src/db/models.py``.

Connects to the database identified by ``DATABASE_URL`` (same convention as
``test_schema.py``/``test_rls.py`` in this directory) via the SQLAlchemy
async session in ``backend/src/db/session.py``.

Run locally (from ``backend/``), with the Supabase local stack running::

    uv run --env-file ../.env pytest tests/db/test_models_roundtrip.py -v

:seealso: backend/src/core/categories.py; supabase/migrations/0001_core.sql.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core.categories import HmrcCategory
from src.db.models import Entity, Org, Property, PropertyOwnership, Transaction
from src.db.session import async_session_factory


def test_hmrc_category_enum_column_matches_python_source_of_truth() -> None:
    """``Transaction.hmrc_category``'s DB-side labels must equal ``HmrcCategory``'s values.

    Guards the SQLAlchemy-side mapping against drift from
    ``src/core/categories.py`` -- the single Python source of truth -- the
    same way ``test_schema.py``'s
    ``test_hmrc_category_enum_has_exactly_15_spec_values`` guards the SQL
    side. No database connection needed: this only inspects the mapped
    column's type metadata.
    """
    column_type = Transaction.__table__.columns["hmrc_category"].type
    assert column_type.enums == [category.value for category in HmrcCategory]


@pytest.mark.asyncio
async def test_org_entity_property_ownership_roundtrip() -> None:
    """Insert org -> entity -> property -> 100% ownership, then select it back.

    Runs in a single, never-committed transaction: everything is rolled
    back at the end (``finally``) rather than left in the database, so this
    test leaves no residual data behind -- same cleanup discipline as the
    raw-``asyncpg`` tests in ``test_schema.py``/``test_rls.py``.
    """
    session = async_session_factory()
    try:
        org = Org(name="roundtrip test org")
        session.add(org)
        await session.flush()

        entity = Entity(org_id=org.id, name="roundtrip test entity", tax_regime="mtd_itsa")
        session.add(entity)
        await session.flush()

        property_ = Property(
            org_id=org.id,
            address_line1="1 Test Street",
            city="Luton",
            postcode="LU1 1AA",
            finance_cost_classification="residential",
        )
        session.add(property_)
        await session.flush()

        ownership = PropertyOwnership(
            org_id=org.id,
            property_id=property_.id,
            entity_id=entity.id,
            ownership_percentage=Decimal("100.00"),
        )
        session.add(ownership)
        await session.flush()

        org_id, entity_id, property_id, ownership_id = org.id, entity.id, property_.id, ownership.id

        # Force a real re-read from the database rather than asserting
        # against the still-attached, already-loaded Python objects.
        session.expire_all()

        fetched_org = (await session.execute(select(Org).where(Org.id == org_id))).scalar_one()
        assert fetched_org.name == "roundtrip test org"

        fetched_entity = (
            await session.execute(select(Entity).where(Entity.id == entity_id))
        ).scalar_one()
        assert fetched_entity.org_id == org_id
        assert fetched_entity.name == "roundtrip test entity"
        assert fetched_entity.tax_regime == "mtd_itsa"

        fetched_property = (
            await session.execute(select(Property).where(Property.id == property_id))
        ).scalar_one()
        assert fetched_property.org_id == org_id
        assert fetched_property.postcode == "LU1 1AA"
        assert fetched_property.finance_cost_classification == "residential"

        fetched_ownership = (
            await session.execute(select(PropertyOwnership).where(PropertyOwnership.id == ownership_id))
        ).scalar_one()
        assert fetched_ownership.org_id == org_id
        assert fetched_ownership.property_id == property_id
        assert fetched_ownership.entity_id == entity_id
        assert fetched_ownership.ownership_percentage == Decimal("100.00")
    finally:
        await session.rollback()
        await session.close()
