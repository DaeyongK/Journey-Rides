import aiosqlite
from datetime import datetime, timezone

DB_PATH = "rides.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        with open("schema.sql") as f:
            await db.executescript(f.read())
        await db.commit()

async def execute(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()

async def fetchall(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        return await cur.fetchall()

async def fetchone(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        return await cur.fetchone()
