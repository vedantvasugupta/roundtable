import sqlite3

# Database file path
DATABASE_FILE = "bot_database.db"

def add_proposal_id_column():
    """Add proposal_id column to the proposals table and migrate data from id."""
    print("Adding proposal_id column to proposals table...")
    
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    try:
        # Check if proposal_id column already exists
        cursor.execute("PRAGMA table_info(proposals)")
        columns = cursor.fetchall()
        
        proposal_id_exists = any(col[1] == 'proposal_id' for col in columns)
        
        if not proposal_id_exists:
            print("proposal_id column doesn't exist, adding it...")
            
            # Create a backup of the database
            print("Creating backup of database...")
            cursor.execute("BEGIN TRANSACTION")
            
            # Add proposal_id column
            cursor.execute("ALTER TABLE proposals ADD COLUMN proposal_id INTEGER")
            
            # Copy values from id to proposal_id
            cursor.execute("UPDATE proposals SET proposal_id = id")
            
            print("Data migrated from id to proposal_id")
            
            # Commit changes
            conn.commit()
            print("Changes committed to database")
        else:
            print("proposal_id column already exists")
        
        # Verify the changes
        cursor.execute("PRAGMA table_info(proposals)")
        columns = cursor.fetchall()
        
        print("\nUpdated columns in proposals table:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        
        # Check if data was migrated correctly
        cursor.execute("SELECT COUNT(*) FROM proposals WHERE id = proposal_id OR (id IS NULL AND proposal_id IS NULL)")
        count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM proposals")
        total = cursor.fetchone()[0]
        
        if count == total:
            print("\nAll data migrated correctly from id to proposal_id")
        else:
            print(f"\nWarning: Only {count} out of {total} rows have matching id and proposal_id values")
        
        print("\nDatabase schema updated successfully")
        
    except Exception as e:
        print(f"Error updating database schema: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    add_proposal_id_column()
