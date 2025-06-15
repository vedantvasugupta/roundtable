import sqlite3

# Database file path
DATABASE_FILE = "bot_database.db"

def verify_server_id_column():
    """Verify that the server_id column exists in the proposals table."""
    print("Verifying server_id column in proposals table...")
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Check if server_id column exists
        cursor.execute("PRAGMA table_info(proposals)")
        columns = cursor.fetchall()
        
        print("Columns in proposals table:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        
        # Check if server_id column exists
        server_id_exists = any(col[1] == 'server_id' for col in columns)
        
        if server_id_exists:
            print("\n✅ server_id column exists in the proposals table")
            
            # Try to insert a test proposal
            print("\nInserting a test proposal...")
            cursor.execute("""
                INSERT INTO proposals (server_id, guild_id, proposer_id, title, description, proposal_text, voting_mechanism, deadline, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+3 days'), ?)
            """, (123456789, 123456789, 987654321, "Test Proposal", "Test Description", "Test Description", "plurality", "Pending"))
            
            proposal_id = cursor.lastrowid
            print(f"✅ Test proposal inserted with ID: {proposal_id}")
            
            # Verify the proposal was inserted correctly
            cursor.execute("SELECT * FROM proposals WHERE rowid = ?", (proposal_id,))
            proposal = cursor.fetchone()
            
            if proposal:
                column_names = [description[0] for description in cursor.description]
                print("\nProposal details:")
                for i, col in enumerate(column_names):
                    print(f"  {col}: {proposal[i]}")
                
                # Find the index of the server_id column
                server_id_index = column_names.index('server_id') if 'server_id' in column_names else -1
                
                if server_id_index >= 0 and proposal[server_id_index] == 123456789:
                    print("\n✅ server_id was stored correctly")
                else:
                    print("\n❌ server_id was not stored correctly")
            else:
                print("\n❌ Failed to retrieve the test proposal")
            
            # Clean up the test proposal
            cursor.execute("DELETE FROM proposals WHERE rowid = ?", (proposal_id,))
            print(f"\n✅ Test proposal cleaned up")
            
            conn.commit()
        else:
            print("\n❌ server_id column does not exist in the proposals table")
        
        conn.close()
        
        return server_id_exists
    except Exception as e:
        print(f"\n❌ Error during verification: {e}")
        return False

if __name__ == "__main__":
    print("=== Server ID Column Verification ===\n")
    
    if verify_server_id_column():
        print("\n✅ Verification completed successfully!")
        print("The database fix has resolved the issue with the server_id column.")
        print("The !propose command should now work correctly.")
    else:
        print("\n❌ Verification failed. The server_id column is missing or not working correctly.")
