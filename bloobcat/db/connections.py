from tortoise import fields, models
from tortoise.exceptions import IntegrityError


class Connections(models.Model):
    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField()
    at = fields.DateField()

    class Meta:
        unique_together = (("user_id", "at"),)

    @classmethod
    async def process(cls, user_id: int, at):
        try:
            await cls.get_or_create(user_id=user_id, at=at)
        except IntegrityError:
            # Concurrent updaters can race on (user_id, at); treat as already created.
            await cls.get(user_id=user_id, at=at)
