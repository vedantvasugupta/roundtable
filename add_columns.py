import sqlite3
import shutil
from datetime import datetime

# Database file path
DATABASE_FILE = "bot_database.db"

def backup_database():
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

def add_columns():
    """Add missing columns to proposals table"""
    print("\nAdding missing columns to proposals table...")
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Check current columns
        cursor.execute("PRAGMA table_info(proposals)")
        columns = [col[1] for col in cursor.fetchall()]
        
        # Add missing columns
        if 'guild_id' not in columns:
            print("Adding guild_id column...")
            cursor.execute("ALTER TABLE proposals ADD COLUMN guild_id INTEGER")
            print("guild_id column added successfully")
            
            # Set guild_id equal to server_id for all existing rows
            print("Setting guild_id = server_id for all existing proposals...")
            cursor.execute("UPDATE proposals SET guild_id = server_id")
            print(f"Updated guild_id for {cursor.rowcount} rows")
        else:
            print("guild_id column already exists")
            
        if 'proposal_text' not in columns:
            print("Adding proposal_text column...")
            cursor.execute("ALTER TABLE proposals ADD COLUMN proposal_text TEXT")
            print("proposal_text column added successfully")
            
            # Set proposal_text equal to description for all existing rows
            print("Setting proposal_text = description for all existing proposals...")
            cursor.execute("UPDATE proposals SET proposal_text = description")
            print(f"Updated proposal_text for {cursor.rowcount} rows")
        else:
            print("proposal_text column already exists")
            
        # Ensure id column exists
        if 'id' not in columns:
            print("Adding id column...")
            cursor.execute("ALTER TABLE proposals ADD COLUMN id INTEGER")
            print("id column added successfully")
            
            # Set id equal to proposal_id for all existing rows
            print("Setting id = proposal_id for all existing proposals...")
            cursor.execute("UPDATE proposals SET id = proposal_id")
            print(f"Updated id for {cursor.rowcount} rows")
        else:
            print("id column already exists")
        
        # Commit changes
        conn.commit()
        conn.close()
        
        print("Database update completed successfully")
        return True
    except Exception as e:
        print(f"Error during column addition: {e}")
        return False

def main():
    """Main function to add missing columns."""
    print("=== Adding Missing Columns ===")
    
    # Backup the database
    backup_success = backup_database()
    if not backup_success:
        print("Aborting due to backup failure")
        return
    
    # Add columns
    success = add_columns()
    
    if success:
        print("\n✅ Database update completed successfully!")
    else:
        print("\n❌ Database update failed. Please restore from backup.")

if __name__ == "__main__":
    main()