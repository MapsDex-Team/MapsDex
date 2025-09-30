import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import random
import time
from datetime import datetime
from ballsdex.settings import settings

REPORT_CHANNEL_ID = 1407012534715809843
REPORT_GUILD_ID = 1406779375608664276
REPORT_JSON_PATH = os.path.join(os.path.dirname(__file__), "reports.json")

REPORT_TYPES = [
    ("Report Violation", "violation"),
    ("Report Bug", "bug"),
    ("Provide Suggestion", "suggestion"),
    ("Other", "other"),
]

def load_reports():
    if not os.path.exists(REPORT_JSON_PATH):
        return {}
    with open(REPORT_JSON_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def save_reports(data):
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def generate_report_id(existing_ids):
    while True:
        rid = str(random.randint(100000, 999999))
        if rid not in existing_ids:
            return rid

class ReportCog(commands.Cog, name="Report"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.report_messages = {}

    @app_commands.command(name="report", description="Report issues or suggestions to the backend server")
    @app_commands.describe(
        report_type="Select report type",
        content="Please describe your issue or suggestion in detail"
    )
    @app_commands.choices(
        report_type=[app_commands.Choice(name=label, value=value) for label, value in REPORT_TYPES]
    )
    async def report(self, interaction: discord.Interaction, report_type: app_commands.Choice[str], content: str):
        reports = load_reports()
        report_id = generate_report_id(reports.keys())
        now = datetime.utcnow().isoformat()
        reports[report_id] = {
            "user_id": interaction.user.id,
            "user_name": str(interaction.user),
            "type": report_type.name,
            "type_value": report_type.value,
            "content": content,
            "time": now,
            "replied": False,
            "reply_time": None,
            "reply_by": None,
        }
        save_reports(reports)

        embed = discord.Embed(
            title=f"New User Report Received (ID: {report_id})",
            color=discord.Color.orange()
        )
        embed.add_field(name="Report Type", value=report_type.name, inline=False)
        embed.add_field(name="Content", value=content, inline=False)
        embed.add_field(name="Report ID", value=report_id, inline=False)
        embed.add_field(name="Status", value="Pending", inline=False)
        embed.set_footer(text=f"From {interaction.user} ({interaction.user.id})")
        embed.timestamp = discord.utils.utcnow()

        view = ReportReplyView(self, report_id, reports[report_id])

        guild = self.bot.get_guild(REPORT_GUILD_ID)
        if guild is not None:
            channel = guild.get_channel(REPORT_CHANNEL_ID)
            if channel and isinstance(channel, discord.TextChannel):
                message = await channel.send(embed=embed, view=view)
                self.report_messages[report_id] = message
                await interaction.response.send_message(f"✅ Report submitted successfully! Your report ID is {report_id}. You will be notified of any updates via DM.", ephemeral=True)
                try:
                    await interaction.user.send(
                        f"Hello, we have received your report (ID: {report_id}, Type: {report_type.name}). Our administrators will process it.\nYou will be notified of any updates via DM. Thank you for your assistance!"
                    )
                except Exception:
                    pass
                return

        await interaction.response.send_message("❌ Report submission failed. Please contact an administrator.", ephemeral=True)

class ReportReplyView(discord.ui.View):
    def __init__(self, cog: ReportCog, report_id: str, report_data: dict):
        super().__init__(timeout=None)
        self.cog = cog
        self.report_id = report_id
        self.report_data = report_data

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.primary)
    async def reply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only administrators can use this feature.", ephemeral=True)
            return

        modal = ReportReplyModal(self.cog, self.report_id, self.report_data)
        await interaction.response.send_modal(modal)

class ReportReplyModal(discord.ui.Modal, title="Reply to Report"):
    def __init__(self, cog: ReportCog, report_id: str, report_data: dict):
        super().__init__()
        self.cog = cog
        self.report_id = report_id
        self.report_data = report_data
        self.reply_content = discord.ui.TextInput(
            label="Reply Content",
            placeholder="Enter your reply...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.reply_content)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        reports = load_reports()
        report = reports.get(self.report_id)
        if not report:
            await interaction.followup.send(f"❌ Report ID {self.report_id} not found.", ephemeral=True)
            return

        report["replied"] = True
        report["reply_time"] = datetime.utcnow().isoformat()
        report["reply_by"] = str(interaction.user)
        save_reports(reports)

        if self.report_id in self.cog.report_messages:
            original_message = self.cog.report_messages[self.report_id]
            original_embed = original_message.embeds[0]
            original_embed.color = discord.Color.green()
            for i, field in enumerate(original_embed.fields):
                if field.name == "Status":
                    original_embed.set_field_at(i, name="Status", value="Replied", inline=False)
                    break
            view = ReportReplyView(self.cog, self.report_id, report)
            for item in view.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await original_message.edit(embed=original_embed, view=view)

        embed = discord.Embed(
            title=f"Administrator Reply (Report ID: {self.report_id})",
            color=discord.Color.green()
        )
        embed.add_field(name="Report Type", value=report["type"], inline=False)
        embed.add_field(name="Original Content", value=report["content"], inline=False)
        embed.add_field(name="Administrator Reply", value=self.reply_content.value, inline=False)
        embed.set_footer(text=f"Replied by: {interaction.user} ({interaction.user.id})")
        embed.timestamp = discord.utils.utcnow()

        guild = self.cog.bot.get_guild(REPORT_GUILD_ID)
        if guild is not None:
            channel = guild.get_channel(REPORT_CHANNEL_ID)
            if channel and isinstance(channel, discord.TextChannel):
                await channel.send(embed=embed)
                await interaction.followup.send(f"✅ Reply sent successfully.", ephemeral=True)

                try:
                    user = await self.cog.bot.fetch_user(report["user_id"])
                    await user.send(
                        f"Hello, your report (ID: {self.report_id}, Type: {report['type']}) has received an administrator reply:\n"
                        f"{self.reply_content.value}"
                    )
                except Exception as e:
                    print(f"Error sending DM: {str(e)}")
                return

        await interaction.followup.send("❌ Reply failed. Please contact an administrator.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ReportCog(bot))
