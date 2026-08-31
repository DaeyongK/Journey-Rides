import discord

import availability
from availability import (
    decode_occurrence,
    encode_occurrence,
    get_poll,
    get_user_entries,
    occurrence_label,
    replace_entries,
)
from views import get_school

# Discord allows at most 25 options in a single select menu. A month only ever
# has ~8-10 ride occurrences, so one select is always enough; this is a guard.
MAX_OPTIONS = 25


# ─────────────────────────────────────────────────────────────
# Availability View (persistent, driver-facing)
# ─────────────────────────────────────────────────────────────
class AvailabilityView(discord.ui.View):
    def __init__(self, poll_id, school, occurrences, is_closed: bool):
        super().__init__(timeout=None)
        self.poll_id = str(poll_id)
        self.school = school
        self.occurrences = list(occurrences)[:MAX_OPTIONS]

        suffix = f"{self.poll_id}:{school}"

        options = [
            discord.SelectOption(
                label=occurrence_label(ride_date, ride_type),
                value=encode_occurrence(ride_date, ride_type),
            )
            for ride_date, ride_type in self.occurrences
        ] or [discord.SelectOption(label="No ride dates", value="none")]

        self.select = discord.ui.Select(
            placeholder="Closed — availability is no longer being collected"
            if is_closed
            else f"{school} drivers: select every date you can drive…",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id=f"avail:pick:{suffix}",
            disabled=is_closed or not self.occurrences,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        if not is_closed:
            mine = discord.ui.Button(
                label="My availability",
                emoji="📋",
                style=discord.ButtonStyle.secondary,
                custom_id=f"avail:mine:{suffix}",
            )
            mine.callback = self._on_mine
            self.add_item(mine)

            clear = discord.ui.Button(
                label="Clear my availability",
                emoji="🗑",
                style=discord.ButtonStyle.danger,
                custom_id=f"avail:clear:{suffix}",
            )
            clear.callback = self._on_clear
            self.add_item(clear)

    # ──────────────── Helpers ────────────────
    async def _guard(self, interaction: discord.Interaction) -> bool:
        """Returns True if the interaction may proceed. Otherwise a response has
        already been sent."""
        poll = await get_poll(self.poll_id)
        if not poll or poll["state"] == "closed":
            await interaction.response.send_message(
                "❌ This availability poll is closed.", ephemeral=True
            )
            return False

        school = get_school(interaction.user)
        if not school:
            await interaction.response.send_message(
                "❌ You need a school role to submit availability.", ephemeral=True
            )
            return False
        if school != self.school:
            await interaction.response.send_message(
                f"❌ This form is for **{self.school}** drivers — you're in **{school}**. "
                "Use your own school's availability channel.",
                ephemeral=True,
            )
            return False
        return True

    # ──────────────── Callbacks ────────────────
    async def _on_select(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return

        picks = sorted(
            decode_occurrence(v) for v in self.select.values if v != "none"
        )
        await replace_entries(self.poll_id, interaction.user.id, self.school, picks)

        if picks:
            lines = "\n".join(f"• {occurrence_label(d, t)}" for d, t in picks)
            await interaction.response.send_message(
                f"✅ Saved your availability for **{self.school}**:\n{lines}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ Cleared — you have no dates marked for this month.",
                ephemeral=True,
            )

        # If the schedule for this month is already assigned, fold this driver
        # into any dates that are still uncovered or short on drivers.
        newly_assigned = []
        if picks:
            newly_assigned = await availability.topup_assignment_for_user(
                interaction.client, self.poll_id, interaction.user.id, self.school
            )
        if newly_assigned:
            got = "\n".join(
                f"• {occurrence_label(d, t)}" for d, t in newly_assigned
            )
            await interaction.followup.send(
                "🚗 The driving schedule for this month was already set, so "
                "you've been **assigned to drive** these dates that were short "
                f"on drivers:\n{got}\n\nMessage an admin if you can't make one.",
                ephemeral=True,
            )

        await availability.refresh_admin_availability_message(
            interaction.client, self.poll_id
        )

    async def _on_mine(self, interaction: discord.Interaction):
        entries = await get_user_entries(self.poll_id, interaction.user.id)
        if entries:
            lines = "\n".join(
                f"• {occurrence_label(d, t)}" for d, t in sorted(entries)
            )
            await interaction.response.send_message(
                f"📋 Your current availability:\n{lines}", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "📋 You haven't marked any availability for this month yet.",
                ephemeral=True,
            )

    async def _on_clear(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await replace_entries(self.poll_id, interaction.user.id, self.school, [])
        await interaction.response.send_message(
            "🗑 Cleared your availability for this month.", ephemeral=True
        )
        await availability.refresh_admin_availability_message(
            interaction.client, self.poll_id
        )
