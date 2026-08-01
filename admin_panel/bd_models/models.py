# pyright: reportIncompatibleVariableOverride=false

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from typing import TYPE_CHECKING, Any, Self, cast

import discord
from discord.utils import format_dt
from django.contrib import admin
from django.core.cache import cache
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.safestring import SafeText, mark_safe
from django.utils.timezone import now

from ballsdex.core.discord import View
from ballsdex.core.image_generator.image_gen import draw_card
from settings.models import settings

from .enums import DonationPolicy, FriendPolicy, MentionPolicy, PrivacyPolicy, TradeCooldownPolicy

if TYPE_CHECKING:
    from django.db.models.fields.files import ImageFieldFile

    from ballsdex.core.bot import BallsDexBot


def transform_media(path: str) -> str:
    return path.replace("/static/uploads/", "").replace("/ballsdex/core/image_generator/src/", "default/")


def image_display(image_link: str) -> SafeText:
    return mark_safe(f'<img src="/media/{transform_media(image_link)}" width="80%" />')


balls: dict[int, Ball] = {}
regimes: dict[int, Regime] = {}
economies: dict[int, Economy] = {}
specials: dict[int, Special] = {}
groups: dict[int, BallGroup] = {}


class QuerySet[T: models.Model](models.QuerySet[T]):
    def get_or_none(self, *args: Any, **kwargs: Any) -> T | None:
        try:
            return super().get(*args, **kwargs)
        except self.model.DoesNotExist:
            return None

    async def aget_or_none(self, *args: Any, **kwargs: Any) -> T | None:
        try:
            return await super().aget(*args, **kwargs)
        except self.model.DoesNotExist:
            return None

    async def aall(self) -> list[T]:
        return [x async for x in super().all()]


if TYPE_CHECKING:

    class Manager[T: models.Model](models.Manager[T], QuerySet[T]):
        pass

else:

    class Manager[T: models.Model](models.Manager[T].from_queryset(QuerySet)):
        pass


# --- Currency and per-player balances (global-only currency list) ---
class Currency(models.Model):
    """
    Global, admin-configurable currency. No per-guild behavior — global-only.
    Name is allowed to be blank to support migration when the admin hasn't configured a name yet.
    """

    name = models.CharField(max_length=64, blank=True, null=True, help_text="Currency name (singular)")
    plural_name = models.CharField(max_length=64, blank=True, null=True, help_text="Optional plural name")
    symbol = models.CharField(max_length=16, blank=True, null=True, help_text="Unicode symbol/text (e.g. $ or 💎)")
    emoji_id = models.BigIntegerField(blank=True, null=True, help_text="Optional Discord emoji ID")
    display_before_amount = models.BooleanField(
        blank=True, null=True, help_text="If true, render symbol before amount. Null = use global default"
    )
    is_active = models.BooleanField(default=True, help_text="Soft-delete flag")
    is_default = models.BooleanField(default=False, help_text="Set for the migrated legacy currency (if any)")
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "currency"
        indexes = (models.Index(fields=("is_default",)),)

    def __str__(self) -> str:
        return self.name or f"Currency #{self.pk}"


class UserCurrencyBalance(models.Model):
    """
    Per-player, per-currency balance table. Managed but displayed inline inside Player admin.
    """

    player = models.ForeignKey("Player", on_delete=models.CASCADE, related_name="currency_balances")
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="balances")
    amount = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "user_currency_balance"
        unique_together = (("player", "currency"),)
        indexes = (models.Index(fields=("player_id",)), models.Index(fields=("currency_id",)),)

    def __str__(self) -> str:
        return f"{self.player_id} - {self.currency.name or self.currency.pk}: {self.amount}"


# ------------------------------------------------------------------


class GuildConfig(models.Model):
    guild_id = models.BigIntegerField(unique=True, help_text="Discord guild ID")
    spawn_channel = models.BigIntegerField(null=True, help_text="Discord channel ID where balls will spawn")
    enabled = models.BooleanField(help_text="Whether the bot will spawn countryballs in this guild", default=True)
    silent = models.BooleanField(
        help_text="Whether the responses of guesses get sent as ephemeral or not", default=False
    )
    manual_drop_enabled = models.BooleanField(
        help_text="Whether players are allowed to use the drop command in this server", default=True
    )
    admin_command_synced = models.BooleanField(
        help_text="True if slash admin commands are present in this server", default=False, editable=False
    )

    objects: Manager[Self] = Manager()

    def __str__(self) -> str:
        return str(self.guild_id)

    class Meta:
        managed = True
        db_table = "guildconfig"


class Player(models.Model):
    discord_id = models.BigIntegerField(unique=True, help_text="Discord user ID")
    # NOTE: player.money column removed — balances are stored in UserCurrencyBalance rows.
    donation_policy = models.SmallIntegerField(
        choices=DonationPolicy.choices,
        help_text="How you want to handle donations",
        default=DonationPolicy.ALWAYS_ACCEPT,
    )
    privacy_policy = models.SmallIntegerField(
        choices=PrivacyPolicy.choices, help_text="How you want to handle inventory privacy", default=PrivacyPolicy.DENY
    )
    mention_policy = models.SmallIntegerField(
        choices=MentionPolicy.choices, help_text="Control the bot's mentions", default=MentionPolicy.ALLOW
    )
    friend_policy = models.SmallIntegerField(
        choices=FriendPolicy.choices, help_text="Open or close your friend requests", default=FriendPolicy.ALLOW
    )
    trade_cooldown_policy = models.SmallIntegerField(
        choices=TradeCooldownPolicy.choices,
        help_text="To bypass or not the trade cooldown",
        default=TradeCooldownPolicy.COOLDOWN,
    )
    extra_data = models.JSONField(blank=True, default=dict)

    objects: Manager[Self] = Manager()

    balls: models.QuerySet[BallInstance]

    class Meta:
        managed = True
        db_table = "player"

    def __str__(self) -> str:
        return f"{'
    N{NO MOBILE PHONES} ' if self.is_blacklisted() else ''}#{self.pk} ({self.discord_id})"

    def is_blacklisted(self) -> bool:
        # this should only be used for the admin panel
        if "startbot" in sys.argv:
            return False

        blacklist = cast(
            list[int],
            cache.get_or_set(
                "blacklist", BlacklistedID.objects.all().values_list("discord_id", flat=True), timeout=300
            ),
        )
        return self.discord_id in blacklist

    async def is_friend(self, other_player: "Player") -> bool:
        return await Friendship.objects.filter(
            (Q(player1=self) & Q(player2=other_player)) | (Q(player1=other_player) & Q(player2=self))
        ).aexists()

    async def is_blocked(self, other_player: "Player") -> bool:
        return await Block.objects.filter((Q(player1=self) & Q(player2=other_player))).aexists()

    @property
    def can_be_mentioned(self) -> bool:
        return self.mention_policy == MentionPolicy.ALLOW

    # New API: require a currency argument for money operations (no default currency)
    async def add_money(self, amount: int, currency: "Currency | int | str") -> int:
        """
        Add to the player's balance on the specified currency. Currency must be provided.
        Accepts a Currency instance, a PK (int), or a currency name (str).
        """
        if amount <= 0:
            raise ValueError("Amount to add must be positive")

        # resolve currency
        if isinstance(currency, Currency):
            cur = currency
        elif isinstance(currency, int):
            cur = await Currency.objects.aget_or_none(pk=currency)
        else:
            cur = await Currency.objects.aget_or_none(name=currency)

        if cur is None:
            raise ValueError("Currency not found")

        ucb = await UserCurrencyBalance.objects.aget_or_none(player=self, currency=cur)
        if ucb is None:
            ucb = UserCurrencyBalance(player=self, currency=cur, amount=amount)
            await ucb.asave()
        else:
            ucb.amount += amount
            await ucb.asave(update_fields=("amount",))
        return ucb.amount

    async def remove_money(self, amount: int, currency: "Currency | int | str") -> None:
        """
        Remove from the player's balance on the specified currency. Currency must be provided.
        """
        if amount <= 0:
            raise ValueError("Amount to remove must be positive")

        # resolve currency
        if isinstance(currency, Currency):
            cur = currency
        elif isinstance(currency, int):
            cur = await Currency.objects.aget_or_none(pk=currency)
        else:
            cur = await Currency.objects.aget_or_none(name=currency)

        if cur is None:
            raise ValueError("Currency not found")

        ucb = await UserCurrencyBalance.objects.aget_or_none(player=self, currency=cur)
        if not ucb or ucb.amount < amount:
            raise ValueError("Not enough money")
        ucb.amount -= amount
        await ucb.asave(update_fields=("amount",))

    def can_afford(self, amount: int, currency: "Currency | int | str") -> bool:
        """
        Synchronous convenience to check if the player can afford an amount on the given currency.
        This method tries to resolve the currency synchronously; returns False if not found.
        """
        try:
            if isinstance(currency, Currency):
                cur = currency
            elif isinstance(currency, int):
                cur = Currency.objects.filter(pk=currency).first()
            else:
                cur = Currency.objects.filter(name=currency).first()
        except Exception:
            return False
        if not cur:
            return False
        row = UserCurrencyBalance.objects.filter(player=self, currency=cur).first()
        return bool(row and row.amount >= amount)


class Economy(models.Model):
    name = models.CharField(max_length=64)
    icon = models.ImageField(max_length=200, help_text="512x512 PNG image")

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy"
        verbose_name_plural = "economies"

    def __str__(self) -> str:
        return self.name


class Regime(models.Model):
    name = models.CharField(max_length=64)
    background = models.ImageField(max_length=200, help_text="1428x2000 PNG image")

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "regime"

    def __str__(self) -> str:
        return self.name

# rest of file unchanged
