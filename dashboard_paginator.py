import io
import discord
from db import execute
from exporter import get_pasteable_text

# ─────────────────────────────────────────────────────────────
# Dashboard Paginator View
# Manages the buttons on the admin dashboard
# ─────────────────────────────────────────────────────────────
class DashboardPaginator(discord.ui.View):
    def __init__(self, embeds, announcement_id, title, start_index=0):
        super().__init__(timeout=None)

        self.embeds = embeds
        self.announcement_id = announcement_id
        self.title = title
        self.index = start_index

        self.prev_button = discord.ui.Button(
            label="◀️ Prev",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dashboard:prev:{announcement_id}"
        )
        self.next_button = discord.ui.Button(
            label="▶️ Next",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dashboard:next:{announcement_id}"
        )
        self.export_button = discord.ui.Button(
            label="📊 Export Snapshot",
            style=discord.ButtonStyle.success,
            custom_id=f"dashboard:export:{announcement_id}"
        )
        self.prev_button.callback = self.on_prev
        self.next_button.callback = self.on_next
        self.export_button.callback = self.on_export

        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.export_button)
        self._update_buttons()

    # Ensures buttons are enabled/disabled correctly 
    # e.g. Admin cannot click next on the last page
    def _update_buttons(self):
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(self.embeds) - 1

    # Renders current pagination status
    def _current_embed(self):
        self._update_buttons()
        embed = self.embeds[self.index].copy()
        embed.set_footer(text=f"Page {self.index + 1}/{len(self.embeds)}")
        return embed

    async def on_prev(self, interaction: discord.Interaction):
        if self.index <= 0:
            await interaction.response.defer()
            return

        self.index -= 1
        await execute(
            "UPDATE announcements SET dashboard_page=$1 WHERE id=$2",
            (self.index, self.announcement_id)
        )

        await interaction.response.edit_message(
            embed=self._current_embed(),
            view=self
        )

    async def on_next(self, interaction: discord.Interaction):
        if self.index >= len(self.embeds) - 1:
            await interaction.response.defer()
            return

        self.index += 1
        await execute(
            "UPDATE announcements SET dashboard_page=$1 WHERE id=$2",
            (self.index, self.announcement_id)
        )

        await interaction.response.edit_message(
            embed=self._current_embed(),
            view=self
        )

    async def on_export(self, interaction: discord.Interaction):
        csv_text = await get_pasteable_text(
            interaction.client,
            self.announcement_id
        )

        file_buffer = io.BytesIO(csv_text.encode("utf-8"))

        await interaction.response.send_message(
            content=(
                "📋 **How to use this export**\n"
                "1. Download the file\n"
                "2. Open it\n"
                "3. Copy everything\n"
                "4. Paste into the Google Sheets template accordingly"
            ),
            file=discord.File(fp=file_buffer, filename="rides_export.txt"),
            ephemeral=True
        )
