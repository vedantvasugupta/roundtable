import aiosqlite
import asyncio
import os

# Get the absolute path to the database file
DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_database.db")
print(f"Using database at: {DATABASE_FILE}")

async def add_voting_message_id_column():
    """Add voting_message_id column to proposals table if it doesn't exist"""
    print("Starting database migration...")
    try:
        # Connect to the database
        async with aiosqlite.connect(DATABASE_FILE) as db:
            # Check if the column already exists
            async with db.execute("PRAGMA table_info(proposals)") as cursor:
                columns = await cursor.fetchall()
                column_names = [column[1] for column in columns]

                if "voting_message_id" not in column_names:
                    print("Adding voting_message_id column to proposals table...")
                    # Add the column
                    await db.execute("ALTER TABLE proposals ADD COLUMN voting_message_id TEXT")
                    await db.commit()
                    print("Column added successfully!")
                else:
                    print("voting_message_id column already exists.")

            # Also check for guild_id column which is referenced in the code
            if "guild_id" not in column_names:
                print("Adding guild_id column to proposals table...")
                # Add the column
                await db.execute("ALTER TABLE proposals ADD COLUMN guild_id INTEGER")
                # Set guild_id equal to server_id for existing rows
                await db.execute("UPDATE proposals SET guild_id = server_id WHERE guild_id IS NULL")
                await db.commit()
                print("guild_id column added successfully!")
            else:
                print("guild_id column already exists.")

            print("Database migration completed successfully!")
    except Exception as e:
        print(f"Error during migration: {e}")

# Run the migration
if __name__ == "__main__":
    asyncio.run(add_voting_message_id_column())
