from django.db import migrations, models, transaction


def forwards(apps, schema_editor):
    Settings = apps.get_model("settings", "Settings")
    Player = apps.get_model("bd_models", "Player")
    Currency = apps.get_model("bd_models", "Currency")
    UserCurrencyBalance = apps.get_model("bd_models", "UserCurrencyBalance")

    # Check if there are any players with money > 0
    players_with_money = list(Player.objects.filter(money__gt=0).values_list("pk", "money"))
    if not players_with_money:
        # Nothing to migrate; leave Player.money intact
        return

    # Create a global currency using Settings if available, otherwise create unnamed default
    s = Settings.objects.first()
    if s and s.currency_name:
        currency = Currency.objects.create(
            name=s.currency_name,
            plural_name=s.currency_plural_name,
            symbol=s.currency_symbol,
            emoji_id=s.currency_emoji_id,
            display_before_amount=s.currency_symbol_before,
            is_default=True,
            is_active=True,
        )
    else:
        currency = Currency.objects.create(is_default=True, is_active=True)

    # Migrate Player.money -> UserCurrencyBalance for players with money
    to_create = []
    for pk, amt in players_with_money:
        if amt and amt > 0:
            to_create.append(UserCurrencyBalance(player_id=pk, currency_id=currency.pk, amount=amt))
    if to_create:
        UserCurrencyBalance.objects.bulk_create(to_create, batch_size=1000)

    # Remove the Player.money field from the table now that values are migrated
    try:
        field = Player._meta.get_field("money")
    except Exception:
        return
    schema_editor.remove_field(Player, field)


def backwards(apps, schema_editor):
    raise RuntimeError("This migration is irreversible.")


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0018_guildconfig_manual_drop_enabled"),
        ("settings", "0007_settings_currency_emoji_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="Currency",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, null=True, blank=True)),
                ("plural_name", models.CharField(max_length=64, null=True, blank=True)),
                ("symbol", models.CharField(max_length=16, null=True, blank=True)),
                ("emoji_id", models.BigIntegerField(null=True, blank=True)),
                ("display_before_amount", models.BooleanField(null=True, blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "currency"},
        ),
        migrations.CreateModel(
            name="UserCurrencyBalance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("currency", models.ForeignKey(on_delete=models.CASCADE, related_name="balances", to="bd_models.currency")),
                ("player", models.ForeignKey(on_delete=models.CASCADE, related_name="currency_balances", to="bd_models.player")),
            ],
            options={"db_table": "user_currency_balance"},
        ),
        migrations.RunPython(forwards, backwards),
    ]
