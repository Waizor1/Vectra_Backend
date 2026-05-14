"""Add ``payment_purpose`` column to ``processed_payments``.

The marketing-segment resolver counts «paid» users by all ``ProcessedPayments``
rows with ``status='succeeded' AND amount_external > 0``. This conflated
subscription purchases with LTE / device top-ups, so any segment-targeted
promo (``no_purchase_yet`` / ``trial_active``) disappeared right after a
user bought traffic. Persisting the purpose explicitly lets the resolver
exclude top-ups cleanly without parsing JSON at read time.

Idempotent. Safe to re-run.

Backfill strategy (in order):
1. New rows: column added as NULL (default).
2. Rows whose ``payment_id`` matches a known balance-only top-up prefix
   are tagged immediately — no JSON parsing needed.
3. Remaining rows: parse ``provider_payload`` as jsonb inside a per-row
   ``DO`` block with ``BEGIN/EXCEPTION`` so a single malformed payload
   cannot abort the whole migration.

Legacy rows that remain ``NULL`` after backfill are treated as
«subscription» by the resolver (backward-compatible default).
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Index `idx_processed_payments_user_purpose` is created out-of-band by
    # bloobcat/__main__.py::_apply_concurrent_index_patches with
    # `CREATE INDEX CONCURRENTLY` — `processed_payments` is a hot table and
    # the migration linter (scripts/check_migration_safety.py) requires it.
    # The DO-block below stays in the migration transaction; it only writes
    # via UPDATE, which takes row locks (not the table lock CONCURRENTLY
    # avoids), so the backfill is safe to run inline.
    return """
        ALTER TABLE "processed_payments"
        ADD COLUMN IF NOT EXISTS "payment_purpose" VARCHAR(32);

        UPDATE "processed_payments"
        SET "payment_purpose" = 'lte_topup'
        WHERE "payment_purpose" IS NULL
          AND "payment_id" LIKE 'balance_lte_topup_%';

        UPDATE "processed_payments"
        SET "payment_purpose" = 'devices_topup'
        WHERE "payment_purpose" IS NULL
          AND "payment_id" LIKE 'balance_devices_topup_%';

        DO $$
        DECLARE
            rec RECORD;
            meta_lte TEXT;
            meta_dev TEXT;
        BEGIN
            FOR rec IN
                SELECT id, provider_payload
                FROM processed_payments
                WHERE payment_purpose IS NULL
                  AND provider_payload IS NOT NULL
                  AND provider_payload <> ''
            LOOP
                BEGIN
                    meta_lte := (rec.provider_payload::jsonb) -> 'metadata' ->> 'lte_topup';
                    meta_dev := (rec.provider_payload::jsonb) -> 'metadata' ->> 'devices_topup';
                EXCEPTION WHEN OTHERS THEN
                    CONTINUE;
                END;
                IF meta_lte IN ('true', 'True', '1') THEN
                    UPDATE processed_payments
                    SET payment_purpose = 'lte_topup'
                    WHERE id = rec.id;
                ELSIF meta_dev IN ('true', 'True', '1') THEN
                    UPDATE processed_payments
                    SET payment_purpose = 'devices_topup'
                    WHERE id = rec.id;
                END IF;
            END LOOP;
        END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    # Index is dropped here for completeness even though it is created by
    # the runtime hook; DROP INDEX with IF EXISTS is a no-op when missing.
    return """
        DROP INDEX IF EXISTS "idx_processed_payments_user_purpose";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "payment_purpose";
    """
