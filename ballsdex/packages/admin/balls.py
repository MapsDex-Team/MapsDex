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
    if balls_count == 0 and (not currency or source_player.money == 0):
        await ctx.send(f"{source}'s inventory is empty.", ephemeral=True)
        return

    view = ConfirmChoiceView(ctx, accept_message="Confirmed, transferring...", cancel_message="Request cancelled.")
    if currency:
        text = (
            f"Are you sure you want to transfer {balls_count} {settings.plural_collectible_name} and "
            f"{format_currency(source_player.money)} from {source} to {dest}?"
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
    transferred_money = source_player.money

    @transaction.atomic
    def perform_transfer():
        trade = Trade.objects.create(
            player1=source_player, player2=dest_player, player1_money=source_player.money if currency else 0
        )
        trade_objects: list[TradeObject] = []
        for ball in qs:
            trade_objects.append(TradeObject(trade=trade, ballinstance=ball, player=source_player))
        TradeObject.objects.bulk_create(trade_objects)
        updated = qs.update(player=dest_player, trade_player=source_player)
        if currency:
            dest_player.money += source_player.money
            source_player.money = 0
            dest_player.save(update_fields=("money",))
            source_player.save(update_fields=("money",))
        return updated

    updated = await sync_to_async(perform_transfer)()

    if currency:
        text = (
            f"{updated} {settings.plural_collectible_name} and {format_currency(transferred_money)} "
            f"transferred from {source} to {dest}."
        )
    else:
        text = f"{updated} {settings.plural_collectible_name} transferred from {source} to {dest}."
    await ctx.send(text, ephemeral=True)
    log.info(
        f"{ctx.author} transferred inventory of {source} ({source.id}, {updated} {settings.plural_collectible_name}, "
        f"{format_currency(transferred_money if currency else 0)}) to {dest} ({dest.id}).",
        extra={"webhook": True},
    )
