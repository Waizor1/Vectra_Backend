from tortoise import BaseDBAsyncClient

async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE \"users\" ALTER COLUMN \"referred_by\" TYPE BIGINT USING \"referred_by\"::BIGINT;
    """

async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE \"users\" ALTER COLUMN \"referred_by\" TYPE INTEGER USING \"referred_by\"::INTEGER;
    """ 