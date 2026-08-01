async def balls_transferinv(
    ctx: commands.Context[BallsDexBot], source: discord.User, dest: discord.User, currency: bool = False
):
    """
    Transfer the full inventory of a user to another.

    Parameters
    ----------
    source: discord.User
        The user whose inventory you want to transfer
    dest: discord.User
        The user who should receive the inventory
    currency: bool
        Whether the player's balance should also be transferred.
    """
    if source == dest:
        await ctx.send("You specified the same source and destination.", ephemeral=True)
        return
    try:
        source_player = await Player.objects.aget(discord_id=source.id)
    except Player.DoesNotExist:
        await ctx.send(f"User {source} does not have a player profile.", ephemeral=True)
        return
    qs = BallInstance.objects.filter(player=source_player)
    balls_count = await qs.acount()
    if balls_count == 0 and (not currency or await UserCurrencyBalance.objects.filter(player=source_player).acount() == 0):
        await ctx.send(f"{source}'s inventory is empty.", ephemeral=True)
        return

    view = ConfirmChoiceView(ctx, accept_message="Confirmed, transferring...", cancel_message="Request cancelled.")
    if currency:
        # compute total per-currency summary
        balances = await UserCurrencyBalance.objects.filter(player=source_player, amount__gt=0).select_related("currency").all()
        money_txt = ", ".join(f"{b.amount} {b.currency.name or b.currency.pk}" for b in balances)
        text = (
            f"Are you sure you want to transfer {balls_count} {settings.plural_collectible_name} and "
            f"{money_txt} from {source} to {dest}?"
        )
    else:
        text = (
            f"Are you sure you want to transfer {balls_count} {settings.plural_collectible_name} "
            f"from {source} to {dest}?"
        )
    await ctx.send(text, view=view, ephemeral=True)
    await view.wait()
    if not view.value:
        return

    dest_player, _ = await Player.objects.aget_or_create(discord_id=dest.id)

    @transaction.atomic
    def perform_transfer():
        trade = Trade.objects.create(player1=source_player, player2=dest_player)
        trade_objects: list[TradeObject] = []
        for ball in qs:
            trade_objects.append(TradeObject(trade=trade, ballinstance=ball, player=source_player))
        TradeObject.objects.bulk_create(trade_objects)
        updated = qs.update(player=dest_player, trade_player=source_player)

        if currency:
            # transfer every currency from source to dest, create one TradeMoney entry per currency
            balances = list(UserCurrencyBalance.objects.select_for_update().filter(player=source_player, amount__gt=0))
            for b in balances:
                # deduct from source
                amount = b.amount
                b.amount = 0
                b.save(update_fields=("amount",))
                # credit dest
                dest_ucb, _ = UserCurrencyBalance.objects.get_or_create(player=dest_player, currency=b.currency, defaults={"amount": 0})
                dest_ucb.amount = F("amount") + amount
                dest_ucb.save(update_fields=("amount",))
                # record per-currency movement
                TradeMoney.objects.create(trade=trade, player=source_player, currency=b.currency, amount=amount)
        return updated

    updated = await sync_to_async(perform_transfer)()

    if currency:
        text = (
            f"{updated} {settings.plural_collectible_name} and balances transferred from {source} to {dest}."
        )
    else:
        text = f"{updated} {settings.plural_collectible_name} transferred from {source} to {dest}."
    await ctx.send(text, ephemeral=True)
    log.info(
        f"{ctx.author} transferred inventory of {source} ({source.id}, {updated} {settings.plural_collectible_name}) to {dest} ({dest.id}).",
        extra={"webhook": True},
    )
