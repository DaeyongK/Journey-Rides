import asyncio
import os
from contextlib import asynccontextmanager
import asyncpg
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

_pool = None

# Only one sign-up at a time may pick the next sheet row (row_num).
row_lock = asyncio.Lock()


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL)

    with open("schema.sql") as f:
        schema = f.read()

    async with _pool.acquire() as conn:
        await conn.execute(schema)


@asynccontextmanager
async def transaction():
    """Yields a connection whose queries commit (or roll back) together."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            yield conn


async def execute(query, params=()):
    async with _pool.acquire() as conn:
        await conn.execute(query, *params)


async def executemany(query, args_list):
    if not args_list:
        return
    async with _pool.acquire() as conn:
        await conn.executemany(query, args_list)


async def fetchone(query, params=()):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(query, *params)


async def fetchall(query, params=()):
    async with _pool.acquire() as conn:
        return await conn.fetch(query, *params)
