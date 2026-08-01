from typing import TYPE_CHECKING

import discord
from discord import app_commands

from ballsdex.core.utils.transformers import (
    BallTransform,
    EconomyTransform,
    RegimeTransform,
    SpecialTransform,
    # add CurrencyTransform import forward-compatible
)
from settings.models import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


# add CurrencyTransform alias at the end of this file if not present (the real implementation is in the module)
try:
    from ballsdex.core.utils.transformers import CurrencyTransform  # type: ignore
except Exception:
    CurrencyTransform = app_commands.Transform[int, int]
