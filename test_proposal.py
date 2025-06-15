import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta
import db

async def test_proposal_creation():
    """
    Test function to simulate the !propose command and verify database schema.
    
    This test:
    1. Checks the current schema of the proposals table
    2. Attempts to create a proposal using the db.create_proposal function
    3. Verifies if the operation succeeds or fails
    """
    print("\n=== Testing Proposal Creation ===")
    
    # Test data
    server_id = 123456789
    proposer_id = 987654321
    title = "Server Update"
    description = "Update the rules for clarity and engagement"
    voting_mechanism = "plurality"
    deadline = datetime.now() + timedelta(days=3)
    requires_approval = True
    
    # Step 1: Check current database schema
    print("\n1. Checking current database schema...")
    try:
        conn = sqlite3.connect(db.DATABASE_FILE)
        cursor = conn.cursor()
        
        # Get schema information for the proposals table
        cursor.execute("PRAGMA table_info(proposals)")
        columns = cursor.fetchall()
        
        print(f"Current columns in proposals table:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        
        # Check if server_id column exists
        server_id_exists = any(col[1] == 'server_id' for col in columns)
        print(f"\nserver_id column exists: {server_id_exists}")
        
        conn.close()
    except Exception as e:
        print(f"Error checking schema: {e}")
        return
    
    # Step 2: Attempt to create a proposal
    print("\n2. Attempting to create a proposal...")
    try:
        proposal_id = await db.create_proposal(
            server_id, proposer_id, title, description, 
            voting_mechanism, deadline, requires_approval
        )
        print(f"Success! Proposal created with ID: {proposal_id}")
    except Exception as e:
        print(f"Error creating proposal: {e}")
        
        # If the error is about missing server_id column, suggest the fix
        if "no column named server_id" in str(e):
            print("\nConfirmed issue: 'server_id' column is missing in the proposals table")
            print("Recommended fix: Add the server_id column to the proposals table")
    
    # Step 3: Verify the proposal was created (if no error occurred)
    if 'proposal_id' in locals():
        try:
            proposal = await db.get_proposal(proposal_id)
            print(f"\nRetrieved proposal: {proposal}")
        except Exception as e:
            print(f"Error retrieving proposal: {e}")

async def fix_database_schema():
    """
    Fix the database schema by adding the missing server_id column and other required columns.
    This function will:
    1. Create a backup of the current database
    2. Add missing columns to the proposals table
    3. Migrate data from old columns to new columns if needed
    """
    print("\n=== Fixing Database Schema ===")
    
    # Step 1: Create a backup of the database
    print("\n1. Creating database backup...")
    try:
        # Read the current database
        with open(db.DATABASE_FILE, 'rb') as src:
            db_data = src.read()
        
        # Write to backup file
        backup_file = f"{db.DATABASE_FILE}.backup"
        with open(backup_file, 'wb') as dest:
            dest.write(db_data)
        
        print(f"Backup created: {backup_file}")
    except Exception as e:
        print(f"Error creating backup: {e}")
        return False
    
    # Step 2: Alter the table to add missing columns
    print("\n2. Updating database schema...")
    try:
        conn = sqlite3.connect(db.DATABASE_FILE)
        cursor = conn.cursor()
        
        # Check current schema
        cursor.execute("PRAGMA table_info(proposals)")
        columns = {col[1]: col for col in cursor.fetchall()}
        
        # Add missing columns if they don't exist
        if 'server_id' not in columns:
            print("Adding server_id column...")
            cursor.execute("ALTER TABLE proposals ADD COLUMN server_id INTEGER")
        
        # Check if we need to migrate data from guild_id to server_id
        if 'guild_id' in columns and 'server_id' in columns:
            print("Migrating data from guild_id to server_id...")
            cursor.execute("UPDATE proposals SET server_id = guild_id WHERE server_id IS NULL")
        
        # Add other missing columns based on the code in db.py
        required_columns = {
            'proposal_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
            'title': 'TEXT',
            'description': 'TEXT',
            'requires_approval': 'BOOLEAN DEFAULT TRUE',
            'approved_by': 'INTEGER'
        }
        
        for col_name, col_type in required_columns.items():
            if col_name not in columns and col_name != 'proposal_id':  # Skip primary key
                print(f"Adding {col_name} column...")
                cursor.execute(f"ALTER TABLE proposals ADD COLUMN {col_name} {col_type}")
        
        # Commit changes
        conn.commit()
        conn.close()
        print("Database schema updated successfully")
        return True
    except Exception as e:
        print(f"Error updating schema: {e}")
        return False

async def verify_fix():
    """
    Verify that the fix worked by creating a proposal and checking if it was stored correctly.
    """
    print("\n=== Verifying Fix ===")
    
    # Test data
    server_id = 123456789
    proposer_id = 987654321
    title = "Verification Test"
    description = "Testing if the database fix worked"
    voting_mechanism = "plurality"
    deadline = datetime.now() + timedelta(days=1)
    requires_approval = True
    
    try:
        # Create a proposal
        proposal_id = await db.create_proposal(
            server_id, proposer_id, title, description, 
            voting_mechanism, deadline, requires_approval
        )
        print(f"Proposal created with ID: {proposal_id}")
        
        # Retrieve the proposal
        proposal = await db.get_proposal(proposal_id)
        
        # Verify server_id was stored correctly
        if proposal and proposal.get('server_id') == server_id:
            print(f"Success! server_id was stored correctly: {proposal.get('server_id')}")
            return True
        else:
            print(f"Error: server_id was not stored correctly")
            print(f"Retrieved proposal: {proposal}")
            return False
    except Exception as e:
        print(f"Error during verification: {e}")
        return False

async def main():
    """Main function to run the tests and fix."""
    # Initialize the database
    print("Initializing database...")
    await db.init_db()
    
    # Test proposal creation to confirm the issue
    await test_proposal_creation()
    
    # Fix the database schema
    success = await fix_database_schema()
    
    if success:
        # Verify the fix worked
        await verify_fix()
    else:
        print("Fix was not applied successfully. Please check the errors above.")

if __name__ == "__main__":
    asyncio.run(main())
