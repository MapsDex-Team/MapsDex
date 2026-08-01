from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from django.db import transaction
from django.db.models import F

from ballsdex.core.utils.utils import can_mention
from bd_models.models import Player, Trade, TradeObject, UserCurrencyBalance, Currency, TradeMoney
from settings.models import settings
from settings.utils import format_currency

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class Money(commands.GroupCog):
    """
    Currency commands
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def balance(self, interaction: discord.Interaction["BallsDexBot"], currency: app_commands.Transform[int, "CurrencyTransformer"] | None = None):
        """
        Check your balance.
        """
        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            if currency:
                await interaction.response.send_message(
                    f"You have no balance for that currency.", ephemeral=True
                )
            else:
                await interaction.response.send_message("You have no balances.", ephemeral=True)
            return

        if currency:
            # currency is an int pk from transform
            cur = await Currency.objects.aget(pk=int(currency))
            ucb = await UserCurrencyBalance.objects.aget_or_none(player=player, currency=cur)
            amount = ucb.amount if ucb else 0
            await interaction.response.send_message(
                f"You have {format_currency(amount, currency=cur, shortened=False, bot=self.bot)}.", ephemeral=True
            )
            return

        # show all balances
        balances = await UserCurrencyBalance.objects.filter(player=player, amount__gt=0).select_related("currency").all()
        if not balances:
            await interaction.response.send_message("You have no balances.", ephemeral=True)
            return
        text = "Your balances:\n"
        for b in balances:
            text += f"- {format_currency(b.amount, currency=b.currency, shortened=False, bot=self.bot)}\n"
        await interaction.response.send_message(text, ephemeral=True)

    @transaction.atomic()
    def perform_donation(self, old_player: Player, new_player: Player, amount: int, currency: Currency) -> Trade:
        # operate on UserCurrencyBalance rows atomically
        old_ucb = UserCurrencyBalance.objects.select_for_update().get(player=old_player, currency=currency)
        if old_ucb.amount < amount:
            raise RuntimeError(
                f"Player's balance changed, cannot afford donation anymore {amount=} {old_ucb.amount=}"
            )
        new_ucb, _ = UserCurrencyBalance.objects.get_or_create(player=new_player, currency=currency, defaults={"amount": 0})
        old_ucb.amount = F("amount") - amount
        new_ucb.amount = F("amount") + amount
        old_ucb.save(update_fields=("amount",))
        new_ucb.save(update_fields=("amount",))
        trade = Trade.objects.create(player1=old_player, player2=new_player)
        TradeMoney.objects.create(trade=trade, player=old_player, currency=currency, amount=amount)
        return trade

    @app_commands.command()
    async def give(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User, currency: app_commands.Transform[int, "CurrencyTransformer"], amount: int):
        """
        Give money to a player.

        Parameters
        ----------
        user: discord.User
            The player you want to give money to.
        amount: int
            The amount to give.
        """
        if amount < 1:
            await interaction.response.send_message("Amount must be strictly positive.", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("You cannot donate to bots.", ephemeral=True)
            return
        if user == interaction.user:
            await interaction.response.send_message(
                f"You cannot give {settings.currency_display_plural(self.bot)} to yourself.", ephemeral=True
            )
            return

        await interaction.response.defer()
        old_player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        cur = await Currency.objects.aget(pk=int(currency))
        ucb = await UserCurrencyBalance.objects.aget_or_none(player=old_player, currency=cur)
        if not ucb or ucb.amount < amount:
            await interaction.followup.send(
                f"You do not have enough {settings.currency_display_plural(self.bot)}.", ephemeral=True
            )
            return

        new_player, _ = await Player.objects.aget_or_create(discord_id=user.id)
        blocked = await new_player.is_blocked(old_player)
        if blocked:
            await interaction.followup.send("You cannot interact with a user that has blocked you.", ephemeral=True)
            return
        if new_player.discord_id in self.bot.blacklist:
            await interaction.followup.send("You cannot donate to a blacklisted user.", ephemeral=True)
            return

        await sync_to_async(self.perform_donation)(old_player, new_player, amount, cur)
        await interaction.followup.send(
            f"You just gave {format_currency(amount, currency=cur)} to {user.mention}!",
            allowed_mentions=await can_mention([new_player]),
        )
