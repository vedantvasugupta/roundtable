import sqlite3

# Database file path
DATABASE_FILE = "bot_database.db"

def check_schema():
    """Check if the database schema has the server_id column."""
    print("Checking database schema...")
    
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Check if server_id column exists in proposals table
    cursor.execute("PRAGMA table_info(proposals)")
    columns = cursor.fetchall()
    
    print("Columns in proposals table:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    # Check if server_id and guild_id columns exist
    server_id_exists = any(col[1] == 'server_id' for col in columns)
    guild_id_exists = any(col[1] == 'guild_id' for col in columns)
    
    print(f"\nserver_id column exists: {server_id_exists}")
    print(f"guild_id column exists: {guild_id_exists}")
    
    # Try to insert a test row
    if server_id_exists and guild_id_exists:
        print("\nTrying to insert a test row...")
        try:
            cursor.execute(
                """
                INSERT INTO proposals 
                (server_id, guild_id, proposer_id, title, description, proposal_text, voting_mechanism, deadline, status) 
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+3 days'), ?)
                """,
                (123456789, 123456789, 987654321, "Test Title", "Test Description", "Test Description", "plurality", "Pending")
            )
            proposal_id = cursor.lastrowid
            print(f"Test row inserted with ID: {proposal_id}")
            
            # Verify the row was inserted correctly
            cursor.execute("SELECT server_id, guild_id FROM proposals WHERE rowid = ?", (proposal_id,))
            result = cursor.fetchone()
            if result:
                server_id, guild_id = result
                print(f"Inserted values: server_id={server_id}, guild_id={guild_id}")
                
                if server_id == 123456789 and guild_id == 123456789:
                    print("Both server_id and guild_id were stored correctly")
                else:
                    print("Column values don't match expected values")
            else:
                print("Failed to retrieve the inserted row")
            
            # Clean up the test row
            cursor.execute("DELETE FROM proposals WHERE rowid = ?", (proposal_id,))
            print(f"Test row cleaned up")
            
            conn.commit()
        except Exception as e:
            print(f"Error during test: {e}")
    
    conn.close()

if __name__ == "__main__":
    check_schema()
