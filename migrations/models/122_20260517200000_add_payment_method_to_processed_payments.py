"""Add ``payment_method`` column to ``processed_payments``.

Stores the concrete method (SBPQR / CRYPTO / CARD / INTERNATIONAL / ERIP)
that Platega used so analytics can split the funnel without parsing
``provider_payload`` JSON at read time. NULL means «unknown / legacy».

Index ``idx_processed_payments_payment_method_processed_at`` is created
out-of-band by ``bloobcat/__main__.py::_apply_concurrent_index_patches``
with ``CREATE INDEX CONCURRENTLY`` — ``processed_payments`` is a hot
table and the migration linter (``scripts/check_migration_safety.py``)
requires CONCURRENTLY for any new index on it (post-107 policy).

Idempotent. Safe to re-run.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "payment_method" VARCHAR(32);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    # Index is dropped here for completeness even though it is created by
    # the runtime hook; DROP INDEX with IF EXISTS is a no-op when missing.
    return """
        DROP INDEX IF EXISTS "idx_processed_payments_payment_method_processed_at";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "payment_method";
    """
