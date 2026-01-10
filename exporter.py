import io
from db import fetchall

SCHOOL_CONFIG = [
    ("GT", "Georgia Tech"),
    ("Emory", "Emory"),
    ("GSU", "Georgia State"),
]

async def get_pasteable_text(bot, announcement_id) -> str:
    rows = await fetchall(
        "SELECT user_id, school, role, seats FROM ride_entries WHERE announcement_id=?",
        (announcement_id,)
    )

    organized = {k: {"drivers": [], "riders": []} for k, _ in SCHOOL_CONFIG}

    for uid, school, role, seats in rows:
        if school not in organized:
            continue

        member = bot.get_user(uid) or await bot.fetch_user(uid)
        name = member.display_name if member else f"User_{uid}"

        if role == "driver":
            organized[school]["drivers"].append((name, seats))
        else:
            organized[school]["riders"].append(name)

    output = io.StringIO()

    # Data rows
    max_rows = max(
        max(len(v["drivers"]), len(v["riders"]))
        for v in organized.values()
    )

    for i in range(max_rows):
        row_parts = []

        for key, _ in SCHOOL_CONFIG:
            drivers = organized[key]["drivers"]
            riders = organized[key]["riders"]

            d_name, d_seats = ("", "")
            r_name = ""

            if i < len(drivers):
                d_name, d_seats = drivers[i]
            if i < len(riders):
                r_name = riders[i]

            row_parts += [
                str(d_name),
                str(d_seats),
                str(r_name),
                ""  # checkbox column placeholder
            ]

        output.write("\t".join(row_parts) + "\n")

    return output.getvalue()
