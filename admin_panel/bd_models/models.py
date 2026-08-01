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
    money = models.PositiveBigIntegerField(help_text="Money posessed by the player", db_default=0)
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

    async def add_money(self, amount: int) -> int:
        if amount <= 0:
            raise ValueError("Amount to add must be positive")
        self.money += amount
        await self.asave(update_fields=("money",))
        return self.money

    async def remove_money(self, amount: int) -> None:
        if self.money < amount:
            raise ValueError("Not enough money")
        self.money -= amount
        await self.asave(update_fields=("money",))

    def can_afford(self, amount: int) -> bool:
        return self.money >= amount


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


class EnabledManager[T: models.Model](Manager[T]):
    def get_queryset(self) -> models.QuerySet[T]:
        return super().get_queryset().filter(enabled=True)


class SpecialEnabledManager(Manager["Special"]):
    def get_queryset(self) -> models.QuerySet[Special]:
        return super().get_queryset().filter(hidden=False)


class BaseBallInstanceManager[T: models.Model](Manager[T]):
    def with_stats(self):
        return self.annotate(
            attack=Cast(
                models.ExpressionWrapper(
                    F("ball__attack")
                    * (models.Value(1.0) + Cast(F("attack_bonus"), models.FloatField()) / models.Value(100.0)),
                    output_field=models.FloatField(),
                ),
                models.BigIntegerField(),
            ),
            health=Cast(
                models.ExpressionWrapper(
                    F("ball__health")
                    * (models.Value(1.0) + Cast(F("health_bonus"), models.FloatField()) / models.Value(100.0)),
                    output_field=models.FloatField(),
                ),
                models.BigIntegerField(),
            ),
        )


class BallInstanceManager[T: models.Model](BaseBallInstanceManager[T]):
    def get_queryset(self) -> models.QuerySet[T]:
        return super().get_queryset().filter(deleted=False)


class TradeableManager[T: models.Model](BallInstanceManager[T]):
    def get_queryset(self) -> models.QuerySet[T]:
        return super().get_queryset().filter(tradeable=True)


class Special(models.Model):
    name = models.CharField(max_length=64)
    catch_phrase = models.CharField(
        max_length=128, blank=True, null=True, help_text="Sentence sent in bonus when someone catches a special card"
    )
    start_date = models.DateTimeField(
        blank=True, null=True, help_text="Start time of the event. If blank, starts immediately"
    )
    end_date = models.DateTimeField(
        blank=True, null=True, help_text="End time of the event. If blank, the event is permanent"
    )
    rarity = models.FloatField(help_text="Value between 0 and 1, chances of using this special background.")
    emoji = models.CharField(max_length=20, blank=True, null=True, help_text="A unicode character")
    background = models.ImageField(max_length=200, blank=True, null=True, help_text="1428x2000 PNG image")
    tradeable = models.BooleanField(help_text="Whether balls of this event can be traded", default=True)
    hidden = models.BooleanField(help_text="Hides the event from user commands", default=False)
    credits = models.CharField(max_length=64, help_text="Author of the special event artwork", null=True)

    objects: Manager[Self] = Manager()
    enabled_objects = SpecialEnabledManager()

    class Meta:
        managed = True
        db_table = "special"

    def __str__(self) -> str:
        return self.name


class Ball(models.Model):
    country = models.CharField(unique=True, max_length=48, verbose_name="Name")
    health = models.IntegerField(help_text="Ball health stat")
    attack = models.IntegerField(help_text="Ball attack stat")
    rarity = models.FloatField(help_text="Rarity of this ball")
    emoji_id = models.BigIntegerField(help_text="Emoji ID for this ball")
    wild_card = models.ImageField(max_length=200, help_text="Image used when a new ball spawns in the wild")
    collection_card = models.ImageField(max_length=200, help_text="Image used when displaying balls")
    credits = models.CharField(max_length=64, help_text="Author of the collection artwork")
    capacity_name = models.CharField(max_length=64, help_text="Name of the countryball's capacity")
    capacity_description = models.CharField(max_length=256, help_text="Description of the countryball's capacity")
    capacity_logic = models.JSONField(help_text="Effect of this capacity", blank=True, default=dict)
    enabled = models.BooleanField(help_text="Enables spawning and show in completion", default=True)
    short_name = models.CharField(
        max_length=24,
        blank=True,
        null=True,
        help_text="An alternative shorter name used only when generating the card, if the base name is too long.",
    )
    catch_names = models.TextField(
        blank=True, null=True, help_text="Additional possible names for catching this ball, separated by semicolons"
    )
    tradeable = models.BooleanField(help_text="Whether this ball can be traded with others", default=True)
    economy = models.ForeignKey(
        Economy, on_delete=models.SET_NULL, blank=True, null=True, help_text="Economical regime of this country"
    )
    economy_id: int | None
    regime = models.ForeignKey(Regime, on_delete=models.CASCADE, help_text="Political regime of this country")
    regime_id: int
    created_at = models.DateTimeField(blank=True, null=True, auto_now_add=True, editable=False)
    translations = models.TextField(blank=True, null=True)

    objects: Manager[Self] = Manager()
    enabled_objects: EnabledManager[Self] = EnabledManager()
    tradeable_objects: TradeableManager[Self] = TradeableManager()

    class Meta:
        managed = True
        db_table = "ball"
        # these are set at startup when settings are read
        # verbose_name = settings.collectible_name
        # verbose_name_plural = settings.plural_collectible_name

    @property
    def cached_regime(self) -> Regime:
        return regimes.get(self.regime_id) or self.regime

    @property
    def cached_economy(self) -> Economy | None:
        return economies.get(self.economy_id) or self.economy if self.economy_id else None

    def __str__(self) -> str:
        return self.country

    @admin.display(description="Current collection card")
    def collection_image(self) -> SafeText:
        return image_display(str(self.collection_card))

    @admin.display(description="Current spawn asset")
    def spawn_image(self) -> SafeText:
        return image_display(str(self.wild_card))

    def save(self, **kwargs) -> None:
        def lower_catch_names(names: str | None) -> str | None:
            if names:
                return ";".join([x.strip() for x in names.split(";")]).lower()

        self.catch_names = lower_catch_names(self.catch_names)
        self.translations = lower_catch_names(self.translations)

        return super().save(**kwargs)

    
