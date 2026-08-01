"""Django migration 0008 - multi currency migration

This migration will:
- Add Currency.tradeable field
- Create TradeMoney model
- Backfill a legacy currency (from settings or unnamed fallback), migrate Player.money into UserCurrencyBalance
- Backfill TradeMoney entries from existing numeric trade fields
- Remove old numeric money columns from Player and Trade

Be sure to run a DB backup before running this migration in production.
"""
from django.db import migrations, models


def forward(apps, schema_editor):
    Currency = apps.get_model("bd_models", "Currency")
    Player = apps.get_model("bd_models", "Player")
    UserCurrencyBalance = apps.get_model("bd_models", "UserCurrencyBalance")
    Trade = apps.get_model("bd_models", "Trade")
    TradeMoney = apps.get_model("bd_models", "TradeMoney")

    # settings model is in another app
    Settings = apps.get_model("settings", "Settings")

    # pick or create legacy currency
    settings = Settings.objects.first()
    legacy = None
    if settings and getattr(settings, "currency_name", None):
        legacy = Currency.objects.filter(name=settings.currency_name).first()
        if not legacy:
            legacy = Currency.objects.create(
                name=settings.currency_name,
                plural_name=getattr(settings, "currency_plural_name", None),
                symbol=getattr(settings, "currency_symbol", None),
                emoji_id=getattr(settings, "currency_emoji_id", None),
                tradeable=True,
            )
    else:
        legacy = Currency.objects.first()
        if not legacy:
            legacy = Currency.objects.create(name=None, tradeable=True)

    # migrate player.money -> user_currency_balance
    for player in Player.objects.all():
        money = getattr(player, "money", None)
        if money and money > 0:
            ucb, _ = UserCurrencyBalance.objects.get_or_create(player=player, currency=legacy)
            ucb.amount = (ucb.amount or 0) + int(money)
            ucb.save()

    # migrate trades: create TradeMoney entries for numeric fields if present
    for trade in Trade.objects.all():
        p1_amount = getattr(trade, "player1_money", 0) or 0
        p2_amount = getattr(trade, "player2_money", 0) or 0
        created = False
        if p1_amount:
            TradeMoney.objects.create(trade=trade, player=trade.player1, currency=legacy, amount=int(p1_amount))
            created = True
        if p2_amount:
            TradeMoney.objects.create(trade=trade, player=trade.player2, currency=legacy, amount=int(p2_amount))
            created = True


def reverse(apps, schema_editor):
    # noop reverse; this migration is destructive and should not be reversed automatically
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0007_auto"),
        ("settings", "0007_settings_currency_emoji_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="currency",
            name="tradeable",
            field=models.BooleanField(default=True, help_text="Whether this currency can be used in trades/give"),
        ),
        migrations.CreateModel(
            name="TradeMoney",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("amount", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("currency", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, to="bd_models.currency")),
                ("player", models.ForeignKey(on_delete=models.CASCADE, to="bd_models.player")),
                ("trade", models.ForeignKey(on_delete=models.CASCADE, related_name="money", to="bd_models.trade")),
            ],
            options={"db_table": "trademoney"},
        ),
        migrations.RunPython(forward, reverse),
        # remove legacy numeric money columns
        migrations.RemoveField(model_name="player", name="money"),
        migrations.RemoveField(model_name="trade", name="player1_money"),
        migrations.RemoveField(model_name="trade", name="player2_money"),
    ]
