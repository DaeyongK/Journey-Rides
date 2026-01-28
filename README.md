# 🚗 Journey Rides Discord Bot

A Discord bot for managing **scheduled ride announcements**, **driver/rider signups**, and **live admin dashboards** with automatic closing and exporting.

Built with `discord.py` (v2), **PostgreSQL**, and persistent UI views.

---

## ✨ Features

- 📢 Scheduled announcements with automatic posting
- ⏳ Auto-close announcements at a specified time
- 🚘 Driver & rider signup system
- 📊 Live admin dashboard with pagination
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

---

## 🐘 Local PostgreSQL Setup (Docker)

This project uses **PostgreSQL** for both local development and production.
To ensure parity between environments, Postgres should be run locally using Docker.

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
| /announcement_create | Schedule a new announcement (refer to parameter definitions below for more information) | title:**Sunday Service Rides for 1/11/2026**<br>send_at:**2026-01-04 08:00**<br>end_at:**2026-01-10 23:00**<br>reactable:**True** |
| /announcement_edit | Edit a sent or closed announcement | announcement_id:**550e8400-e29b-41d4-a716-446655440000** |
| /announcement_delete | Permanently delete an announcement | announcement_id:**550e8400-e29b-41d4-a716-446655440000** |
| /announcement_unschedule | Remove a scheduled announcement | announcement_id:**550e8400-e29b-41d4-a716-446655440000** |
| /announcement_view | View all announcements and content | (no arguments) |


## 📢 Creating Announcements

| Parameter | Description |
|-----------|-------------|
| title | Title of the announcement shown in embeds and dashboards |
| send_at | When the announcement is sent. Format: YYYY-MM-DD HH:MM (US/Eastern) |
| end_at | When requests close. Must be same as or after send_at (US/Eastern). If the announcement is non-reactable, just enter some arbitrary time in the future.|
| reactable | Whether users can submit ride requests and driver entries. If True, users will be able to submit ride requests and driver entries. If False, the announcement will have no buttons for submitting requests, and no admin dashboard will be displayed. In other words, it will be a simple announcement that could be used as reminders to sign up etc. |

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

---

### `dashboard.py`
**Admin dashboard rendering**
- Builds multi-page embeds
- Aggregates drivers/riders by school
- Refreshes dashboards on changes

---

### `dashboard_paginator.py`
**Dashboard controls**
- Prev / Next page navigation
- Snapshot export button
- Persistent pagination state

---

### `exporter.py`
**Export logic**
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
