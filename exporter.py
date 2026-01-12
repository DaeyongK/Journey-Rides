async def get_pasteable_text(bot, announcement_id) -> str:
    rows = await fetchall(
        "SELECT user_id, school, role, seats FROM ride_entries WHERE announcement_id=$1",
        (announcement_id,)
    )

    organized = {k: {"drivers": [], "riders": []} for k, _ in SCHOOL_CONFIG}
    guild = bot.get_guild(int(os.getenv("SERVER_ID")))

    for uid, school, role, seats in rows:
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
            organized[school]["drivers"].append((name, seats))
        else:
            organized[school]["riders"].append(name)

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

            d_name, d_seats = ("", "")
            r_name = ""

            if i < len(drivers):
                d_name, d_seats = drivers[i]
            if i < len(riders):
                r_name = riders[i]

            row_parts += [
                d_name,
                str(d_seats),
                r_name,
                ""
            ]

        output.write("\t".join(row_parts) + "\n")

    return output.getvalue()
