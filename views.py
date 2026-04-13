import asyncio
import os
import discord
from db import execute, fetchone
from exporter import remove_from_sheets, sync_to_sheets
from time_utils import format_close_time, now
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
        WHERE announcement_id=$1 AND user_id=$2
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

    def __init__(self, interaction, aid, title, send_at_dt, end_at_dt, reactable):
        super().__init__()
        self.interaction = interaction
        self.aid = aid
        self.title_val = title
        self.send_at = send_at_dt
        self.end_at = end_at_dt
        self.reactable = reactable

        if self.reactable:
            self.content_category = discord.ui.TextInput(
                label="Ride Category",
                style=discord.TextStyle.short,
                placeholder="F for Friday PM or S for Sunday Service",
                required=True,
                max_length=1,
            )
            self.add_item(self.content_category)
        else:
            self.content_category = None

    async def on_submit(self, interaction: discord.Interaction):
        category_value = None

        if self.content_category:
            raw_category = self.content_category.value.strip().upper()
            
            if raw_category not in ["F", "S"]:
                await interaction.response.send_message(
                    "❌ **Invalid Ride Category!** Please recreate the announcement and type exactly **F** (for Friday PM) or **S** (for Sunday Service).",
                    ephemeral=True
                )
                return
            
            category_value = raw_category

        await execute(
            """
            INSERT INTO announcements (
                id, title, send_at, end_at, content, content_category, reactable, state
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'scheduled')
            """,
            (
                self.aid,
                self.title_val,
                self.send_at,
                self.end_at,
                self.content.value,
                category_value,
                self.reactable,
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

    def __init__(self, announcement_id, old_title, old_content, old_content_category):
        super().__init__()
        self.announcement_id = announcement_id
        self.title_input.default = old_title
        self.content_input.default = old_content

        if old_content_category is not None:
            self.content_category = discord.ui.TextInput(
                label="Ride Category",
                style=discord.TextStyle.short,
                placeholder="F for Friday PM or S for Sunday Service",
                required=True,
                max_length=1,
                default=old_content_category
            )
            self.add_item(self.content_category)
        else:
            self.content_category = None

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        new_category = self.content_category.value if self.content_category else None

        await execute(
            """
            UPDATE announcements
            SET title=$1, content=$2, content_category=$3
            WHERE id=$4
            """,
            (
                self.title_input.value,
                self.content_input.value,
                new_category,
                self.announcement_id,
            )
        )

        row = await fetchone(
            """
            SELECT message_id, reactable
            FROM announcements
            WHERE id=$1
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

                    header = f"**{self.title_input.value}**"

                    row = await fetchone(
                        """
                        SELECT end_at
                        FROM announcements
                        WHERE id=$1
                        """,
                        (self.announcement_id,)
                    )

                    end_at = None

                    if row:
                        end_at = row["end_at"]

                    if reactable:
                        close_text = format_close_time(end_at)
                        header += f"\n{close_text}"

                    await msg.edit(
                        content=f"{header}\n{self.content_input.value}"
                    )

                except Exception as e:
                    print(e)

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
    phone = discord.ui.TextInput(label="Phone Number (e.g. 9999999999)", required=True)
    info = discord.ui.TextInput(label="Additional Information (Optional)", required=False)

    def __init__(self, announcement_id, default_seats=None, default_number=None):
        super().__init__()
        self.announcement_id = announcement_id
        if (default_seats):
            self.seats.default = default_seats
            self.phone.default = default_number

    async def on_submit(self, interaction: discord.Interaction):
        # Instantly send the loading message
        await interaction.response.send_message("⏳ Registering...", ephemeral=True)
        
        school = get_school(interaction.user).strip()
        current_count = await fetchone(
                "SELECT MAX(row_num) FROM ride_entries WHERE announcement_id=$1 AND role=$2 AND school=$3", 
                (self.announcement_id, "driver", school)
            )
        
        cat_record = await fetchone(
            "SELECT content_category FROM announcements WHERE id=$1 AND reactable=$2",
            (self.announcement_id, True)
        )
        content_category = cat_record[0] if cat_record else "F"

        highest_row = int(current_count[0]) if current_count and current_count[0] is not None else 0
        row_count = highest_row + 1

        try:
            if not str.isdigit(self.seats.value):
                raise ValueError("seats")

            seats = int(self.seats.value)
            if seats <= 0:
                raise ValueError("seats")

            phone = self.phone.value
            if not str.isdigit(self.phone.value) or len(self.phone.value) != 10:
                raise ValueError("phone")

            info = self.info.value
            if len(self.info.value) > 130:
                raise ValueError("info")

        except ValueError as e:
            # Edit ephemeral response if validation fails
            if str(e) == "seats":
                await interaction.edit_original_response(content="❌ Please enter a valid positive number of seats.")
            if str(e) == "phone":
                await interaction.edit_original_response(content="❌ Please enter a valid phone number (e.g 9999999999 without dashes).")
            if str(e) == "info":
                await interaction.edit_original_response(content="❌ Additional information is limited to 130 characters.")
            return

        await execute(
            """
            INSERT INTO ride_entries (
                announcement_id, user_id, school, role, seats, updated_at, phone, info, row_num
            )
            VALUES ($1, $2, $3, 'driver', $4, $5, $6, $7, $8)
            """,
            (self.announcement_id, interaction.user.id, school, seats, now(), phone, info, row_count)
        )

        # Sync to Google Sheets and wait for response
        google_receipt = await sync_to_sheets(
                member=interaction.user,
                announcement_id=self.announcement_id,
                school=school,
                role="driver",
                seats=seats,
                phone=phone,
                info=info,
                count=row_count,
                content_category=content_category
            )
        
        if google_receipt is None:
            google_receipt = "Error: No response received from Google."

        if "Error" in google_receipt or "⚠️" in google_receipt:
            await interaction.edit_original_response(
                content=f"❌ **Google Sheets Error:**\n`{google_receipt}`\n*Please try again or contact an admin.*"
            )
            return
        
        # Stores information
        await execute(
            """
            INSERT INTO saved_info (
                user_id,
                role,
                seats,
                phone
            )
            VALUES ($1, 'driver', $2, $3)
            ON CONFLICT (user_id)
            DO UPDATE SET
                role = 'driver',
                seats = EXCLUDED.seats,
                phone = EXCLUDED.phone
            """,
            (
                interaction.user.id,
                seats,
                phone
            )
        )
        await interaction.edit_original_response(content="✅ You are now registered as a driver.")

        await refresh_dashboard_for_announcement(interaction.client, self.announcement_id)

class RiderModal(discord.ui.Modal, title = "Rider Info"):
    phone = discord.ui.TextInput(label="Phone Number (e.g. 9999999999)", required=True)
    info = discord.ui.TextInput(label="Additional Information (Optional)", required=False)
    # Have a "save info" button
    def __init__(self, announcement_id, default_number):
        super().__init__()
        self.announcement_id = announcement_id
        if (default_number):
            self.phone.default = default_number

    async def on_submit(self, interaction: discord.Interaction):
        # Instantly send the loading message
        await interaction.response.send_message("⏳ Registering...", ephemeral=True)
        
        school = get_school(interaction.user).strip()
        current_count = await fetchone(
                "SELECT MAX(row_num) FROM ride_entries WHERE announcement_id=$1 AND role=$2 AND school=$3", 
                (self.announcement_id, "rider", school)
            )
        
        cat_record = await fetchone(
            "SELECT content_category FROM announcements WHERE id=$1 AND reactable=$2",
            (self.announcement_id, True)
        )
        content_category = cat_record[0] if cat_record else "F"

        highest_row = int(current_count[0]) if current_count and current_count[0] is not None else 0
        row_count = highest_row + 1

        try:

            phone = self.phone.value
            if not str.isdigit(self.phone.value) or len(self.phone.value) != 10:
                raise ValueError("phone")

            info = self.info.value
            if len(self.info.value) > 130:
                raise ValueError("info")
            
        # Edit ephemeral message if validation fails

        except ValueError as e:
            if str(e) == "phone":
                await interaction.edit_original_response(content="❌ Please enter a valid phone number (e.g 9999999999 without dashes).")
            if str(e) == "info":
                await interaction.edit_original_response(content="❌ Additional information is limited to 130 characters.")
            return

        await execute(
            """
            INSERT INTO ride_entries (
                announcement_id, user_id, school, role, seats, updated_at, phone, info, row_num
            )
            VALUES ($1, $2, $3, 'rider', NULL, $4, $5, $6, $7)
            """,
            (self.announcement_id, interaction.user.id, school, now(), phone, info, row_count)
        )
        
        # Sync to Google Sheets and wait for response
        google_receipt =await sync_to_sheets(
                member=interaction.user,
                announcement_id=self.announcement_id,
                school=school,
                role="rider",
                seats=None,
                phone=phone,
                info=info,
                count=row_count,
                content_category=content_category
            )
        
        if google_receipt is None:
            google_receipt = "Error: No response received from Google."

        if "Error" in google_receipt or "⚠️" in google_receipt:
            await interaction.edit_original_response(
                content=f"❌ **Google Sheets Error:**\n`{google_receipt}`\n*Please try again or contact an admin.*"
            )
            return

        await execute(
            """
            INSERT INTO saved_info (
                user_id,
                role,
                seats,
                phone
            )
            VALUES ($1, 'rider', NULL, $2)
            ON CONFLICT (user_id)
            DO UPDATE SET
                role = 'rider',
                phone = EXCLUDED.phone
            """,
            (
                interaction.user.id,
                phone
            )
        )

        await interaction.edit_original_response(content="✅ You are now registered as a rider.")

        await refresh_dashboard_for_announcement(interaction.client, self.announcement_id)
        
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

        saved = await fetchone(
        """
        SELECT phone
        FROM saved_info
        WHERE user_id=$1
        """,
        (interaction.user.id,)
        )

        default_number = None

        if saved:
            default_number = saved["phone"]

        await interaction.response.send_modal(
            RiderModal(self.announcement_id, default_number)
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

        saved = await fetchone(
        """
        SELECT seats, phone
        FROM saved_info
        WHERE user_id=$1
        """,
        (interaction.user.id,)
        )

        default_seats = None
        default_number = None

        if saved:
            default_seats = saved["seats"]
            default_number = saved["phone"]

        await interaction.response.send_modal(
            DriverModal(self.announcement_id, default_seats, default_number)
        )

    async def withdraw_callback(self, interaction: discord.Interaction):
        # Immediate loading state in ephemeral message
        await interaction.response.send_message("⏳ Withdrawing...", ephemeral=True)
        
        entry = await fetchone(
            "SELECT school, role, seats, phone, info, row_num FROM ride_entries WHERE user_id=$1 AND announcement_id=$2",
            (interaction.user.id, self.announcement_id)
        )

        if not entry:
            await interaction.edit_original_response(content="ℹ️ You are not registered for this announcement.")
            return

        school, role, seats, phone, info, user_count = entry

        cat_record = await fetchone(
            "SELECT content_category FROM announcements WHERE id=$1",
            (self.announcement_id,)
        )
        content_category = cat_record[0] if cat_record else "F"

        try:
            # Wait for Google Sheets response before confirming withdrawal to user
            google_receipt = await remove_from_sheets(
                interaction.user, 
                self.announcement_id, 
                school, 
                role, 
                seats, 
                phone, 
                info, 
                user_count, 
                content_category
            )

            await execute(
                "DELETE FROM ride_entries WHERE announcement_id=$1 AND user_id=$2",
                (self.announcement_id, interaction.user.id)
            )

            if google_receipt is None:
                google_receipt = "Error: No response received from Google."

            if "Error" in google_receipt or "⚠️" in google_receipt:
                await interaction.edit_original_response(
                    content=f"❌ **Google Sheets Error:**\n`{google_receipt}`\n*Your withdrawal was processed locally but failed to sync with Google Sheets. Please contact an admin to resolve this.*"
                )
                return

            # Edit ephemeral response to confirm successful withdrawal
            await interaction.edit_original_response(content="✅ You have successfully withdrawn and been removed from the ride list.")

            await refresh_dashboard_for_announcement(interaction.client, self.announcement_id)

        except Exception as e:
            print(f"Error during withdrawal: {e}")
            await interaction.edit_original_response(content="⚠️ Something went wrong while withdrawing. Please try again.")