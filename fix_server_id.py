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

def check_current_schema():
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

def fix_server_id_column():
    """
    Fix the server_id column issue by:
    1. Adding the server_id column if it doesn't exist
    2. Migrating data from guild_id to server_id
    """
    print("\nFixing server_id column issue...")
    
    # First, check the current schema
    current_columns = check_current_schema()
    
    # Create a connection to the database
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    try:
        # Add server_id column if it doesn't exist
        if 'server_id' not in current_columns:
            print("Adding server_id column...")
            cursor.execute("ALTER TABLE proposals ADD COLUMN server_id INTEGER")
            print("server_id column added successfully")
        else:
            print("server_id column already exists")
        
        # Check if we need to migrate data from guild_id to server_id
        if 'guild_id' in current_columns:
            print("Migrating data from guild_id to server_id...")
            cursor.execute("UPDATE proposals SET server_id = guild_id WHERE server_id IS NULL")
            print("Data migrated from guild_id to server_id")
        
        # Commit changes
        conn.commit()
        print("Database fix completed successfully")
        
        return True
    except Exception as e:
        print(f"Error during fix: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def verify_fix():
    """Verify that the fix was successful."""
    print("\nVerifying fix...")
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Check if server_id column exists
        cursor.execute("PRAGMA table_info(proposals)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'server_id' in columns:
            print("✅ server_id column exists in the proposals table")
        else:
            print("❌ server_id column is missing from the proposals table")
            return False
        
        # Check if data was migrated from guild_id to server_id
        if 'guild_id' in columns:
            cursor.execute("SELECT COUNT(*) FROM proposals WHERE guild_id IS NOT NULL AND server_id IS NULL")
            count = cursor.fetchone()[0]
            
            if count == 0:
                print("✅ All data successfully migrated from guild_id to server_id")
            else:
                print(f"❌ {count} rows have guild_id but not server_id")
                return False
        
        # Check if there's data in the server_id column
        cursor.execute("SELECT COUNT(*) FROM proposals")
        total_count = cursor.fetchone()[0]
        
        if total_count == 0:
            print("ℹ️ No proposals in the database to check")
        else:
            cursor.execute("SELECT COUNT(*) FROM proposals WHERE server_id IS NOT NULL")
            count = cursor.fetchone()[0]
            
            if count > 0:
                print(f"✅ {count} rows have data in the server_id column")
            else:
                print("❌ No data in the server_id column")
                return False
        
        print("\nFix verification completed successfully")
        return True
    except Exception as e:
        print(f"Error verifying fix: {e}")
        return False
    finally:
        conn.close()

def main():
    """Main function to run the database fix."""
    print("=== Server ID Column Fix Tool ===")
    
    # Backup the database
    backup_success = backup_database()
    if not backup_success:
        print("Aborting fix due to backup failure")
        return
    
    # Fix the server_id column
    fix_success = fix_server_id_column()
    
    # Verify the fix
    if fix_success:
        verification_success = verify_fix()
        if verification_success:
            print("\n✅ Database fix completed successfully!")
            print("\nNow you can run the !propose command without errors.")
            print("The code will use the server_id column for new proposals.")
        else:
            print("\n❌ Fix verification failed. Please check the database manually.")
    else:
        print("\n❌ Database fix failed. Please restore from backup.")

if __name__ == "__main__":
    main()
