import aiosqlite
import asyncio

DATABASE_FILE = "bot_database.db"

async def add_results_pending_column():
    """Add results_pending_announcement column to proposals table"""
    print("Adding results_pending_announcement column to proposals table...")

    async with aiosqlite.connect(DATABASE_FILE) as db:
        # Check if column already exists
        cursor = await db.execute("PRAGMA table_info(proposals)")
        columns = await cursor.fetchall()
        column_names = [column[1] for column in columns]

        if "results_pending_announcement" not in column_names:
            print("Column does not exist, adding it now...")
            await db.execute(
                "ALTER TABLE proposals ADD COLUMN results_pending_announcement BOOLEAN DEFAULT 0"
            )
            await db.commit()
            print("Column added successfully!")
        else:
            print("Column already exists, no changes needed.")

if __name__ == "__main__":
    asyncio.run(add_results_pending_column())
    print("Done!")
