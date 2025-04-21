from fastadmin import TortoiseModelAdmin, register
from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class Tariffs(models.Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=100)
    months = fields.IntField()
    price = fields.IntField()


Tariffs_Pydantic = pydantic_model_creator(Tariffs, name="Tariffs")


@register(Tariffs)
class UsersModelAdmin(TortoiseModelAdmin):
    list_display = ("name", "months", "price")
    verbose_name = "Тарифы"
    verbose_name_plural = "Тарифы"
