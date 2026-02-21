from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "partner_withdrawals"
            ADD COLUMN IF NOT EXISTS "paid_amount_rub" INT;

        UPDATE "partner_withdrawals"
        SET "status" = CASE
                WHEN LOWER(COALESCE("status", "")) IN ('paid', 'success') THEN 'paid'
                ELSE 'created'
            END,
            "paid_amount_rub" = CASE
                WHEN LOWER(COALESCE("status", "")) IN ('paid', 'success')
                    THEN COALESCE("paid_amount_rub", "amount_rub")
                ELSE "paid_amount_rub"
            END;

        ALTER TABLE "partner_withdrawals"
            DROP CONSTRAINT IF EXISTS "chk_partner_withdrawals_status_created_paid";
        ALTER TABLE "partner_withdrawals"
            ADD CONSTRAINT "chk_partner_withdrawals_status_created_paid"
            CHECK ("status" IN ('created', 'paid'));

        ALTER TABLE "partner_withdrawals"
            DROP CONSTRAINT IF EXISTS "chk_partner_withdrawals_paid_amount_non_negative";
        ALTER TABLE "partner_withdrawals"
            ADD CONSTRAINT "chk_partner_withdrawals_paid_amount_non_negative"
            CHECK ("paid_amount_rub" IS NULL OR "paid_amount_rub" >= 0);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "partner_withdrawals"
            DROP CONSTRAINT IF EXISTS "chk_partner_withdrawals_paid_amount_non_negative";
        ALTER TABLE "partner_withdrawals"
            DROP CONSTRAINT IF EXISTS "chk_partner_withdrawals_status_created_paid";

        UPDATE "partner_withdrawals"
        SET "status" = CASE
            WHEN "status" = 'paid' THEN 'success'
            ELSE 'created'
        END;

        ALTER TABLE "partner_withdrawals"
            DROP COLUMN IF EXISTS "paid_amount_rub";
    """
