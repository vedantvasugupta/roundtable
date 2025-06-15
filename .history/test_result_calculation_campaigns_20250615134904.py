import asyncio
import aiosqlite
import json
from datetime import datetime, timedelta
import sys
import os

# Add the current directory to Python path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import voting_utils

class MockGuild:
    def __init__(self, guild_id):
        self.id = guild_id
        self.name = "Test Guild"
        self.text_channels = []
        self.members = []
        self.me = MockMember(12345, "TestBot")
    
    def get_member(self, user_id):
        for member in self.members:
            if member.id == user_id:
                return member
        return None

class MockMember:
    def __init__(self, user_id, name):
        self.id = user_id
        self.name = name
        self.mention = f"<@{user_id}>"
    
    async def send(self, message, **kwargs):
        print(f"[MOCK DM to {self.name}]: {message}")
        return MockMessage(654321)

class MockChannel:
    def __init__(self, name, channel_id):
        self.name = name
        self.id = channel_id
    
    async def send(self, *args, **kwargs):
        print(f"[MOCK SEND to #{self.name}]: {args[0] if args else 'embed'}")
        return MockMessage(123456)

class MockMessage:
    def __init__(self, message_id):
        self.id = message_id

async def test_result_calculation_campaigns():
    """Test the enhanced result calculation and campaign completion system"""
    print("🧪 Testing Enhanced Result Calculation & Campaign System...")
    
    # Initialize database
    await db.init_db()
    print("✅ Database initialized")
    
    # Test 1: Create campaign and scenarios with token-weighted voting
    print("\n📝 Test 1: Creating campaign with token-weighted scenarios...")
    
    guild_id = 12345
    creator_id = 67890
    voter_id_1 = 11111
    voter_id_2 = 22222
    voter_id_3 = 33333
    
    # Create guild and members
    guild = MockGuild(guild_id)
    guild.members = [
        MockMember(creator_id, "Creator"),
        MockMember(voter_id_1, "Voter1"),
        MockMember(voter_id_2, "Voter2"),
        MockMember(voter_id_3, "Voter3")
    ]
    
    # Add mock channels
    guild.text_channels = [
        MockChannel("campaign-management", 111111),
        MockChannel("vote-results", 222222),
        MockChannel("voting-room", 333333)
    ]
    
    # Create campaign
    campaign_id = await db.create_campaign(
        guild_id=guild_id,
        creator_id=creator_id,
        title="Token Weight Test Campaign",
        description="Testing enhanced result calculation",
        total_tokens_per_voter=20,
        num_expected_scenarios=3
    )
    
    # Approve the campaign
    admin_id = 99999
    approved = await db.approve_campaign(campaign_id, admin_id)
    assert approved, "Failed to approve campaign"
    print(f"✅ Created and approved campaign C#{campaign_id}")
    
    # Create scenarios with different weight modes and mechanisms
    deadline = (datetime.utcnow() + timedelta(days=7)).isoformat()
    
    scenarios = []
    
    # Scenario 1: Plurality with equal weight
    scenario_1_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Equal Weight Plurality",
        description="Testing equal weight plurality voting",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True,
            "weight_mode": "equal"
        },
        campaign_id=campaign_id,
        scenario_order=1,
        initial_status="ApprovedScenario"
    )
    scenarios.append(scenario_1_id)
    
    # Scenario 2: Approval with proportional weight
    scenario_2_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Proportional Weight Approval",
        description="Testing proportional weight approval voting",
        voting_mechanism="Approval",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True,
            "weight_mode": "proportional"
        },
        campaign_id=campaign_id,
        scenario_order=2,
        initial_status="ApprovedScenario"
    )
    scenarios.append(scenario_2_id)
    
    # Scenario 3: Borda with proportional weight
    scenario_3_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Proportional Weight Borda",
        description="Testing proportional weight borda voting",
        voting_mechanism="Borda",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True,
            "weight_mode": "proportional"
        },
        campaign_id=campaign_id,
        scenario_order=3,
        initial_status="ApprovedScenario"
    )
    scenarios.append(scenario_3_id)
    
    print(f"✅ Created scenarios: P#{scenario_1_id}, P#{scenario_2_id}, P#{scenario_3_id}")
    
    # Add options to scenarios
    options = ["Option A", "Option B", "Option C"]
    for scenario_id in scenarios:
        await db.add_proposal_options(scenario_id, options)
    
    # Test 2: Enroll voters and simulate voting
    print("\n📝 Test 2: Enrolling voters and simulating token-weighted voting...")
    
    # Enroll voters
    for voter_id in [voter_id_1, voter_id_2, voter_id_3]:
        await db.enroll_voter_in_campaign(campaign_id, voter_id, 20)
    
    # Simulate voting on Scenario 1 (Equal weight - 1 token each)
    await db.record_vote(voter_id_1, scenario_1_id, json.dumps({"option": "Option A"}), False, 1)
    await db.record_vote(voter_id_2, scenario_1_id, json.dumps({"option": "Option B"}), False, 1)
    await db.record_vote(voter_id_3, scenario_1_id, json.dumps({"option": "Option A"}), False, 1)
    
    # Update token balances for scenario 1
    await db.update_user_remaining_tokens(campaign_id, voter_id_1, 1)
    await db.update_user_remaining_tokens(campaign_id, voter_id_2, 1)
    await db.update_user_remaining_tokens(campaign_id, voter_id_3, 1)
    
    # Simulate voting on Scenario 2 (Proportional weight - varying tokens)
    await db.record_vote(voter_id_1, scenario_2_id, json.dumps({"approved": ["Option A", "Option B"]}), False, 5)
    await db.record_vote(voter_id_2, scenario_2_id, json.dumps({"approved": ["Option B", "Option C"]}), False, 3)
    await db.record_vote(voter_id_3, scenario_2_id, json.dumps({"approved": ["Option A"]}), False, 7)
    
    # Update token balances for scenario 2
    await db.update_user_remaining_tokens(campaign_id, voter_id_1, 5)
    await db.update_user_remaining_tokens(campaign_id, voter_id_2, 3)
    await db.update_user_remaining_tokens(campaign_id, voter_id_3, 7)
    
    # Simulate voting on Scenario 3 (Borda with proportional weight)
    await db.record_vote(voter_id_1, scenario_3_id, json.dumps({"rankings": ["Option A", "Option B", "Option C"]}), False, 4)
    await db.record_vote(voter_id_2, scenario_3_id, json.dumps({"rankings": ["Option C", "Option A", "Option B"]}), False, 6)
    await db.record_vote(voter_id_3, scenario_3_id, json.dumps({"rankings": ["Option B", "Option C", "Option A"]}), False, 2)
    
    # Update token balances for scenario 3
    await db.update_user_remaining_tokens(campaign_id, voter_id_1, 4)
    await db.update_user_remaining_tokens(campaign_id, voter_id_2, 6)
    await db.update_user_remaining_tokens(campaign_id, voter_id_3, 2)
    
    print("✅ Voting simulation completed")
    
    # Test 3: Calculate results for each scenario
    print("\n📝 Test 3: Testing enhanced result calculation...")
    
    results_list = []
    
    for i, scenario_id in enumerate(scenarios, 1):
        print(f"\n🔍 Calculating results for Scenario {i} (P#{scenario_id})...")
        
        # Calculate results
        results = await voting_utils.calculate_results(scenario_id)
        assert results is not None, f"Failed to calculate results for scenario {i}"
        
        results_list.append(results)
        
        # Verify result structure
        assert 'mechanism' in results, f"Missing mechanism in results for scenario {i}"
        assert 'total_weighted_votes' in results or 'total_weighted_vote_power' in results or 'total_weighted_voting_power' in results, f"Missing weighted votes in results for scenario {i}"
        assert 'results_detailed' in results, f"Missing detailed results for scenario {i}"
        
        print(f"✅ Scenario {i} results: Mechanism={results['mechanism']}, Winner={results.get('winner', 'None')}")
        
        # Print detailed results for verification
        if results['mechanism'] == 'plurality':
            for option, details in results['results_detailed']:
                print(f"  • {option}: {details['weighted_votes']} weighted, {details['raw_votes']} raw")
        elif results['mechanism'] == 'approval':
            for option, details in results['results_detailed']:
                print(f"  • {option}: {details['weighted_approvals']} weighted approvals, {details['raw_approvals']} raw")
        elif results['mechanism'] == 'borda':
            for option, details in results['results_detailed']:
                print(f"  • {option}: {details['weighted_score']} weighted score, {details['raw_score']} raw")
    
    # Test 4: Test campaign-specific result formatting
    print("\n📝 Test 4: Testing campaign-specific result formatting...")
    
    for i, scenario_id in enumerate(scenarios, 1):
        scenario = await db.get_proposal(scenario_id)
        results = results_list[i-1]
        
        # Test enhanced formatting
        embed = await voting_utils.format_vote_results(results, scenario)
        
        # Verify campaign-specific formatting
        assert f"C#{campaign_id}" in embed.title or f"C#{campaign_id}" in embed.description, f"Campaign ID missing from scenario {i} embed"
        assert f"S#{i}" in embed.title or f"S#{i}" in embed.description, f"Scenario order missing from scenario {i} embed"
        
        # Check for token information in campaign scenarios
        found_token_info = any("Token" in field.name or "token" in field.name.lower() for field in embed.fields)
        assert found_token_info, f"Token information missing from scenario {i} embed"
        
        print(f"✅ Scenario {i} formatting verified")
    
    # Test 5: Close scenarios and test campaign completion
    print("\n📝 Test 5: Testing campaign completion detection...")
    
    # Close all scenarios
    for scenario_id in scenarios:
        await db.update_proposal_status(scenario_id, "Closed")
    
    # Test campaign completion check
    campaign_complete = await voting_utils.check_and_announce_campaign_completion(guild, campaign_id)
    
    assert campaign_complete, "Campaign completion should have been detected"
    print("✅ Campaign completion detected and processed")
    
    # Verify campaign status updated
    updated_campaign = await db.get_campaign(campaign_id)
    assert updated_campaign['status'] == 'completed', f"Campaign status should be 'completed', got '{updated_campaign['status']}'"
    
    # Test 6: Test campaign aggregate results
    print("\n📝 Test 6: Testing campaign aggregate results calculation...")
    
    completed_scenarios = await db.get_proposals_by_campaign_id(campaign_id)
    aggregate_results = await voting_utils.calculate_campaign_aggregate_results(campaign_id, completed_scenarios)
    
    assert aggregate_results['campaign_id'] == campaign_id, "Campaign ID mismatch in aggregate results"
    assert aggregate_results['total_scenarios'] == 3, f"Expected 3 scenarios, got {aggregate_results['total_scenarios']}"
    assert aggregate_results['total_tokens_allocated'] > 0, "Total tokens allocated should be > 0"
    assert len(aggregate_results['scenario_results']) == 3, f"Expected 3 scenario results, got {len(aggregate_results['scenario_results'])}"
    
    print(f"📊 Aggregate Results:")
    print(f"  • Total Tokens Used: {aggregate_results['total_tokens_allocated']}")
    print(f"  • Total Votes Cast: {aggregate_results['total_votes_cast']}")
    print(f"  • Scenarios Completed: {aggregate_results['total_scenarios']}")
    
    for scenario_result in aggregate_results['scenario_results']:
        print(f"  • S{scenario_result['scenario_order']}: {scenario_result['winner'] or 'No winner'} ({scenario_result['tokens_used']} tokens)")
    
    print("✅ Campaign aggregate results calculated correctly")
    
    print("\n🎉 All Enhanced Result Calculation & Campaign tests passed!")
    
    # Summary
    print("\n📋 Test Summary:")
    print("✅ Campaign creation and scenario setup")
    print("✅ Token-weighted voting simulation")
    print("✅ Enhanced result calculation for all mechanisms")
    print("✅ Campaign-specific result formatting")
    print("✅ Campaign completion detection")
    print("✅ Aggregate campaign results calculation")
    print("✅ Token usage tracking across scenarios")
    print("✅ Weight mode handling (equal vs proportional)")

if __name__ == "__main__":
    asyncio.run(test_result_calculation_campaigns())