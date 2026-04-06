import asyncio
import io
import os
import aiohttp
import traceback
from db import fetchall
from dotenv import load_dotenv
load_dotenv()

SCHOOL_CONFIG = [
    ("GT", "Georgia Tech"),
    ("Emory", "Emory"),
    ("GSU", "Georgia State"),
]

GOOGLE_URL = os.getenv("GOOGLE_URL")
_add = "add"
_delete = "delete"
_reset = "reset"

async def sync_to_sheets(member, announcement_id, school, role, seats, phone, info, count, content_category):
    
    """
    Sends a single user's ride entry to the Google Sheet.
    The Google Apps Script handles figuring out which columns to put it in.
    """
    clean_category = content_category[0] if type(content_category).__name__ == 'Record' else content_category
    clean_count = count[0] if type(count).__name__ == 'Record' else count
    
    payload = {
        "action": _add,
        "announcement_id": str(announcement_id),
        "school": str(school), 
        "role": str(role.lower().strip()),  # ensure it matches "driver" or "rider"
        "name": str(member.display_name),
        "seats": str(seats) if seats else "",
        "phone": str(phone),
        "info": str(info) or "",
        "content_category": str(clean_category),
        "count": str(clean_count)
    }

    # 15 seconds is usually plenty of time for Google to respond
    timeout = aiohttp.ClientTimeout(total=15)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            # allow_redirects=True is required because Google Apps Script always redirects POST requests
            async with session.post(GOOGLE_URL, json=payload, allow_redirects=True) as resp:
                text = await resp.text()
                
                return text
                    
        except asyncio.TimeoutError:
            print(f"⚠️ Sheets Sync timed out for {member.display_name}. Google took too long.")
        except Exception as e:
            print(f"⚠️ Unexpected Sheets Sync Error: {e}")
            traceback.print_exc()

async def remove_from_sheets(member, announcement_id, school, role, seats, phone, info, count, content_category):
    clean_category = content_category[0] if type(content_category).__name__ == 'Record' else content_category
    clean_count = count[0] if type(count).__name__ == 'Record' else count

    payload = {
        "action": _delete,
        "announcement_id": str(announcement_id),
        "school": str(school), 
        "role": str(role).lower().strip(),  
        "name": str(member.display_name),
        "seats": str(seats) if seats else "",
        "phone": str(phone),
        "info": str(info or ""),
        "count": str(clean_count),                
        "content_category": str(clean_category) 
    }

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(GOOGLE_URL, json=payload, allow_redirects=True) as resp:
                text = await resp.text()
                
                return text
                    
        except Exception as e:
            return (f"⚠️ Sheets Delete Error: {e}")

async def trigger_sheet_reset(announcement_id, content_category):
    payload = {
        "action": _reset,
        "announcement_id": str(announcement_id),
        "content_category": str(content_category)
    }

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(GOOGLE_URL, json=payload, allow_redirects=True) as resp:
                text = await resp.text()
                if resp.status == 200 and "Error" not in text:
                    print(f"✅ Reset Successful: {text}")
                else:
                    print(f"❌ Reset Failed: {text}")
        except Exception as e:
            print(f"⚠️ Sheets Reset Error: {e}")
        
async def get_pasteable_text(bot, announcement_id) -> str:
    rows = await fetchall(
        "SELECT user_id, school, role, seats, phone, info FROM ride_entries WHERE announcement_id=$1",
        (announcement_id,)
    )

    organized = {k: {"drivers": [], "riders": []} for k, _ in SCHOOL_CONFIG}
    guild = bot.get_guild(int(os.getenv("SERVER_ID")))

    for uid, school, role, seats, phone, info in rows:
        if school not in organized or not guild:
            continue

        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                continue  # skip users not in server

        name = member.display_name

        if role == "driver":
            organized[school]["drivers"].append((name, seats, phone, info))
        else:
            organized[school]["riders"].append((name, phone, info))

    output = io.StringIO()

    max_rows = max(
        max(len(v["drivers"]), len(v["riders"]))
        for v in organized.values()
    )

    for i in range(max_rows):
        row_parts = []

        for key, _ in SCHOOL_CONFIG:
            drivers = organized[key]["drivers"]
            riders = organized[key]["riders"]

            d_name, d_seats, d_phone, d_info = ("", "", "", "")
            r_name, r_phone, r_info = ("", "", "")

            if i < len(drivers):
                d_name, d_seats, d_phone, d_info = drivers[i]
                d_info = d_info or "" # If d_info is null, d_info = ""
            if i < len(riders):
                r_name, r_phone, r_info = riders[i]

            row_parts += [
                d_name,
                str(d_seats),
                d_phone, 
                d_info, 
                r_name,
                r_phone,
                r_info,
                ""
            ]

        output.write("\t".join(row_parts) + "\n")

    return output.getvalue()
