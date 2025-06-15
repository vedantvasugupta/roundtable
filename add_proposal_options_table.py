import asyncio
import aiosqlite
from db import DATABASE_FILE

async def add_proposal_options_table():
    """Add a new table to store proposal options"""
    print("Adding proposal_options table to the database...")

    async with aiosqlite.connect(DATABASE_FILE) as db:
        # Create the proposal_options table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS proposal_options (
                option_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                option_text TEXT NOT NULL,
                option_order INTEGER NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id),
                UNIQUE(proposal_id, option_text)
            )
        """)

        await db.commit()
        print("✅ proposal_options table created successfully")

async def add_get_set_options_functions():
    """Add functions to get and set proposal options to db.py"""
    print("Adding functions to db.py...")

    # Read the current db.py file
    with open("rtable/db.py", "r") as f:
        db_content = f.read()

    # Check if the functions already exist
    if "async def add_proposal_option" in db_content:
        print("Functions already exist in db.py")
        return

    # Add the new functions
    new_functions = '''
# ========================
# === PROPOSAL OPTIONS FUNCTIONS
# ========================

async def add_proposal_option(proposal_id, option_text, option_order):
    """Add an option for a proposal"""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO proposal_options (proposal_id, option_text, option_order)
            VALUES (?, ?, ?)
            ON CONFLICT(proposal_id, option_text) DO UPDATE SET option_order = EXCLUDED.option_order
            """,
            (proposal_id, option_text, option_order)
        )
        await db.commit()
        return True

async def add_proposal_options(proposal_id, options):
    """Add multiple options for a proposal"""
    async with get_db() as db:
        for i, option in enumerate(options):
            await db.execute(
                """
                INSERT INTO proposal_options (proposal_id, option_text, option_order)
                VALUES (?, ?, ?)
                ON CONFLICT(proposal_id, option_text) DO UPDATE SET option_order = EXCLUDED.option_order
                """,
                (proposal_id, option, i)
            )
        await db.commit()
        return True

async def get_proposal_options(proposal_id):
    """Get all options for a proposal, ordered by option_order"""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT option_text FROM proposal_options
            WHERE proposal_id = ?
            ORDER BY option_order
            """,
            (proposal_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows] if rows else []
'''

    # Append the new functions to db.py
    with open("rtable/db.py", "a") as f:
        f.write(new_functions)

    print("✅ Functions added to db.py")

async def main():
    await add_proposal_options_table()
    await add_get_set_options_functions()
    print("✅ All database modifications completed")

if __name__ == "__main__":
    asyncio.run(main())
