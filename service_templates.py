"""
Templates for /announcement_service. One ride_date + type (F / SJ / SC)
produces three linked announcements: OPEN (signup post), REMINDER, RECAP
(spreadsheet link). Offsets are in days relative to ride_date.
"""

SHEET_LINK = "https://docs.google.com/spreadsheets/d/1Hf7TgEe3dzYOkeCtV2Psde3UFXobA8lqYh9B8UEMwx4/edit?usp=sharing"

SERVICE_TEMPLATES = {
    "F": {
        "label": "Friday Prayer Room",
        "ride_weekday": "F",  # matches ride_type_for_date()
        "content_category": "F",
        "slots": {
            "open": {
                "reactable": True,
                "send_offset_days": -4, "send_time": "09:00",  # Monday
                "end_offset_days": -1,  "end_time": "20:00",   # Thursday 8 PM
                "title": "Friday Prayer Room Rides",
                "body": (
                    "Rides for Friday Prayer Room are now open! Please click “I need a ride” "
                    "below if you need a ride to PM on {date}!\n\n"
                    "Onsite POC: {poc}\n\n"
                    "If you are able to drive, please click “I’m a driver.”\n\n"
                    "Pick-up Information:\n"
                    "Emory—\nWhere: Circle in front of Tarbutton Hall\nWhen: 7:40 PM\n"
                    "GT—\nWhere: Student Center Parking Lot\nWhen: 7:30PM\n"
                    "GSU—\nWhere: Half-circle in front of University Commons\nWhen: 7:30PM"
                ),
            },
            "reminder": {
                "reactable": False,
                "send_offset_days": -1, "send_time": "09:00",  # Thursday
                "end_offset_days": -1,  "end_time": "20:00",
                "title": "Rides for Prayer Room Close Tonight!",
                "body": (
                    "Rides Requests for Friday Prayer Room closes tonight at 8 PM!\n"
                    "Be sure to sign up if you need a ride!!"
                ),
            },
            "recap": {
                "reactable": False,
                "send_offset_days": 0, "send_time": "12:00",   # Friday noon
                "end_offset_days": 0,  "end_time": "23:59",
                "title": "Rides for Friday Prayer Room {date}!",
                "body": (
                    "Please check to see the rides for this week’s FRIDAY NIGHT PRAYER ROOM {date}!\n"
                    "**If you need to drop, request any necessary changes, please text in #rides-logistic\n\n"
                    "Onsite POC: {poc}\n\n"
                    f"{SHEET_LINK}"
                ),
            },
        },
    },

    "SC": {
        "label": "Sunday College Service",
        "ride_weekday": "S",
        "content_category": "S",
        "slots": {
            "open": {
                "reactable": True,
                "send_offset_days": -6, "send_time": "09:00",  # Monday
                "end_offset_days": -2,  "end_time": "23:59",   # Friday midnight
                "title": "COLLEGE Sunday Service Rides",
                "body": (
                    "Rides for Sunday Service are now open! Please click “I need a ride” below "
                    "if you need a ride to this week's COLLEGE service on {date}.\n\n"
                    "Onsite POC: {poc}\n\n"
                    "If you are able to drive, please click “I’m a driver.”\n\n"
                    "Pick-up Information (COLLEGE service (11 AM)):\n"
                    "Emory—\nNo rides,\nSunday service is at White Hall 208 at 11 AM\n\n"
                    "GT—\nWhere: Student Center Parking Lot\n"
                    "When: 1st Trip: 9:50 AM\n\t2nd Trip: 10:30 AM \n\n"
                    "GSU—\nWhere: Half-circle in front of University Commons\nWhen: 10:30 AM"
                ),
            },
            "reminder": {
                "reactable": False,
                "send_offset_days": -2, "send_time": "09:00",  # Friday
                "end_offset_days": -2,  "end_time": "23:59",
                "title": "Rides for Sunday Service Close Tonight!",
                "body": (
                    "Rides Requests for Sunday Service closes tonight at 11:59 PM!\n"
                    "Be sure to sign up if you need a ride!!"
                ),
            },
            "recap": {
                "reactable": False,
                "send_offset_days": -1, "send_time": "17:00",  # Saturday 5 PM
                "end_offset_days": 0,   "end_time": "23:59",   # Sunday night
                "title": "Rides for College Sunday Service {date}!",
                "body": (
                    "Please check to see the rides for this week’s COLLEGE SERVICE {date}!\n"
                    "**If you need to drop, request any necessary changes, please text in #rides-logistic\n\n"
                    "Onsite POC: {poc}\n\n"
                    f"{SHEET_LINK}"
                ),
            },
        },
    },

    "SJ": {
        "label": "Sunday Joint Service",
        "ride_weekday": "S",
        "content_category": "S",
        "slots": {
            "open": {
                "reactable": True,
                "send_offset_days": -6, "send_time": "09:00",  # Monday
                "end_offset_days": -2,  "end_time": "23:59",   # Friday midnight
                "title": "JOINT Sunday Service Rides",
                "body": (
                    "Rides for Sunday Service are now open! Please click “I need a ride” below "
                    "if you need a ride to this week's JOINT service on {date}.\n\n"
                    "Onsite POC: {poc}\n\n"
                    "Pick-up Information (JOINT service (1:30 PM)):\n"
                    "Emory—\nWhere: Circle in front of Tarbutton Hall\n"
                    "When:  1st trip: 12:40 PM\n\t2nd trip: 1:00 PM\n"
                    "GT—\nWhere: Student Center Parking Lot\n"
                    "When:   1st trip: 12:10 PM\n2nd trip: 12:50 PM\n"
                    "GSU—\nWhere: Half-circle in front of University Commons\nWhen:   12:30 PM"
                ),
            },
            "reminder": {
                "reactable": False,
                "send_offset_days": -2, "send_time": "09:00",  # Friday
                "end_offset_days": -2,  "end_time": "23:59",
                "title": "Rides for Sunday Service Close Tonight!",
                "body": (
                    "Rides Requests for Sunday Service closes tonight at 11:59 PM!\n"
                    "Be sure to sign up if you need a ride!!"
                ),
            },
            "recap": {
                "reactable": False,
                "send_offset_days": -1, "send_time": "17:00",  # Saturday 5 PM
                "end_offset_days": 0,   "end_time": "23:59",   # Sunday night
                "title": "Rides for Joint Sunday Service {date}!",
                "body": (
                    "Please check to see the rides for this week’s JOINT SERVICE {date}!\n"
                    "**If you need to drop, request any necessary changes, please text in #rides-logistic\n\n"
                    "Onsite POC: {poc}\n\n"
                    f"{SHEET_LINK}"
                ),
            },
        },
    },
}