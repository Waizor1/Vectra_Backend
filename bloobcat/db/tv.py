from tortoise import fields, models


class TvCode(models.Model):
    id = fields.IntField(primary_key=True)
    code = fields.CharField(20)
    connect_url = fields.CharField(100)
