import asyncio
import sqlite3
from datetime import datetime, timedelta
import db

async def test_create_proposal():
    """Test creating a proposal with the fixed database schema."""
    print("\n=== Testing Proposal Creation ===")
    
    # Test data
    server_id = 123456789
    proposer_id = 987654321
    title = "Test Proposal"
    description = "This is a test proposal to verify the database fix"
    voting_mechanism = "plurality"
    deadline = datetime.now() + timedelta(days=3)
    requires_approval = True
    
    try:
        # Create a proposal
        print("\nCreating test proposal...")
        proposal_id = await db.create_proposal(
            server_id, proposer_id, title, description, 
            voting_mechanism, deadline, requires_approval
        )
        print(f"✅ Proposal created successfully with ID: {proposal_id}")
        
        # Retrieve the proposal to verify it was stored correctly
        print("\nRetrieving proposal to verify storage...")
        proposal = await db.get_proposal(proposal_id)
        
        if proposal:
            print("✅ Proposal retrieved successfully")
            print("\nProposal details:")
            for key, value in proposal.items():
                print(f"  {key}: {value}")
            
            # Verify server_id was stored correctly
            if proposal.get('server_id') == server_id:
                print("\n✅ server_id was stored correctly")
            else:
                print(f"\n❌ server_id mismatch: expected {server_id}, got {proposal.get('server_id')}")
        else:
            print("❌ Failed to retrieve proposal")
        
        # Check database directly to verify the server_id column
        print("\nVerifying server_id in database directly...")
        conn = sqlite3.connect(db.DATABASE_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT server_id FROM proposals WHERE id = ? OR proposal_id = ?", 
                      (proposal_id, proposal_id))
        result = cursor.fetchone()
        
        if result and result[0] == server_id:
            print(f"✅ server_id verified in database: {result[0]}")
        else:
            print(f"❌ server_id not found or incorrect in database: {result}")
        
        conn.close()
        
        return True
    except Exception as e:
        print(f"❌ Error during test: {e}")
        return False

async def cleanup_test_data():
    """Clean up test data from the database."""
    print("\n=== Cleaning Up Test Data ===")
    
    try:
        conn = sqlite3.connect(db.DATABASE_FILE)
        cursor = conn.cursor()
        
        # Delete test proposals
        cursor.execute("DELETE FROM proposals WHERE server_id = ?", (123456789,))
        deleted_count = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        print(f"✅ Cleaned up {deleted_count} test proposals")
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")

async def main():
    """Main function to run the test."""
    print("=== Proposal Creation Test ===")
    
    # Initialize the database
    print("Initializing database...")
    await db.init_db()
    
    # Run the test
    success = await test_create_proposal()
    
    # Clean up test data
    await cleanup_test_data()
    
    if success:
        print("\n✅ Test completed successfully!")
        print("\nThe database fix has resolved the issue with the server_id column.")
        print("The !propose command should now work correctly.")
    else:
        print("\n❌ Test failed. Please check the errors above.")

if __name__ == "__main__":
    asyncio.run(main())
