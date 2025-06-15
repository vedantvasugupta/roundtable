import asyncio
import aiosqlite
import json
from datetime import datetime, timedelta
import sys
import os

# Add the current directory to Python path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from proposals import CampaignControlView

class MockBot:
    def __init__(self):
        self.user = MockUser(12345, "TestBot")

class MockUser:
    def __init__(self, user_id, name):
        self.id = user_id
        self.name = name

class MockGuild:
    def __init__(self, guild_id):
        self.id = guild_id

async def test_enhanced_campaign_ui():
    """Test the enhanced campaign management UI functionality"""
    print("🧪 Testing Enhanced Campaign Management UI...")
    
    # Initialize database
    await db.init_db()
    print("✅ Database initialized")
    
    # Test 1: Create campaign and test initial view
    print("\n📝 Test 1: Creating campaign and testing initial UI state...")
    
    guild_id = 12345
    creator_id = 67890
    bot_instance = MockBot()
    
    campaign_id = await db.create_campaign(
        guild_id=guild_id,
        creator_id=creator_id,
        title="Enhanced UI Test Campaign",
        description="Testing the enhanced campaign management UI",
        total_tokens_per_voter=10,
        num_expected_scenarios=4
    )
    
    if campaign_id:
        print(f"✅ Created campaign C#{campaign_id}")
        
        # Approve the campaign to test setup state
        admin_id = 99999
        approved = await db.approve_campaign(campaign_id, admin_id)
        assert approved, "Failed to approve campaign"
        print(f"✅ Campaign C#{campaign_id} approved and in 'setup' state")
        
        # Test initial view state
        control_view = CampaignControlView(campaign_id, bot_instance)
        await control_view.rebuild_view()
        
        print(f"📊 View has {len(control_view.children)} buttons")
        
        # Should have 4 scenario buttons + 1 start button + 1 info button = 6 total
        expected_buttons = 4 + 1 + 1  # scenarios + start + info
        assert len(control_view.children) == expected_buttons, f"Expected {expected_buttons} buttons, got {len(control_view.children)}"
        
        # Check scenario buttons
        scenario_buttons = [btn for btn in control_view.children if 'scenario_' in btn.custom_id]
        assert len(scenario_buttons) == 4, f"Expected 4 scenario buttons, got {len(scenario_buttons)}"
        
        # First button should be active (Define S1), others should be locked
        first_button = control_view.scenario_buttons[1]
        assert not first_button.disabled, "First scenario button should be enabled"
        assert "Define S1" in first_button.label, f"Expected 'Define S1' in label, got '{first_button.label}'"
        
        for i in range(2, 5):
            button = control_view.scenario_buttons[i]
            assert button.disabled, f"Scenario {i} button should be disabled"
            assert "🔒" in button.label, f"Expected locked emoji in scenario {i} button"
        
        print("✅ Initial UI state is correct")
        
    else:
        print("❌ Failed to create campaign")
        return
    
    # Test 2: Create scenarios and test progressive UI updates
    print("\n📝 Test 2: Testing progressive scenario definition and UI updates...")
    
    deadline = (datetime.utcnow() + timedelta(days=7)).isoformat()
    hyperparameters = {"allow_abstain": True, "weight_mode": "equal"}
    
    # Create first scenario
    scenario_1_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Test Scenario 1",
        description="First scenario",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters=hyperparameters,
        campaign_id=campaign_id,
        scenario_order=1,
        initial_status="ApprovedScenario"
    )
    
    # Increment scenario count manually (normally done in _create_new_proposal_entry)
    await db.increment_defined_scenarios(campaign_id)
    
    print(f"✅ Created scenario 1: P#{scenario_1_id}")
    
    # Test view after first scenario
    await control_view.rebuild_view()
    
    # First button should now show ✅, second should be active
    first_button = control_view.scenario_buttons[1]
    assert first_button.disabled, "First scenario button should now be disabled"
    assert "S1 ✅" in first_button.label, f"Expected 'S1 ✅' in label, got '{first_button.label}'"
    
    second_button = control_view.scenario_buttons[2]
    assert not second_button.disabled, "Second scenario button should be enabled"
    assert "Define S2" in second_button.label, f"Expected 'Define S2' in label, got '{second_button.label}'"
    
    # Start button should now be enabled
    start_button = control_view.start_button
    assert start_button and not start_button.disabled, "Start button should be enabled after S1 is defined"
    assert "Start Campaign" in start_button.label, f"Expected 'Start Campaign' in label, got '{start_button.label}'"
    
    print("✅ UI correctly updated after first scenario definition")
    
    # Create second scenario  
    scenario_2_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Test Scenario 2",
        description="Second scenario",
        voting_mechanism="Borda",
        deadline=deadline,
        requires_approval=True,
        hyperparameters=hyperparameters,
        campaign_id=campaign_id,
        scenario_order=2,
        initial_status="ApprovedScenario"
    )
    
    await db.increment_defined_scenarios(campaign_id)
    print(f"✅ Created scenario 2: P#{scenario_2_id}")
    
    # Test view after second scenario
    await control_view.rebuild_view()
    
    # Second button should now show ✅, third should be active
    second_button = control_view.scenario_buttons[2]
    assert second_button.disabled, "Second scenario button should now be disabled"
    assert "S2 ✅" in second_button.label, f"Expected 'S2 ✅' in label, got '{second_button.label}'"
    
    third_button = control_view.scenario_buttons[3]
    assert not third_button.disabled, "Third scenario button should be enabled"
    assert "Define S3" in third_button.label, f"Expected 'Define S3' in label, got '{third_button.label}'"
    
    print("✅ UI correctly updated after second scenario definition")
    
    # Test 3: Test campaign activation and scenario status changes
    print("\n📝 Test 3: Testing campaign activation and voting states...")
    
    # Activate the campaign 
    await db.update_campaign_status(campaign_id, 'active')
    
    # Simulate scenario 1 going into voting
    await db.update_proposal_status(scenario_1_id, "Voting")
    
    # Test view with active voting
    await control_view.rebuild_view()
    
    # First button should show voting status
    first_button = control_view.scenario_buttons[1]
    assert "S1 🗳️" in first_button.label, f"Expected 'S1 🗳️' in label, got '{first_button.label}'"
    
    # Start button should show scenario is active
    start_button = control_view.start_button
    assert start_button and start_button.disabled, "Start button should be disabled when scenario is voting"
    assert "S1 Active" in start_button.label, f"Expected 'S1 Active' in label, got '{start_button.label}'"
    
    print("✅ UI correctly shows voting state")
    
    # Test 4: Test scenario completion and next scenario start
    print("\n📝 Test 4: Testing scenario completion flow...")
    
    # Complete scenario 1
    await db.update_proposal_status(scenario_1_id, "Closed")
    
    # Test view after scenario completion
    await control_view.rebuild_view()
    
    # First button should show closed status
    first_button = control_view.scenario_buttons[1]
    assert "S1 🏁" in first_button.label, f"Expected 'S1 🏁' in label, got '{first_button.label}'"
    
    # Start button should offer to start scenario 2
    start_button = control_view.start_button
    assert start_button and not start_button.disabled, "Start button should be enabled for next scenario"
    assert "Start Scenario 2" in start_button.label, f"Expected 'Start Scenario 2' in label, got '{start_button.label}'"
    
    print("✅ UI correctly handles scenario completion and progression")
    
    # Test 5: Test all scenarios defined state
    print("\n📝 Test 5: Testing all scenarios defined state...")
    
    # Create remaining scenarios
    for scenario_num in [3, 4]:
        scenario_id = await db.create_proposal(
            server_id=guild_id,
            proposer_id=creator_id,
            title=f"Test Scenario {scenario_num}",
            description=f"Scenario {scenario_num}",
            voting_mechanism="Approval",
            deadline=deadline,
            requires_approval=True,
            hyperparameters=hyperparameters,
            campaign_id=campaign_id,
            scenario_order=scenario_num,
            initial_status="ApprovedScenario"
        )
        await db.increment_defined_scenarios(campaign_id)
        print(f"✅ Created scenario {scenario_num}: P#{scenario_id}")
    
    # Test view with all scenarios defined
    await control_view.rebuild_view()
    
    # All scenario buttons should be defined/approved
    for i in range(1, 5):
        button = control_view.scenario_buttons[i]
        assert button.disabled, f"Scenario {i} button should be disabled when defined"
        assert "✅" in button.label or "🏁" in button.label, f"Scenario {i} should show status emoji"
    
    print("✅ UI correctly shows all scenarios defined")
    
    # Test 6: Test campaign completion
    print("\n📝 Test 6: Testing campaign completion state...")
    
    # Complete all remaining scenarios
    remaining_scenarios = await db.get_proposals_by_campaign_id(campaign_id, guild_id)
    for scenario in remaining_scenarios:
        if scenario['status'] in ['ApprovedScenario', 'Voting']:
            await db.update_proposal_status(scenario['proposal_id'], "Closed")
    
    await control_view.rebuild_view()
    
    # Start button should show completion
    start_button = control_view.start_button
    assert start_button and start_button.disabled, "Start button should be disabled when all scenarios complete"
    assert "All Scenarios Complete" in start_button.label, f"Expected completion message in start button, got '{start_button.label}'"
    
    print("✅ UI correctly shows campaign completion")
    
    print("\n🎉 All Enhanced Campaign UI tests passed!")
    
    # Summary
    print("\n📋 Test Summary:")
    print("✅ Initial UI state with locked progression")
    print("✅ Progressive scenario definition unlocking")
    print("✅ Dynamic button states and labels")
    print("✅ Campaign start button activation")
    print("✅ Voting state visualization")
    print("✅ Scenario completion handling")
    print("✅ Campaign completion state")
    print("✅ Proper button styling and emojis")

if __name__ == "__main__":
    asyncio.run(test_enhanced_campaign_ui())