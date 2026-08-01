from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


def format_currency(amount: int, currency=None, shortened: bool = True, bot: "BallsDexBot | None" = None):
    """Format an amount using either a Currency instance or global settings.

    Parameters
    - amount: int
    - currency: Currency model instance or None
    - shortened: bool: short form (symbol+amount) or long form (amount + name)
    - bot: optional bot for resolving emoji
    """
    if currency is not None:
        # lazy import to avoid cyclic imports
        from admin_panel.bd_models.models import Currency

        if not isinstance(currency, Currency):
            try:
                currency = Currency.objects.get(pk=int(currency))
            except Exception:
                currency = None

        if currency:
            symbol = None
            emoji = None
            if getattr(currency, "emoji_id", None):
                if bot is not None:
                    emoji_obj = bot.get_emoji(currency.emoji_id)
                    emoji = str(emoji_obj) if emoji_obj else None
            if emoji:
                if shortened:
                    return f"{emoji}{amount}"
                else:
                    if amount == 0:
                        return f"no {emoji}"
                    elif amount == 1:
                        return f"1 {emoji}"
                    else:
                        return f"{amount} {emoji}"
            symbol = currency.symbol
            if shortened:
                if currency.display_before_amount:
                    return f"{symbol or ''}{amount}"
                else:
                    return f"{amount}{symbol or ''}"
            else:
                if amount == 0:
                    return f"no {currency.name or 'currencies'}"
                elif amount == 1:
                    return f"1 {currency.name or 'currency'}"
                else:
                    return f"{amount} {currency.plural_name or (currency.name + 's' if currency.name else 'currencies')}"

    # fallback to site-wide settings behaviour
    from .models import settings

    if shortened:
        if settings.currency_symbol_before:
            return f"{settings.currency_symbol}{amount}"
        else:
            return f"{amount}{settings.currency_symbol}"
    else:
        if amount == 0:
            return f"no {settings.currency_display_name(bot)}"
        elif amount == 1:
            return f"{amount} {settings.currency_display_name(bot)}"
        else:
            return f"{amount} {settings.currency_display_plural(bot)}"
