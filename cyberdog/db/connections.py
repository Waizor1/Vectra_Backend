from tortoise import fields, models


class Connections(models.Model):
    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField()
    at = fields.DateField()

    @classmethod
    async def process(cls, user_id: int, at):
        await cls.get_or_create(user_id=user_id, at=at)
