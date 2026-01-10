import os
import asyncpg
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

_pool = None


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL)

    with open("schema.sql") as f:
        schema = f.read()

    async with _pool.acquire() as conn:
        await conn.execute(schema)


async def execute(query, params=()):
    async with _pool.acquire() as conn:
        await conn.execute(query, *params)


async def fetchone(query, params=()):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(query, *params)


async def fetchall(query, params=()):
    async with _pool.acquire() as conn:
        return await conn.fetch(query, *params)
