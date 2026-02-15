from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "family_members" DROP CONSTRAINT IF EXISTS "family_members_member_id_fkey";
        ALTER TABLE "family_members" DROP CONSTRAINT IF EXISTS "family_members_owner_id_fkey";
        ALTER TABLE "family_invites" DROP CONSTRAINT IF EXISTS "family_invites_owner_id_fkey";
        ALTER TABLE "family_devices" DROP CONSTRAINT IF EXISTS "family_devices_user_id_fkey";
        ALTER TABLE "family_audit_logs" DROP CONSTRAINT IF EXISTS "family_audit_logs_actor_id_fkey";
        ALTER TABLE "family_audit_logs" DROP CONSTRAINT IF EXISTS "family_audit_logs_owner_id_fkey";

        ALTER TABLE "family_members"
            ADD CONSTRAINT "family_members_member_id_fkey"
            FOREIGN KEY ("member_id") REFERENCES "users" ("id") ON DELETE CASCADE;
        ALTER TABLE "family_members"
            ADD CONSTRAINT "family_members_owner_id_fkey"
            FOREIGN KEY ("owner_id") REFERENCES "users" ("id") ON DELETE CASCADE;
        ALTER TABLE "family_invites"
            ADD CONSTRAINT "family_invites_owner_id_fkey"
            FOREIGN KEY ("owner_id") REFERENCES "users" ("id") ON DELETE CASCADE;
        ALTER TABLE "family_devices"
            ADD CONSTRAINT "family_devices_user_id_fkey"
            FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE CASCADE;
        ALTER TABLE "family_audit_logs"
            ADD CONSTRAINT "family_audit_logs_actor_id_fkey"
            FOREIGN KEY ("actor_id") REFERENCES "users" ("id") ON DELETE CASCADE;
        ALTER TABLE "family_audit_logs"
            ADD CONSTRAINT "family_audit_logs_owner_id_fkey"
            FOREIGN KEY ("owner_id") REFERENCES "users" ("id") ON DELETE CASCADE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "family_members" DROP CONSTRAINT IF EXISTS "family_members_member_id_fkey";
        ALTER TABLE "family_members" DROP CONSTRAINT IF EXISTS "family_members_owner_id_fkey";
        ALTER TABLE "family_invites" DROP CONSTRAINT IF EXISTS "family_invites_owner_id_fkey";
        ALTER TABLE "family_devices" DROP CONSTRAINT IF EXISTS "family_devices_user_id_fkey";
        ALTER TABLE "family_audit_logs" DROP CONSTRAINT IF EXISTS "family_audit_logs_actor_id_fkey";
        ALTER TABLE "family_audit_logs" DROP CONSTRAINT IF EXISTS "family_audit_logs_owner_id_fkey";

        ALTER TABLE "family_members"
            ADD CONSTRAINT "family_members_member_id_fkey"
            FOREIGN KEY ("member_id") REFERENCES "users" ("id") ON DELETE NO ACTION;
        ALTER TABLE "family_members"
            ADD CONSTRAINT "family_members_owner_id_fkey"
            FOREIGN KEY ("owner_id") REFERENCES "users" ("id") ON DELETE NO ACTION;
        ALTER TABLE "family_invites"
            ADD CONSTRAINT "family_invites_owner_id_fkey"
            FOREIGN KEY ("owner_id") REFERENCES "users" ("id") ON DELETE NO ACTION;
        ALTER TABLE "family_devices"
            ADD CONSTRAINT "family_devices_user_id_fkey"
            FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE NO ACTION;
        ALTER TABLE "family_audit_logs"
            ADD CONSTRAINT "family_audit_logs_actor_id_fkey"
            FOREIGN KEY ("actor_id") REFERENCES "users" ("id") ON DELETE NO ACTION;
        ALTER TABLE "family_audit_logs"
            ADD CONSTRAINT "family_audit_logs_owner_id_fkey"
            FOREIGN KEY ("owner_id") REFERENCES "users" ("id") ON DELETE NO ACTION;
    """
