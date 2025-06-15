# temp_migrate.py  – run once to patch an existing bot_database.db
import asyncio, aiosqlite

async def migrate(db_file="bot_database.db"):
    async with aiosqlite.connect(db_file) as db:
        # 1. See which columns we already have
        cur = await db.execute("PRAGMA table_info(proposals);")
        cols = {row[1] for row in await cur.fetchall()}

        # 2. Add the flag column only if it is missing
        if "results_pending_announcement" not in cols:
            await db.execute(
                "ALTER TABLE proposals "
                "ADD COLUMN results_pending_announcement INTEGER DEFAULT 0"
            )
            await db.commit()
            print("✓ Column added, database patched.")
        else:
            print("✓ Column already present, nothing to do.")

asyncio.run(migrate())
#