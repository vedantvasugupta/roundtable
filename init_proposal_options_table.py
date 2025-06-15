import asyncio
from db import init_db

async def main():
    """Initialize the database with all required tables, including proposal_options"""
    print("Initializing database tables...")
    await init_db()
    print("✅ Database tables initialized successfully")

if __name__ == "__main__":
    asyncio.run(main())
