from typing import TYPE_CHECKING

from ballsdex.packages.report.cog import ReportCog

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(ReportCog(bot)) 