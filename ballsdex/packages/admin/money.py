import logging

import discord
from asgiref.sync import sync_to_async
from discord.ext import commands
from django.db import connection

from ballsdex.core.bot import BallsDexBot
from ballsdex.core.utils import checks
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.transformers import ModelTransformer
from bd_models.models import Player, UserCurrencyBalance
from settings.models import settings

log = logging.getLogger(__name__)


# CurrencyTransformer defined inline to avoid circular imports at module import time
class CurrencyTransformer(ModelTransformer):
    name = "currency"
    column = "name"

    def __init__(self, **filters):
        super().__init__(**filters)
        from bd_models.models import Currency

        self.model = Currency

    async def get_options(self, interaction: discord.Interaction["BallsDexBot"], value: str):
        # Return up to 25 currencies matching the typed value
        qs = self.get_queryset()
        if value:
            qs = qs.filter(name__icontains=value)
        qs = qs.order_by("name")[:25]
        choices = []
        for option in await qs.aall():
            choices.append(discord.app_commands.Choice(name=self.key(option), value=option.pk))
        return choices


currency_transformer = CurrencyTransformer()


@commands.hybrid_group()
@checks.has_permissions("bd_models.view_player")
async def money(ctx: commands.Context[BallsDexBot]):
    """
    Currency management tools
    """
    await ctx.send_help(ctx.command)


@money.command()
@checks.has_permissions("bd_models.view_player")
async def balance(ctx: commands.Context[BallsDexBot], user: discord.User, currency: currency_transformer.__class__):
    """
    Show the balance of the user provided

    Parameters
    ----------
    user: discord.User
        The user you want to get information about.
    currency: Currency (autocomplete required)
        The currency to check the balance for.
    """
    player = await Player.objects.aget_or_none(discord_id=user.id)
    if not player:
        await ctx.send(f"This user does not have a {settings.bot_name} account.", ephemeral=True)
        return

    # currency param will be transformed into a Currency instance by the transformer
    cur = currency
    row = await UserCurrencyBalance.objects.aget_or_none(player=player, currency=cur)
    amount = row.amount if row else 0
    label = cur.name or f"Currency #{cur.pk}"
    await ctx.send(f"{user.mention} currently has {amount:,} {label}.", ephemeral=True)


@money.command()
@checks.has_permissions("bd_models.change_player")
async def add(ctx: commands.Context[BallsDexBot], user: discord.User, currency: currency_transformer.__class__, amount: int):
    """
    Add coins to the user provided

    Parameters
    ----------
    user: discord.User
        The user you want to add coins to.
    currency: Currency (autocomplete required)
        The currency to add.
    amount: int
        The amount of coins to add.
    """
    player = await Player.objects.aget_or_none(discord_id=user.id)
    if not player:
        await ctx.send(f"This user does not have a {settings.bot_name} account.", ephemeral=True)
        return

    if amount <= 0:
        await ctx.send("The amount must be greater than zero.", ephemeral=True)
        return

    await player.add_money(amount, currency)
    label = currency.name or f"Currency #{currency.pk}"
    await ctx.send(f"{amount:,} {label} have been added to {user.mention}.", ephemeral=True)
    log.info(f"{ctx.author} ({ctx.author.id}) added {amount:,} {label} to {user} ({user.id})", extra={"webhook": True})


@money.command()
@checks.has_permissions("bd_models.change_player")
async def remove(ctx: commands.Context[BallsDexBot], user: discord.User, currency: currency_transformer.__class__, amount: int):
    """
    Remove coins from the user provided

    Parameters
    ----------
    user: discord.User
        The user you want to remove coins from.
    currency: Currency (autocomplete required)
        The currency to remove.
    amount: int
        The amount of coins to remove.
    """
    player = await Player.objects.aget_or_none(discord_id=user.id)
    if not player:
        await ctx.send(f"This user does not have a {settings.bot_name} account.", ephemeral=True)
        return

    if amount <= 0:
        await ctx.send("The amount must be greater than zero.", ephemeral=True)
        return

    # synchronous can_afford convenience is available on Player
    if not player.can_afford(amount, currency):
        # fetch current balance to show to the admin
        row = await UserCurrencyBalance.objects.aget_or_none(player=player, currency=currency)
        current = row.amount if row else 0
        await ctx.send(f"This user does not have enough {currency.name or f'Currency #{currency.pk}'} to remove (balance={current:,}).", ephemeral=True)
        return
    await player.remove_money(amount, currency)
    await ctx.send(f"{amount:,} {currency.name or f'Currency #{currency.pk}'} have been removed from {user.mention}.", ephemeral=True)
    log.info(
        f"{ctx.author} ({ctx.author.id}) removed {amount:,} {currency.name or currency.pk} from {user} ({user.id})",
        extra={"webhook": True},
    )


@money.command()
@checks.has_permissions("bd_models.change_player")
async def set(ctx: commands.Context[BallsDexBot], user: discord.User, currency: currency_transformer.__class__, amount: int):
    """
    Set the balance of the user provided

    Parameters
    ----------
    user: discord.User
        The user you want to set the balance of.
    currency: Currency (autocomplete required)
        The currency to set.
    amount: int
        The amount of coins to set.
    """
    player = await Player.objects.aget_or_none(discord_id=user.id)
    if not player:
        await ctx.send(f"This user has does not have a {settings.bot_name} account.", ephemeral=True)
        return

    if amount < 0:
        await ctx.send("The amount must be greater than or equal to zero.", ephemeral=True)
        return

    # set via DB update for speed
    @sync_to_async
    def wrapper():
        from bd_models.models import UserCurrencyBalance

        row = UserCurrencyBalance.objects.filter(player=player, currency=currency).first()
        if row:
            row.amount = amount
            row.save(update_fields=("amount",))
        else:
            UserCurrencyBalance.objects.create(player=player, currency=currency, amount=amount)

    await wrapper()
    await ctx.send(f"{user.mention} now has {amount:,} {currency.name or f'Currency #{currency.pk}'}.", ephemeral=True)
    log.info(
        f"{ctx.author} ({ctx.author.id}) set the balance of {user} ({user.id}) to {amount:,} {currency.name or currency.pk}",
        extra={"webhook": True},
    )


@money.command()
@checks.is_superuser()
async def setdefault(
    ctx: commands.Context[BallsDexBot], currency: currency_transformer.__class__, amount: int, force: bool = False
):
    """
    Set the default amount of a currency provided to new users.

    Parameters
    ----------
    currency: Currency (autocomplete required)
        The currency to set the default for.
    amount: int
        The new default amount to set.
    force: bool
        If true, then ALL users will have their balance reset to the default for that currency!
    """
    view = ConfirmChoiceView(ctx)
    label = currency.name or f"Currency #{currency.pk}"
    msg = f"You are about to set the new default balance for {label} to {amount}.\n"
    if force:
        msg += (
            ":warning: You have chosen to reset ALL PLAYERS' balance for this currency to the new amount. "
            "This operation cannot be undone, all existing balances for this currency will be overwritten.\n"
        )
    msg += "\nDo you want to continue?"
    await ctx.send(msg, view=view)
    await view.wait()
    if not view.value:
        return

    @sync_to_async
    def wrapper():
        from bd_models.models import Currency, UserCurrencyBalance, Player

        # update the Currency default_amount
        Currency.objects.filter(pk=currency.pk).update(default_amount=int(amount))

        if force:
            # Efficiently upsert: update existing rows, bulk-create missing ones.
            existing_player_ids = set(
                UserCurrencyBalance.objects.filter(currency=currency).values_list("player_id", flat=True)
            )
            # update existing
            UserCurrencyBalance.objects.filter(currency=currency).update(amount=amount)
            # create missing
            to_create = []
            for player_id in Player.objects.exclude(pk__in=existing_player_ids).values_list("pk", flat=True):
                to_create.append(UserCurrencyBalance(player_id=player_id, currency_id=currency.pk, amount=amount))
            if to_create:
                UserCurrencyBalance.objects.bulk_create(to_create, batch_size=1000)

    await wrapper()
    await ctx.send(f"Default for {label} set to {amount}.", ephemeral=True)
