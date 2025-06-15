import asyncio
import aiosqlite
import json
from datetime import datetime, timedelta
import sys
import os

# Add the current directory to Python path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import voting
from voting import send_voting_dm

async def test_complete_pipeline():
    """Test the complete proposal creation → approval → voting → results pipeline"""
    print("🧪 Starting comprehensive pipeline test...")
    
    # Initialize database
    await db.init_db()
    print("✅ Database initialized")
    
    # Test 1: Create a proposal with hyperparameters
    print("\n📝 Test 1: Creating proposal with hyperparameters...")
    
    hyperparameters = {
        "allow_abstain": True,
        "winning_threshold_percentage": 50
    }
    
    proposal_id = await db.create_proposal(
        server_id=12345,
        proposer_id=67890,
        title="Test Proposal for Pipeline",
        description="Testing the complete voting pipeline",
        voting_mechanism="plurality",
        deadline=(datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S.%f'),
        requires_approval=True,
        hyperparameters=hyperparameters,
        initial_status="Pending Approval"
    )
    
    if proposal_id:
        print(f"✅ Proposal created with ID: {proposal_id}")
    else:
        print("❌ Failed to create proposal")
        return False
    
    # Add proposal options
    options = ["Option A", "Option B", "Option C"]
    await db.add_proposal_options(proposal_id, options)
    print(f"✅ Added {len(options)} options to proposal")
    
    # Test 2: Retrieve proposal and verify hyperparameters
    print("\n🔍 Test 2: Retrieving proposal and checking hyperparameters...")
    
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        print("❌ Failed to retrieve proposal")
        return False
    
    print(f"✅ Retrieved proposal: {proposal['title']}")
    print(f"📊 Hyperparameters type: {type(proposal['hyperparameters'])}")
    print(f"📊 Hyperparameters content: {proposal['hyperparameters']}")
    
    # Verify hyperparameters are properly deserialized as dict
    if isinstance(proposal['hyperparameters'], dict):
        print("✅ Hyperparameters correctly deserialized as dictionary")
        if proposal['hyperparameters'].get('allow_abstain') == True:
            print("✅ Hyperparameter values preserved correctly")
        else:
            print("❌ Hyperparameter values not preserved correctly")
            return False
    else:
        print(f"❌ Hyperparameters not deserialized correctly. Type: {type(proposal['hyperparameters'])}")
        return False
    
    # Test 3: Simulate approval process
    print("\n👍 Test 3: Approving proposal...")
    
    await db.update_proposal_status(proposal_id, "Voting", approved_by=11111)
    updated_proposal = await db.get_proposal(proposal_id)
    
    if updated_proposal['status'] == 'Voting':
        print("✅ Proposal approved and status updated to 'Voting'")
    else:
        print(f"❌ Proposal status not updated correctly. Current: {updated_proposal['status']}")
        return False
    
    # Test 4: Test DM creation (mock member object)
    print("\n📩 Test 4: Testing DM creation with hyperparameters...")
    
    class MockMember:
        def __init__(self, user_id, name):
            self.id = user_id
            self.name = name
            self.dm_content = None
            
        async def send(self, embed=None, view=None):
            # Mock sending DM - just store the content for verification
            self.dm_content = {
                'embed': embed,
                'view': view,
                'hyperparameters_accessed': True
            }
            print(f"📧 Mock DM sent to {self.name}")
            return True
    
    mock_member = MockMember(67890, "TestUser")
    
    try:
        result = await send_voting_dm(mock_member, proposal, options)
        if result:
            print("✅ DM creation successful - hyperparameters.items() works!")
        else:
            print("❌ DM creation failed")
            return False
    except Exception as e:
        print(f"❌ DM creation failed with error: {e}")
        return False
    
    # Test 5: Test vote recording
    print("\n🗳️ Test 5: Testing vote recording...")
    
    vote_data = {"selected_option": "Option A"}
    vote_success = await db.record_vote(
        user_id=67890,
        proposal_id=proposal_id,
        vote_data=json.dumps(vote_data),
        is_abstain=False,
        tokens_invested=None
    )
    
    if vote_success:
        print("✅ Vote recorded successfully")
    else:
        print("❌ Failed to record vote")
        return False
    
    # Verify vote retrieval
    votes = await db.get_proposal_votes(proposal_id)
    if votes and len(votes) == 1:
        print(f"✅ Vote retrieved successfully. Vote data: {votes[0]['vote_data']}")
    else:
        print("❌ Failed to retrieve votes")
        return False
    
    # Test 6: Test proposal retrieval from lists
    print("\n📋 Test 6: Testing proposal retrieval from list functions...")
    
    server_proposals = await db.get_server_proposals(12345)
    found_in_server_list = any(p['proposal_id'] == proposal_id for p in server_proposals)
    
    if found_in_server_list:
        print("✅ Proposal found in server proposals list")
        # Verify hyperparameters in list
        test_proposal = next(p for p in server_proposals if p['proposal_id'] == proposal_id)
        if isinstance(test_proposal['hyperparameters'], dict):
            print("✅ Hyperparameters correctly deserialized in server proposals list")
        else:
            print("❌ Hyperparameters not correctly deserialized in server proposals list")
            return False
    else:
        print("❌ Proposal not found in server proposals list")
        return False
    
    status_proposals = await db.get_proposals_by_status("Voting")
    found_in_status_list = any(p['proposal_id'] == proposal_id for p in status_proposals)
    
    if found_in_status_list:
        print("✅ Proposal found in status-filtered proposals list")
    else:
        print("❌ Proposal not found in status-filtered proposals list")
        return False
    
    # Test 7: Test backward compatibility with double-encoded hyperparameters
    print("\n🔄 Test 7: Testing backward compatibility with double-encoded data...")
    
    # Manually insert a proposal with double-encoded hyperparameters
    double_encoded_hyperparams = json.dumps(json.dumps({"test_param": "test_value"}))
    
    async with db.get_db() as conn:
        cursor = await conn.execute(
            """INSERT INTO proposals 
               (server_id, proposer_id, title, description, voting_mechanism, 
                deadline, requires_approval, status, hyperparameters, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (12345, 67890, "Double Encoded Test", "Test description", "plurality",
             (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S.%f'),
             False, "Voting", double_encoded_hyperparams,
             datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f'),
             datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f'))
        )
        await conn.commit()
        double_encoded_proposal_id = cursor.lastrowid
    
    # Retrieve and verify double-encoded proposal is handled correctly
    double_proposal = await db.get_proposal(double_encoded_proposal_id)
    if isinstance(double_proposal['hyperparameters'], dict):
        print("✅ Double-encoded hyperparameters correctly handled")
        if double_proposal['hyperparameters'].get('test_param') == 'test_value':
            print("✅ Double-encoded hyperparameter values preserved correctly")
        else:
            print("❌ Double-encoded hyperparameter values not preserved correctly")
            return False
    else:
        print(f"❌ Double-encoded hyperparameters not handled correctly. Type: {type(double_proposal['hyperparameters'])}")
        return False
    
    print("\n🎉 All tests passed! Complete pipeline working correctly.")
    print(f"✅ Hyperparameters serialization fix successful")
    print(f"✅ DM sending no longer crashes with 'str' object has no attribute 'items'")
    print(f"✅ Backward compatibility maintained for existing double-encoded data")
    
    return True

async def main():
    """Run the complete pipeline test"""
    try:
        success = await test_complete_pipeline()
        if success:
            print("\n🏆 COMPLETE PIPELINE TEST: PASSED")
        else:
            print("\n💥 COMPLETE PIPELINE TEST: FAILED")
        return success
    except Exception as e:
        print(f"\n💥 COMPLETE PIPELINE TEST: FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    asyncio.run(main())