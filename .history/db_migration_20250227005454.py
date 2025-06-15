import asyncio
import sqlite3
import os
import shutil
from datetime import datetime

# Database file path
DATABASE_FILE = "bot_database.db"

async def backup_database():
    """Create a backup of the database before making changes."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{DATABASE_FILE}.{timestamp}.backup"
    
    print(f"Creating backup: {backup_file}")
    
    try:
        shutil.copy2(DATABASE_FILE, backup_file)
        print(f"Backup created successfully: {backup_file}")
        return True
    except Exception as e:
        print(f"Error creating backup: {e}")
        return False

async def check_current_schema():
    """Check the current schema of the proposals table."""
    print("\nChecking current database schema...")
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Get schema information for the proposals table
        cursor.execute("PRAGMA table_info(proposals)")
        columns = cursor.fetchall()
        
        print(f"Current columns in proposals table:")
        column_dict = {}
        
        for col in columns:
            col_id, col_name, col_type, not_null, default_val, pk = col
            column_dict[col_name] = {
                'type': col_type,
                'not_null': not_null,
                'default': default_val,
                'pk': pk
            }
            print(f"  - {col_name} ({col_type})")
        
        conn.close()
        return column_dict
    except Exception as e:
        print(f"Error checking schema: {e}")
        return {}

async def migrate_database():
    """
    Migrate the database schema to match the code in db.py.
    
    This function:
    1. Adds missing columns to the proposals table
    2. Migrates data from old columns to new columns if needed
    """
    print("\nMigrating database schema...")
    
    # First, check the current schema
    current_columns = await check_current_schema()
    
    # Create a connection to the database
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    try:
        # Add server_id column if it doesn't exist
        if 'server_id' not in current_columns:
            print("Adding server_id column...")
            cursor.execute("ALTER TABLE proposals ADD COLUMN server_id INTEGER")
        
        # Check if we need to migrate data from guild_id to server_id
        if 'guild_id' in current_columns and 'server_id' in current_columns:
            print("Migrating data from guild_id to server_id...")
            cursor.execute("UPDATE proposals SET server_id = guild_id WHERE server_id IS NULL")
        
        # Define the expected columns based on db.py
        expected_columns = {
            'proposal_id': 'INTEGER PRIMARY KEY',
            'server_id': 'INTEGER NOT NULL',
            'proposer_id': 'INTEGER NOT NULL',
            'title': 'TEXT NOT NULL',
            'description': 'TEXT NOT NULL',
            'voting_mechanism': 'TEXT NOT NULL',
            'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'deadline': 'TIMESTAMP NOT NULL',
            'status': 'TEXT DEFAULT "Pending"',
            'requires_approval': 'BOOLEAN DEFAULT TRUE',
            'approved_by': 'INTEGER'
        }
        
        # Add missing columns
        for col_name, col_type in expected_columns.items():
            if col_name not in current_columns and col_name != 'proposal_id':  # Skip primary key
                print(f"Adding {col_name} column...")
                cursor.execute(f"ALTER TABLE proposals ADD COLUMN {col_name} {col_type}")
        
        # Migrate data from old column names to new ones if needed
        column_mappings = {
            'id': 'proposal_id',
            'guild_id': 'server_id',
            'proposal_text': 'description'
        }
        
        for old_col, new_col in column_mappings.items():
            if old_col in current_columns and new_col in current_columns and old_col != new_col:
                print(f"Migrating data from {old_col} to {new_col}...")
                cursor.execute(f"UPDATE proposals SET {new_col} = {old_col} WHERE {new_col} IS NULL")
        
        # Commit changes
        conn.commit()
        print("Database migration completed successfully")
        
        # Verify the changes
        cursor.execute("PRAGMA table_info(proposals)")
        updated_columns = cursor.fetchall()
        
        print("\nUpdated columns in proposals table:")
        for col in updated_columns:
            print(f"  - {col[1]} ({col[2]})")
        
        return True
    except Exception as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

async def create_new_proposals_table():
    """
    Create a new proposals table with the correct schema and migrate data from the old table.
    This is a more comprehensive approach if adding columns isn't sufficient.
    """
    print("\nCreating new proposals table with correct schema...")
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Rename the old table
        cursor.execute("ALTER TABLE proposals RENAME TO proposals_old")
        
        # Create the new table with the correct schema
        cursor.execute("""
        CREATE TABLE proposals (
            proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            proposer_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            voting_mechanism TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deadline TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'Pending',
            requires_approval BOOLEAN DEFAULT TRUE,
            approved_by INTEGER,
            FOREIGN KEY (server_id) REFERENCES servers(server_id)
        )
        """)
        
        # Check the old table's columns
        cursor.execute("PRAGMA table_info(proposals_old)")
        old_columns = {col[1]: col for col in cursor.fetchall()}
        
        # Map old column names to new ones
        column_mappings = {
            'id': 'proposal_id',
            'guild_id': 'server_id',
            'proposer_id': 'proposer_id',
            'proposal_text': 'description',
            'voting_mechanism': 'voting_mechanism',
            'created_at': 'created_at',
            'deadline': 'deadline',
            'status': 'status',
            'message_id': None  # This column is not in the new schema
        }
        
        # Prepare the migration query
        old_cols = []
        new_cols = []
        
        for old_col, new_col in column_mappings.items():
            if old_col in old_columns and new_col:
                old_cols.append(old_col)
                new_cols.append(new_col)
        
        # Add default values for missing columns
        if 'title' not in old_columns:
            new_cols.append('title')
            old_cols.append("'Untitled Proposal'")
        
        if 'requires_approval' not in old_columns:
            new_cols.append('requires_approval')
            old_cols.append('TRUE')
        
        # Migrate data
        if old_cols and new_cols:
            query = f"""
            INSERT INTO proposals ({', '.join(new_cols)})
            SELECT {', '.join(old_cols)} FROM proposals_old
            """
            cursor.execute(query)
            
            # Get the number of migrated rows
            cursor.execute("SELECT COUNT(*) FROM proposals")
            count = cursor.fetchone()[0]
            print(f"Migrated {count} proposals to the new table")
        
        # Commit changes
        conn.commit()
        print("New proposals table created successfully")
        
        return True
    except Exception as e:
        print(f"Error creating new table: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

async def verify_migration():
    """Verify that the migration was successful by checking the schema and data."""
    print("\nVerifying migration...")
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Check the schema
        cursor.execute("PRAGMA table_info(proposals)")
        columns = cursor.fetchall()
        
        # Verify required columns exist
        required_columns = ['proposal_id', 'server_id', 'title', 'description', 'voting_mechanism', 'deadline', 'status']
        missing_columns = [col for col in required_columns if col not in [c[1] for c in columns]]
        
        if missing_columns:
            print(f"Error: Missing columns: {', '.join(missing_columns)}")
            return False
        
        # Check if there's data in the table
        cursor.execute("SELECT COUNT(*) FROM proposals")
        count = cursor.fetchone()[0]
        print(f"Found {count} proposals in the table")
        
        # If there are proposals, check a sample
        if count > 0:
            cursor.execute("SELECT * FROM proposals LIMIT 1")
            sample = cursor.fetchone()
            column_names = [description[0] for description in cursor.description]
            
            print("\nSample proposal:")
            for i, col in enumerate(column_names):
                print(f"  {col}: {sample[i]}")
        
        print("\nMigration verification completed successfully")
        return True
    except Exception as e:
        print(f"Error verifying migration: {e}")
        return False
    finally:
        conn.close()

async def main():
    """Main function to run the database migration."""
    print("=== Database Migration Tool ===")
    
    # Backup the database
    backup_success = await backup_database()
    if not backup_success:
        print("Aborting migration due to backup failure")
        return
    
    # Check current schema
    await check_current_schema()
    
    # Ask for confirmation
    print("\nWARNING: This will modify your database schema. Make sure you have a backup.")
    proceed = input("Do you want to proceed with the migration? (y/n): ")
    
    if proceed.lower() != 'y':
        print("Migration aborted by user")
        return
    
    # Try the simple migration approach first
    migration_success = await migrate_database()
    
    # If that fails, try the more comprehensive approach
    if not migration_success:
        print("\nSimple migration failed. Trying comprehensive approach...")
        migration_success = await create_new_proposals_table()
    
    # Verify the migration
    if migration_success:
        verification_success = await verify_migration()
        if verification_success:
            print("\n✅ Database migration completed successfully!")
        else:
            print("\n❌ Migration verification failed. Please check the database manually.")
    else:
        print("\n❌ Database migration failed. Please restore from backup.")

if __name__ == "__main__":
    asyncio.run(main())
