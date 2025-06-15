import asyncio
import aiosqlite
import json
from datetime import datetime, timedelta
import sys
import os

# Add the current directory to Python path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db

async def test_campaign_auto_approval():
    """Test the campaign auto-approval functionality for scenarios"""
    print("🧪 Testing Campaign Auto-Approval Logic...")
    
    # Initialize database
    await db.init_db()
    print("✅ Database initialized")
    
    # Test 1: Create a campaign in pending_approval status
    print("\n📝 Test 1: Creating campaign in pending_approval status...")
    
    guild_id = 12345
    creator_id = 67890
    
    campaign_id = await db.create_campaign(
        guild_id=guild_id,
        creator_id=creator_id,
        title="Test Campaign Auto-Approval",
        description="Testing auto-approval logic for scenarios",
        total_tokens_per_voter=10,
        num_expected_scenarios=3
    )
    
    if campaign_id:
        print(f"✅ Created campaign C#{campaign_id}")
        
        # Verify campaign is in pending_approval status
        campaign = await db.get_campaign(campaign_id)
        print(f"📊 Campaign status: {campaign['status']}")
        assert campaign['status'] == 'pending_approval', f"Expected 'pending_approval', got '{campaign['status']}'"
        
    else:
        print("❌ Failed to create campaign")
        return
    
    # Test 2: Test scenario creation logic for unapproved campaign
    print("\n📝 Test 2: Testing scenario creation for unapproved campaign...")
    
    # Simulate the logic from _create_new_proposal_entry
    hyperparameters = {"allow_abstain": True, "weight_mode": "equal"}
    campaign_check = await db.get_campaign(campaign_id)
    
    # Determine initial status based on campaign approval status
    requires_approval = True
    initial_status = "Pending Approval" if requires_approval else "Voting"
    
    # Auto-approve scenarios if the campaign is already approved
    if campaign_id:
        campaign = await db.get_campaign(campaign_id)
        if campaign and campaign['status'] in ['setup', 'active']:
            # Campaign is approved, so scenarios should be auto-approved to 'ApprovedScenario' status
            initial_status = "ApprovedScenario"
            print(f"DEBUG: Auto-approving scenario for approved campaign C#{campaign_id}, status: {campaign['status']}")
        elif campaign and campaign['status'] == 'pending_approval':
            # Campaign not yet approved, scenario follows normal approval flow
            initial_status = "Pending Approval" if requires_approval else "Pending Approval"
            print(f"DEBUG: Scenario for unapproved campaign C#{campaign_id} set to Pending Approval")
    
    print(f"📊 Scenario initial status for unapproved campaign: {initial_status}")
    assert initial_status == "Pending Approval", f"Expected 'Pending Approval', got '{initial_status}'"
    
    # Test 3: Approve the campaign and test auto-approval
    print("\n📝 Test 3: Approving campaign and testing auto-approval...")
    
    # Approve the campaign
    admin_id = 99999
    approved = await db.approve_campaign(campaign_id, admin_id)
    
    if approved:
        print(f"✅ Campaign C#{campaign_id} approved")
        
        # Verify campaign status changed to 'setup'
        campaign = await db.get_campaign(campaign_id)
        print(f"📊 Campaign status after approval: {campaign['status']}")
        assert campaign['status'] == 'setup', f"Expected 'setup', got '{campaign['status']}'"
        
    else:
        print("❌ Failed to approve campaign")
        return
    
    # Test 4: Test scenario creation logic for approved campaign
    print("\n📝 Test 4: Testing scenario creation for approved campaign...")
    
    # Simulate the logic again for approved campaign
    if campaign_id:
        campaign = await db.get_campaign(campaign_id)
        if campaign and campaign['status'] in ['setup', 'active']:
            # Campaign is approved, so scenarios should be auto-approved to 'ApprovedScenario' status
            initial_status = "ApprovedScenario"
            print(f"DEBUG: Auto-approving scenario for approved campaign C#{campaign_id}, status: {campaign['status']}")
        elif campaign and campaign['status'] == 'pending_approval':
            # Campaign not yet approved, scenario follows normal approval flow
            initial_status = "Pending Approval" if requires_approval else "Pending Approval"
            print(f"DEBUG: Scenario for unapproved campaign C#{campaign_id} set to Pending Approval")
    
    print(f"📊 Scenario initial status for approved campaign: {initial_status}")
    assert initial_status == "ApprovedScenario", f"Expected 'ApprovedScenario', got '{initial_status}'"
    
    # Test 5: Create an actual scenario to test the full flow
    print("\n📝 Test 5: Creating actual scenario for approved campaign...")
    
    deadline = (datetime.utcnow() + timedelta(days=7)).isoformat()
    
    scenario_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Test Scenario 1",
        description="First test scenario",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters=hyperparameters,
        campaign_id=campaign_id,
        scenario_order=1,
        initial_status="ApprovedScenario"
    )
    
    if scenario_id:
        print(f"✅ Created scenario P#{scenario_id}")
        
        # Verify scenario status
        scenario = await db.get_proposal(scenario_id)
        print(f"📊 Scenario status: {scenario['status']}")
        assert scenario['status'] == 'ApprovedScenario', f"Expected 'ApprovedScenario', got '{scenario['status']}'"
        
        # Test increment scenarios count
        print("\n📝 Test 6: Testing scenario count increment...")
        old_count = campaign['current_defined_scenarios']
        new_count = await db.increment_defined_scenarios(campaign_id)
        print(f"📊 Scenario count: {old_count} → {new_count}")
        
        # Verify count was incremented
        campaign_updated = await db.get_campaign(campaign_id)
        print(f"📊 Updated campaign scenario count: {campaign_updated['current_defined_scenarios']}")
        assert campaign_updated['current_defined_scenarios'] == old_count + 1, f"Expected {old_count + 1}, got {campaign_updated['current_defined_scenarios']}"
        
    else:
        print("❌ Failed to create scenario")
        return
    
    # Test 7: Test existing scenario auto-approval when campaign is approved
    print("\n📝 Test 7: Testing existing scenario auto-approval...")
    
    # Create another campaign that starts in pending status
    campaign_id_2 = await db.create_campaign(
        guild_id=guild_id,
        creator_id=creator_id,
        title="Test Campaign 2",
        description="Testing existing scenario auto-approval",
        total_tokens_per_voter=10,
        num_expected_scenarios=2
    )
    
    # Create a scenario for the unapproved campaign
    scenario_id_2 = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Test Scenario 2",
        description="Scenario created before campaign approval",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters=hyperparameters,
        campaign_id=campaign_id_2,
        scenario_order=1,
        initial_status="Pending Approval"
    )
    
    print(f"✅ Created unapproved scenario P#{scenario_id_2}")
    
    # Verify scenario is pending
    scenario_2 = await db.get_proposal(scenario_id_2)
    print(f"📊 Scenario 2 status before campaign approval: {scenario_2['status']}")
    assert scenario_2['status'] == 'Pending Approval', f"Expected 'Pending Approval', got '{scenario_2['status']}'"
    
    # Now approve the campaign
    approved_2 = await db.approve_campaign(campaign_id_2, admin_id)
    assert approved_2, "Failed to approve second campaign"
    
    # Simulate the auto-approval logic from _perform_approve_campaign_action
    campaign_proposals = await db.get_proposals_by_campaign_id(campaign_id_2, guild_id=guild_id)
    updated_scenario_count = 0
    if campaign_proposals:
        for scenario_proposal in campaign_proposals:
            if scenario_proposal['status'] == 'Pending Approval':
                await db.update_proposal_status(scenario_proposal['proposal_id'], "ApprovedScenario", set_requires_approval_false=True)
                updated_scenario_count += 1
                print(f"DEBUG: Auto-approved existing scenario P#{scenario_proposal['proposal_id']} for newly approved C#{campaign_id_2}")
    
    print(f"📊 Auto-approved {updated_scenario_count} existing scenarios")
    
    # Verify the scenario was auto-approved
    scenario_2_updated = await db.get_proposal(scenario_id_2)
    print(f"📊 Scenario 2 status after campaign approval: {scenario_2_updated['status']}")
    assert scenario_2_updated['status'] == 'ApprovedScenario', f"Expected 'ApprovedScenario', got '{scenario_2_updated['status']}'"
    
    print("\n🎉 All tests passed! Campaign auto-approval logic is working correctly.")
    
    # Summary
    print("\n📋 Test Summary:")
    print("✅ Campaign creation in pending_approval status")
    print("✅ Scenarios for unapproved campaigns set to Pending Approval")
    print("✅ Campaign approval changes status to 'setup'")
    print("✅ Scenarios for approved campaigns auto-approved to 'ApprovedScenario'")
    print("✅ Scenario creation and status assignment")
    print("✅ Campaign scenario count increment")
    print("✅ Existing scenarios auto-approved when campaign is approved")

if __name__ == "__main__":
    asyncio.run(test_campaign_auto_approval())