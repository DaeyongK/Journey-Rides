# 🚗 Journey Rides Discord Bot

A Discord bot for managing **scheduled ride announcements**, **driver/rider signups**, and **live admin dashboards** with automatic closing and exporting.

Built with `discord.py` (v2), **PostgreSQL**, **Google Apps Script**, and persistent UI views.

---

## ✨ Features

- 📢 Scheduled announcements with automatic posting
- ⏳ Auto-close announcements at a specified time
- 🚘 Driver & rider signup system
- 📊 Live admin dashboard with pagination
- 🔄 Automatic Google Sheet Syncing
- 📤 Exportable signup snapshots (Google Sheets–ready)
- 🔁 Persistent buttons and views across restarts
- 🗑 Automatic cleanup of old announcements

---

## 🛠 Requirements

- **Python 3.10+**
- **Docker** (for local PostgreSQL)
- A Discord bot with:
  - Message Content Intent
  - Server Members Intent
- A Discord server with:
  - Public announcement channel
  - Admin dashboard channel
  - School role IDs (GT / Emory / GSU)
- A Google Sheet with:
  - Google Apps Script (set to available to anyone)

---

## 🐘 Local PostgreSQL Setup (Docker)

This project uses **PostgreSQL** for both local development and production.
To ensure parity between environments, Postgres should be run locally using Docker.
**Everytime you start the bot, ensure that Postgres is running on Docker.**

---

### 1️⃣ Install Docker

If Docker is not already installed:

- macOS / Windows: https://www.docker.com/products/docker-desktop
- Linux: https://docs.docker.com/engine/install/

Verify installation:

```bash
docker --version
```

### 2️⃣ Start PostgreSQL Container

Run the following command to start a local Postgres instance:

```bash
docker run --name journey-postgres \
  -e POSTGRES_USER=journey \
  -e POSTGRES_PASSWORD=journey \
  -e POSTGRES_DB=journey \
  -p 5432:5432 \
  -d postgres:16
```

This will:

- Create a database named journey
- Expose Postgres on localhost:5432
- Persist data as long as the container exists

### 3️⃣ Stopping / Restarting PostgreSQL

Stop the database:

```bash
docker stop journey-postgres
```

Restart it later:

```bash
docker start journey-postgres
```

Remove it entirely (⚠️ deletes all data):

```bash
docker rm -f journey-postgres
```

## 💻 Discord Bot Setup (Discord Developer Profile)

This project uses **Discord Developer Profile** to set up the Discord bot, configure its permissions, and connect it to production.

---

### 1️⃣ Login to Discord Developer Profile

1. Go to the Discord Developer Portal:  
   https://discord.com/developers/applications
2. Log in with the Discord account that will own the bot.
3. Click **New Application**, give it a name, and create it.

---

### 2️⃣ Create the Bot User

1. In the application dashboard, select **Bot** from the left sidebar.
2. Click **Add Bot** and confirm.
3. (Optional but recommended)
   - Set a bot username
   - Upload a bot avatar
4. Enable only the **Privileged Gateway Intents** your bot actually needs.

---

### 3️⃣ Copy the Bot Token

1. In the **Bot** tab, locate **Bot Token**.
2. Click **Reset Token** or **Copy**.
3. Store the token securely.

⚠️ **Do not commit this token to version control or share it publicly.**

Add it to your `.env` file:

    DISCORD_TOKEN=your_bot_token_here

---

### 4️⃣ Configure Bot Permissions

The bot requires the following permissions:

- Manage Messages  
- Send Messages  
- Use External Emojis  
- Add Reactions  
- Embed Links  

To configure permissions:

1. Go to **OAuth2 → URL Generator**
2. Under **Scopes**, select:
   - bot
3. Under **Bot Permissions**, enable:
   - Manage Messages
   - Send Messages
   - Use External Emojis
   - Add Reactions
   - Embed Links

An invite URL will be generated automatically.

---

### 5️⃣ Invite the Bot to a Server

1. Copy the generated OAuth2 invite URL.
2. Open it in your browser.
3. Select the server to invite the bot to.
4. Click **Authorize**.
5. Complete the CAPTCHA.

The bot will now appear in the server (offline until running).

---



### 🔐 Security Notes

- Never expose your bot token
- Reset the token immediately if compromised
- Use environment variables for all secrets
- Grant only the permissions the bot actually needs

## 📊 Google Sheets Setup (Apps Script Web App)

This project uses a **Google Apps Script Web App** to automatically export the rides data into a Google Sheet. Contact **Joshua Yi** (@jshuao on Discord) if there are any issues with the Google Apps Script on deployment.

---

### 1️⃣ Prep the Google Sheet

1. Go to Google Sheets and create a new spreadsheet (or open existing one).
2. Look at the tabs at the bottom left of the screen. 
3. You **must** create or rename two tabs to match these exactly (capitalization and spaces matter!):
   - `Friday PM Imports`
   - `Sunday Service Imports`
4. Do **NOT** add column headers to the sheet.

---

### 2️⃣ Add the Apps Script Code

1. On your Google Sheet, click **Extensions → Apps Script** in the top menu bar.
2. A new code editor tab will open, likely showing a file named `Code.gs`.
3. Delete all the default code inside (the empty `myFunction` block).
4. Paste the complete **Journey Rides Apps Script** (the Javascript code provided for this project) into the editor.
   - Found in `googleappscript.js`
5. Click the **Save** icon (the floppy disk) at the top.

---

### 3️⃣ Deploy the Web App (Crucial Step)

This is where we turn the code into a live URL that your Discord bot can talk to.

1. In the top right corner of the Apps Script editor, click the big blue **Deploy** button.
2. Select **New deployment**.
3. Next to "Select type", click the gear icon ⚙️ and choose **Web app**.
4. Configure the settings exactly like this:
   - **Description:** Journey Rides Bot
   - **Execute as:** `Me (your.email@gmail.com)`
   - **Who has access:** `Anyone` *(⚠️ **CRITICAL:** If you set this to anything else, the bot will be blocked!)*
5. Click **Deploy**.

---

### 4️⃣ Authorize the Script

Because you wrote a custom script asking to edit your spreadsheets, Google will ask you to verify it.

1. Click **Authorize access** on the popup.
2. Select the Google account that owns the spreadsheet.
3. You will see a warning screen saying "Google hasn’t verified this app."
4. Click the small **Advanced** text at the bottom.
5. Click **Go to Untitled project (unsafe)**.
6. Click **Allow** to grant the script permission to edit your sheets.

---

### 5️⃣ Connect to the Bot

1. Once authorized, a "Deployment successfully updated" window will appear.
2. Under the "Web app" section, copy the **URL** (it will end in `/exec`).
3. Store this URL securely. 

Add it to your `.env` file just like your Discord token:

    GOOGLE_URL=https://script.google.com/macros/s/your_long_script_id_here/exec

---

### 6️⃣ (OPTIONAL) Sync to public spreadsheet

1. Open the public spreadsheet and go to the import pages.
2. Click the top-left cell where you want the data to be imported to and type:
   ```=IMPORTRANGE("PRIVATE_SHEET_URL", "SHEET_NAME_FROM_PRIVATE_URL!A1:Z100")```
   Do this for each page, Friday PM Imports (Private) -> Friday PM Import (Public), Sunday Service Imports (Private) -> Sunday Service Import (Public).

### 🔐 Security Notes

- **Never share your Web App URL publicly.** Anyone with this link can send HTTP POST requests to add or delete rows in your spreadsheet.
- Do not delete the Google Account that created the script, or the Web App will go offline.
- If you ever need to update the Javascript code in the future, you **must** deploy it as a New Version (Deploy → Manage Deployments → Edit ✏️ → New version → Deploy).

## 🚀 Getting Started

### 1️⃣ Clone the Repository

```bash
git clone <your-repo-url>
cd journey-rides
``` 

### 2️⃣ Create a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3️⃣ Install Dependencies
```bash
pip install -r requirements.txt
```

### 4️⃣ Configure Environment Variables

Create a .env file in the project root (real values are shared privately):

```bash
DISCORD_TOKEN=your_bot_token_here

DATABASE_URL=postgresql://journey:journey@localhost:5432/journey

SERVER_ID=123456789012345678
PUBLIC_CHANNEL_ID=123456789012345678
ADMIN_CHANNEL_ID=123456789012345678
WITHDRAW_CHANNEL_ID=123456789012345678
AVAILABILITY_CHANNEL_ID_GT=123456789012345678      # GT drivers' availability channel
AVAILABILITY_CHANNEL_ID_EMORY=123456789012345678   # Emory drivers' availability channel

ALLOWED_ROLE_ID=123456789012345678
GT_ROLE_ID=123456789012345678
EMORY_ROLE_ID=123456789012345678
GSU_ROLE_ID=123456789012345678
```

### 5️⃣ Run the Bot

```bash
python bot.py
```

On first startup:

- A PostgreSQL connection pool is created

- Database tables are initialized automatically from schema.sql

You should see:

```bash
[scheduler] started
```

Your slash commands will sync automatically.

## 🧭 Slash Commands Overview

| Command | Description | Example |
|--------|-------------|---------|
| /announcement_create | Schedule a new announcement (refer to parameter definitions below for more information). Optional `ride_date` links it to the availability schedule so assigned drivers are auto-registered on send. | title:**Sunday Service Rides for 1/11/2026**<br>send_at:**2026-01-04 08:00**<br>end_at:**2026-01-10 23:00**<br>reactable:**True**<br>ride_date:**2026-01-11** (optional) |
| /announcement_edit | Edit a sent or closed announcement | announcement_id:**550e8400-e29b-41d4-a716-446655440000** |
| /announcement_delete | Permanently delete an announcement | announcement_id:**550e8400-e29b-41d4-a716-446655440000** |
| /announcement_unschedule | Remove a scheduled announcement | announcement_id:**550e8400-e29b-41d4-a716-446655440000** |
| /announcement_view | View all announcements and content | (no arguments) |
| /availability_create | Open a monthly driver-availability poll; posts a dropdown into the availability channel with every Friday PM / Sunday Service date that month, plus a live availability list in the admin channel. **A month can only be sent once.** | month:**2026-09**<br>exclude:**2026-09-25** (optional)<br>sunday_host:**2026-09-06J, 2026-09-13G** (optional) |
| /availability_view | Show the current availability grid (per school) before assigning | month:**2026-09** |
| /availability_assign | Auto-assign drivers toward each ride's base target (even load) and post the schedule to the admin channel. Full recompute — clears manual edits | month:**2026-09** |
| /availability_adjust | Add or remove a single driver for one ride occurrence (use week-of to add extra drivers based on rider counts) | month:**2026-09**<br>date:**2026-09-06**<br>ride_type:**Sunday Service**<br>driver:**@user**<br>action:**add** |
| /availability_close | Close the poll and disable the dropdown | month:**2026-09** |


## 🗓 Monthly Driver Availability

Plan a month of driving ahead of time:

1. `/availability_create month:2026-09` posts a dropdown into **each school's own channel** — `AVAILABILITY_CHANNEL_ID_GT` and `AVAILABILITY_CHANNEL_ID_EMORY` (both required). Drivers select **every** date they can drive (re-selecting replaces their previous answer); a driver in the wrong school's channel is turned away. Buttons let a driver review (**📋 My availability**) or wipe (**🗑 Clear**) their picks. A month can only be created once — the poll row is kept permanently. *(GSU does not currently use the monthly availability system — only GT and Emory.)*

   **`sunday_host`** (optional) sets which campus each Sunday service is at, so each school's dropdown only shows the Sundays *its* drivers are needed for. Format: comma-separated `YYYY-MM-DD` + a letter — **`J`** joint service (both schools drive), **`E`** Emory-hosted service (**GT** drives — GT students need rides to Emory), **`G`** GT-hosted service (**Emory** drives). If you use it, list **every** Sunday that month (e.g. `2026-09-06J, 2026-09-13G, 2026-09-20G, 2026-09-27E`). Friday PM dates always go to both schools. Omit `sunday_host` entirely and every date goes to both, as before.
2. A **live availability list** is posted to the admin channel: per school, a "by ride" list (who's available each date) and a "by driver" list (each driver → their dates). It edits itself in place each time a driver responds. `/availability_view month:2026-09` shows the same on demand (ephemeral).
3. `/availability_assign month:2026-09` fills each ride up to its **base target** (GT and Emory separately), spreading each driver's total across the month as evenly as their availability allows, and posts a per-school schedule to the admin channel. Rides that come in **below target** (including zero) are flagged with their count (e.g. `⚠ 2/6`) in the schedule and the command's reply — but **no "drivers needed" call-out is posted at this stage**. Any already-sent announcement for these rides is updated to match.
4. `/availability_adjust …` adds/removes individual drivers beyond the auto-assignment (e.g. stacking extra drivers on a heavy week). Also updates any already-sent announcement for that ride.
5. `/availability_close month:2026-09` disables the dropdown.

**Base targets** live in `ASSIGN_TARGETS` in `availability.py`, keyed by `(school, ride_type)` — currently **GT Sunday Service = 10**, **GT Friday PM = 5**, **Emory Sunday Service = 6**, **Emory Friday PM = 3**; anything not listed defaults to **1**. Edit that dict to change them.

If a driver submits or updates availability **after** the month is assigned, they're automatically slotted into any of their available rides that are still below target (never removing anyone); they get a DM-style ephemeral note and the admin channel is notified.

Views persist across restarts. One poll per month, ever (enforced by a unique index on `availability_polls.month`).

### Pipeline into weekly announcements

When you create an announcement with a `ride_date` (and `reactable: True`), the drivers assigned to
that `(ride_date, category)` in the availability schedule are **auto-registered as drivers** — they
don't need to press *I'm a Driver*. Extra drivers can still sign up normally, and any driver can still
*Withdraw*.

An already-registered driver (assigned or self-signed-up) can press *I'm a Driver* again at any time
to **update their seats, phone, and notes** — the modal is pre-filled with their current values and
their spot on the list / sheet stays put. Only riders are asked to withdraw first.

`ride_date` must land on a **Friday** (Friday PM) or a **Sunday** (Sunday Service), and its weekday
must match the ride category entered in the announcement modal — otherwise the create fails and you
re-run `/announcement_create`.

The announcement's driver list is reconciled with the schedule at three points:

1. when the announcement sends,
2. when `/availability_assign` recomputes the month, and
3. when `/availability_adjust` adds/removes a driver for that ride.

Each reconcile **adds** assigned drivers who aren't signed up and **withdraws** drivers who were
auto-added but are no longer assigned. Manually-signed-up drivers are never touched. The admin
dashboard and Google Sheet are updated to match, and an admin-channel note summarises the changes.

- Assigned drivers are always registered. If a driver has saved seats + phone (from a previous
  signup, in `saved_info`) those are filled in; otherwise they're registered with blank seats/phone
  and the admin note flags them to press *I'm a Driver* to fill it in.
- Drivers assigned but no longer in the server are skipped and listed in the admin note.
- Already-**closed** announcements are left alone.

**Shortfall alert on close.** When a `ride_date` announcement's signups close, each school (GT /
Emory) whose total driver **seats < riders** gets an urgent "drivers needed" post in its availability
channel, showing the seat/rider counts and the shortfall. Schools that are covered get nothing.


## 📢 Creating Announcements

| Parameter | Description |
|-----------|-------------|
| title | Title of the announcement shown in embeds and dashboards |
| send_at | When the announcement is sent. Format: YYYY-MM-DD HH:MM (US/Eastern) |
| end_at | When requests close. Must be same as or after send_at (US/Eastern). If the announcement is non-reactable, just enter some arbitrary time in the future.|
| reactable | Whether users can submit ride requests and driver entries. If True, users will be able to submit ride requests and driver entries. If False, the announcement will have no buttons for submitting requests, and no admin dashboard will be displayed. In other words, it will be a simple announcement that could be used as reminders to sign up etc. |
| ride_date | *(optional)* The calendar date of this ride, `YYYY-MM-DD`. Must be a Friday or Sunday and match the ride category. If set and `reactable` is True, drivers assigned to that date in the monthly availability schedule are auto-registered (and kept in sync). Leave blank to disable the pipeline. |

---

## 📂 File Structure & Responsibilities

### `bot.py`
**Main entry point**
- Bot startup
- Command registration
- Persistent view restoration
- Scheduler loop initialization

---

### `views.py`
**User-facing UI logic**
- Modals for announcements & drivers
- Ride request / driver / withdraw buttons
- Public interaction handling

---

### `scheduler.py`
**Background automation**
- Sends scheduled announcements
- Closes expired announcements
- Deletes old announcements (180 days)
- Keeps dashboards in sync

> Run only **one** bot process against a database. Two live instances double-send announcements and
> cause `10062 Unknown interaction` errors on slash commands.

---

### `dashboard.py`
**Admin dashboard rendering**
- Builds multi-page embeds
- Aggregates drivers/riders by school
- Refreshes dashboards on changes

---

### `availability.py`
**Monthly driver availability & assignment**
- Poll / occurrence / entry / assignment DB helpers
- `auto_assign` — even-split driver assignment per school
- Renders the availability list and the driving-schedule embeds
- `prefill_announcement_drivers` / `sync_assignments_to_announcements` — reconcile a sent announcement's driver signups with the current assignments (add + auto-withdraw)
- `request_drivers_if_short` — per-school "drivers needed" call-out, posted **only** when an announcement closes with driver seats < riders

---

### `availability_views.py`
**Availability dropdown (persistent, one per school)**
- Multi-select of ride dates + "My availability" / "Clear" buttons
- School-scoped: only that school's drivers can use it
- Replaces a driver's picks on each submit; disabled when the poll closes

---

### `dashboard_paginator.py`
**Dashboard controls**
- Prev / Next page navigation
- Snapshot export button
- Persistent pagination state

---

### `exporter.py`
**Export logic**
- Automatically syncs signup data into the targeted Google Sheet
- Converts signup data into a paste-ready tab-separated format
- Designed for Google Sheets templates

---

### `db.py`
**Async SQLite helpers**
- Query execution
- Fetch helpers
- Database initialization

---

### `time_utils.py`
**Time handling utilities**
- Eastern ↔ UTC conversions
- Discord relative timestamps
- Announcement close formatting

---

### `schema.sql`
**Database schema**
- `announcements`
- `ride_entries`
- `saved_info`
- `availability_polls`, `availability_poll_messages`, `availability_occurrences`, `availability_entries`, `availability_assignments`

---

## 🔐 Permissions Required

The bot requires the following Discord permissions:
- **Manage Messages** (for admin commands)
- **Read Message History**
- **Send Messages**
- **Use Slash Commands**

---

## 🧠 Notes

- **Important**: All times must be entered with the following format: YYYY-MM-DD TT:TT (e.g. 2026-01-10 09:00). Entered times are assumed to be Eastern Time, but is converted to UTC internally (check time_utils.py)
- **Important**: Admin dashboard pagination has global impact. In other words, if an admin clicks the next button on the dashboard, other admins will have their dashboards also panel to the next page.
- Buttons and dashboards persist across bot restarts
- Dashboards auto-refresh every second
- Editing closed announcements is allowed
- Riders and drivers cannot double-register
