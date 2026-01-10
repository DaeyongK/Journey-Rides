import os
import discord
from db import execute, fetchone
from time_utils import now
from dashboard import refresh_dashboard_for_announcement
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID"))
ROLE_IDS = {
    "GT": int(os.getenv("GT_ROLE_ID")),
    "Emory": int(os.getenv("EMORY_ROLE_ID")),
    "GSU": int(os.getenv("GSU_ROLE_ID"))
}

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def get_school(member) -> str:
    for school, role_id in ROLE_IDS.items():
        if any(r.id == role_id for r in member.roles):
            return school
    return None

async def is_registered(announcement_id, user_id) -> bool:
    row = await fetchone(
        """
        SELECT 1
        FROM ride_entries
        WHERE announcement_id=? AND user_id=?
        """,
        (announcement_id, user_id)
    )
    return row is not None

# ─────────────────────────────────────────────────────────────
# Modals
# ─────────────────────────────────────────────────────────────
class AnnouncementContentModal(discord.ui.Modal, title="Announcement Content"):
    content = discord.ui.TextInput(
        label="Announcement Body",
        style=discord.TextStyle.paragraph,
        placeholder="Paste your full announcement here...",
        required=True,
        max_length=4000,
    )

    def __init__(self, interaction, aid, title, send_at_norm, end_at_norm, reactable):
        super().__init__()
        self.interaction = interaction
        self.aid = aid
        self.title_val = title
        self.send_at = send_at_norm
        self.end_at = end_at_norm
        self.reactable = reactable

    async def on_submit(self, interaction: discord.Interaction):
        await execute(
            """
            INSERT INTO announcements (
                id, title, send_at, end_at, content, reactable, state, message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, 'scheduled', NULL)
            """,
            (
                self.aid,
                self.title_val,
                self.send_at,
                self.end_at,
                self.content.value,
                int(self.reactable),
            )
        )

        await interaction.response.send_message(
            f"✅ Announcement created: `{self.aid}`",
            ephemeral=True
        )


class AnnouncementEditModal(discord.ui.Modal, title="Edit Announcement"):
    title_input = discord.ui.TextInput(
        label="Announcement Title",
        max_length=256,
        required=True,
    )

    content_input = discord.ui.TextInput(
        label="Announcement Body",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )

    def __init__(self, announcement_id, old_title, old_content):
        super().__init__()
        self.announcement_id = announcement_id
        self.title_input.default = old_title
        self.content_input.default = old_content

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await execute(
            """
            UPDATE announcements
            SET title=?, content=?
            WHERE id=?
            """,
            (
                self.title_input.value,
                self.content_input.value,
                self.announcement_id,
            )
        )

        row = await fetchone(
            """
            SELECT message_id, reactable
            FROM announcements
            WHERE id=?
            """,
            (self.announcement_id,)
        )

        if row:
            message_id, reactable = row

            # Update public message
            public_ch = interaction.client.get_channel(PUBLIC_CHANNEL_ID)
            if not public_ch:
                try:
                    public_ch = await interaction.client.fetch_channel(PUBLIC_CHANNEL_ID)
                except Exception:
                    public_ch = None

            if public_ch and message_id:
                try:
                    msg = await public_ch.fetch_message(message_id)
                    await msg.edit(
                        content=f"**{self.title_input.value}**\n{self.content_input.value}"
                    )
                except Exception:
                    pass

            # Refresh dashboard (if reactable)
            if reactable:
                await refresh_dashboard_for_announcement(
                    interaction.client,
                    self.announcement_id
                )

        await interaction.followup.send(
            "✅ Announcement updated successfully.",
            ephemeral=True
        )


class DriverModal(discord.ui.Modal, title="Driver Info"):
    seats = discord.ui.TextInput(label="Number of seats", required=True)

    def __init__(self, announcement_id):
        super().__init__()
        self.announcement_id = announcement_id

    async def on_submit(self, interaction: discord.Interaction):
        school = get_school(interaction.user)
        try:
            seats = int(self.seats.value)
            if seats <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid positive number of seats.",
                ephemeral=True
            )
            return

        await execute(
            """
            INSERT INTO ride_entries
            VALUES (?, ?, ?, 'driver', ?, ?)
            """,
            (
                self.announcement_id,
                interaction.user.id,
                school,
                seats,
                now(),
            )
        )

        await interaction.response.send_message(
            "✅ You are now registered as a driver.",
            ephemeral=True
        )

        await refresh_dashboard_for_announcement(
            interaction.client,
            self.announcement_id
        )

# ─────────────────────────────────────────────────────────────
# Ride View (Public Buttons)
# ─────────────────────────────────────────────────────────────

class RideView(discord.ui.View):
    def __init__(self, announcement_id, is_closed: bool):
        super().__init__(timeout=None)
        self.announcement_id = announcement_id

        if not is_closed:
            request = discord.ui.Button(
                label="Request Ride",
                style=discord.ButtonStyle.primary,
                custom_id=f"ride:request:{announcement_id}"
            )
            request.callback = self.request_callback
            self.add_item(request)

        driver = discord.ui.Button(
            label="I'm a Driver",
            style=discord.ButtonStyle.success,
            custom_id=f"ride:driver:{announcement_id}"
        )
        driver.callback = self.driver_callback

        withdraw = discord.ui.Button(
            label="Withdraw",
            style=discord.ButtonStyle.danger,
            custom_id=f"ride:withdraw:{announcement_id}"
        )
        withdraw.callback = self.withdraw_callback

        self.add_item(driver)
        self.add_item(withdraw)

    # ──────────────── Callbacks ────────────────

    async def request_callback(self, interaction: discord.Interaction):
        school = get_school(interaction.user)
        if not school:
            await interaction.response.send_message(
                "❌ You must have a school role (GT, Emory, or GSU) to request a ride.",
                ephemeral=True
            )
            return

        reg = await is_registered(self.announcement_id, interaction.user.id)
        if reg:
            await interaction.response.send_message(
                "⚠️ You are already registered. Please withdraw before switching roles.",
                ephemeral=True
            )
            return

        await execute(
            """
            INSERT INTO ride_entries
            VALUES (?, ?, ?, 'rider', NULL, ?)
            """,
            (
                self.announcement_id,
                interaction.user.id,
                school,
                now(),
            )
        )

        await interaction.response.send_message(
            "✅ Ride requested.",
            ephemeral=True
        )

        await refresh_dashboard_for_announcement(
            interaction.client,
            self.announcement_id
        )

    async def driver_callback(self, interaction: discord.Interaction):
        school = get_school(interaction.user)
        if not school:

            await interaction.response.send_message(
                "❌ You must have a school role (GT, Emory, or GSU) to register as a driver.",
                ephemeral=True
            )
            return

        reg = await is_registered(self.announcement_id, interaction.user.id)
        if reg:
            await interaction.response.send_message(
                "⚠️ You are already registered. Please withdraw before switching roles.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            DriverModal(self.announcement_id)
        )

    async def withdraw_callback(self, interaction: discord.Interaction):
        reg = await is_registered(self.announcement_id, interaction.user.id)
        if not reg:
            await interaction.response.send_message(
                "ℹ️ You are not registered for this announcement.",
                ephemeral=True
            )
            return

        await execute(
            """
            DELETE FROM ride_entries
            WHERE announcement_id=? AND user_id=?
            """,
            (self.announcement_id, interaction.user.id)
        )

        await interaction.response.send_message(
            "❌ You have withdrawn.",
            ephemeral=True
        )

        await refresh_dashboard_for_announcement(
            interaction.client,
            self.announcement_id
        )
