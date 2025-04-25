from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "active_tariffs" (
            "id" VARCHAR(5) NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL,
            "name" VARCHAR(100) NOT NULL,
            "months" INT NOT NULL,
            "price" INT NOT NULL,
            "hwid_limit" INT NOT NULL DEFAULT 1,
            CONSTRAINT "fk_active_tariffs_user" FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE CASCADE
        );
        ALTER TABLE "users" ADD "active_tariff_id" VARCHAR(5);
        ALTER TABLE "users" ADD CONSTRAINT "fk_users_active_t_active_t_d3f8c1d9" 
            FOREIGN KEY ("active_tariff_id") 
            REFERENCES "active_tariffs" ("id") 
            ON DELETE SET NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" DROP CONSTRAINT "fk_users_active_t_active_t_d3f8c1d9";
        ALTER TABLE "users" DROP COLUMN "active_tariff_id";
        DROP TABLE "active_tariffs";
    """ 