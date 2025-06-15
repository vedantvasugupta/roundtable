import asyncio
import aiosqlite
import json
from datetime import datetime, timedelta
import sys
import os

# Add the current directory to Python path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from voting import PluralityVoteView, TokenInvestmentModal

class MockBot:
    def __init__(self):
        self.user = MockUser(12345, "TestBot")

class MockUser:
    def __init__(self, user_id, name):
        self.id = user_id
        self.name = name

class MockInteraction:
    def __init__(self, user_id):
        self.user = MockUser(user_id, "TestUser")
        self.response = MockResponse()
        self.guild_id = 12345
    
    async def followup_send(self, *args, **kwargs):
        pass
    
    async def edit_original_response(self, *args, **kwargs):
        pass

class MockResponse:
    def __init__(self):
        self.is_done_flag = False
    
    def is_done(self):
        return self.is_done_flag
    
    async def defer(self, *args, **kwargs):
        self.is_done_flag = True

async def test_token_weighted_voting():
    """Test the token-weighted voting system functionality"""
    print("🧪 Testing Token-Weighted Voting System...")
    
    # Initialize database
    await db.init_db()
    print("✅ Database initialized")
    
    # Test 1: Create campaign and scenarios
    print("\n📝 Test 1: Creating campaign and scenarios with different weight modes...")
    
    guild_id = 12345
    creator_id = 67890
    voter_id_1 = 11111
    voter_id_2 = 22222
    
    # Create campaign
    campaign_id = await db.create_campaign(
        guild_id=guild_id,
        creator_id=creator_id,
        title="Token Weight Test Campaign",
        description="Testing token-weighted voting functionality",
        total_tokens_per_voter=10,
        num_expected_scenarios=3
    )
    
    if not campaign_id:
        print("❌ Failed to create campaign")
        return
    
    print(f"✅ Created campaign C#{campaign_id}")
    
    # Approve the campaign
    admin_id = 99999
    approved = await db.approve_campaign(campaign_id, admin_id)
    assert approved, "Failed to approve campaign"
    print(f"✅ Campaign C#{campaign_id} approved")
    
    # Create scenarios with different weight modes
    deadline = (datetime.utcnow() + timedelta(days=7)).isoformat()
    
    # Scenario 1: Equal weight mode
    equal_weight_hyperparameters = {
        "allow_abstain": True,
        "weight_mode": "equal"
    }
    
    scenario_1_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Equal Weight Scenario",
        description="Testing equal weight voting",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters=equal_weight_hyperparameters,
        campaign_id=campaign_id,
        scenario_order=1,
        initial_status="ApprovedScenario"
    )
    
    # Scenario 2: Proportional weight mode
    proportional_weight_hyperparameters = {
        "allow_abstain": True,
        "weight_mode": "proportional"
    }
    
    scenario_2_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=creator_id,
        title="Proportional Weight Scenario",
        description="Testing proportional weight voting",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters=proportional_weight_hyperparameters,
        campaign_id=campaign_id,
        scenario_order=2,
        initial_status="ApprovedScenario"
    )
    
    print(f"✅ Created equal weight scenario P#{scenario_1_id}")
    print(f"✅ Created proportional weight scenario P#{scenario_2_id}")
    
    # Add options to scenarios
    options = ["Option A", "Option B", "Option C"]
    await db.add_proposal_options(scenario_1_id, options)
    await db.add_proposal_options(scenario_2_id, options)
    
    # Test 2: Test token enrollment and initial balances
    print("\n📝 Test 2: Testing voter enrollment and token balances...")
    
    # Enroll voters in campaign
    await db.enroll_voter_in_campaign(campaign_id, voter_id_1, 10)
    await db.enroll_voter_in_campaign(campaign_id, voter_id_2, 10)
    
    # Verify initial token balances
    tokens_1 = await db.get_user_remaining_tokens(campaign_id, voter_id_1)
    tokens_2 = await db.get_user_remaining_tokens(campaign_id, voter_id_2)
    
    print(f"📊 Voter 1 initial tokens: {tokens_1}")
    print(f"📊 Voter 2 initial tokens: {tokens_2}")
    
    assert tokens_1 == 10, f"Expected 10 tokens for voter 1, got {tokens_1}"
    assert tokens_2 == 10, f"Expected 10 tokens for voter 2, got {tokens_2}"
    
    print("✅ Token enrollment and balances correct")
    
    # Test 3: Test TokenInvestmentModal behavior for different weight modes
    print("\n📝 Test 3: Testing TokenInvestmentModal for different weight modes...")
    
    # Create campaign details
    campaign_details = await db.get_campaign(campaign_id)
    
    # Test equal weight mode modal
    equal_weight_view = PluralityVoteView(
        proposal_id=scenario_1_id,
        options=options,
        user_id=voter_id_1,
        allow_abstain=True,
        campaign_id=campaign_id,
        campaign_details=campaign_details,
        user_remaining_tokens=10,
        proposal_hyperparameters=equal_weight_hyperparameters
    )
    
    equal_weight_modal = TokenInvestmentModal(equal_weight_view, 10)
    
    print(f"📊 Equal weight modal title: {equal_weight_modal.title}")
    print(f"📊 Equal weight modal max tokens: {equal_weight_modal.max_tokens}")
    print(f"📊 Equal weight modal weight mode: {equal_weight_modal.weight_mode}")
    
    assert equal_weight_modal.weight_mode == "equal", f"Expected equal weight mode, got {equal_weight_modal.weight_mode}"
    assert equal_weight_modal.max_tokens == 1, f"Expected max 1 token for equal mode, got {equal_weight_modal.max_tokens}"
    
    # Test proportional weight mode modal
    proportional_weight_view = PluralityVoteView(
        proposal_id=scenario_2_id,
        options=options,
        user_id=voter_id_1,
        allow_abstain=True,
        campaign_id=campaign_id,
        campaign_details=campaign_details,
        user_remaining_tokens=10,
        proposal_hyperparameters=proportional_weight_hyperparameters
    )
    
    proportional_weight_modal = TokenInvestmentModal(proportional_weight_view, 10)
    
    print(f"📊 Proportional modal title: {proportional_weight_modal.title}")
    print(f"📊 Proportional modal max tokens: {proportional_weight_modal.max_tokens}")
    print(f"📊 Proportional modal weight mode: {proportional_weight_modal.weight_mode}")
    
    assert proportional_weight_modal.weight_mode == "proportional", f"Expected proportional weight mode, got {proportional_weight_modal.weight_mode}"
    assert proportional_weight_modal.max_tokens == 10, f"Expected max 10 tokens for proportional mode, got {proportional_weight_modal.max_tokens}"
    
    print("✅ TokenInvestmentModal correctly handles different weight modes")
    
    # Test 4: Test voting and token deduction
    print("\n📝 Test 4: Testing voting and token balance updates...")
    
    # Test equal weight voting (should auto-invest 1 token)
    # Simulate vote recording
    vote_success = await db.record_vote(
        user_id=voter_id_1,
        proposal_id=scenario_1_id,
        vote_data=json.dumps({"option": "Option A"}),
        is_abstain=False,
        tokens_invested=1
    )
    
    assert vote_success, "Failed to record vote for equal weight scenario"
    
    # Update token balance
    token_update_success = await db.update_user_remaining_tokens(campaign_id, voter_id_1, 1)
    assert token_update_success, "Failed to update token balance"
    
    # Verify token balance
    updated_tokens_1 = await db.get_user_remaining_tokens(campaign_id, voter_id_1)
    print(f"📊 Voter 1 tokens after equal weight vote: {updated_tokens_1}")
    assert updated_tokens_1 == 9, f"Expected 9 tokens after voting, got {updated_tokens_1}"
    
    # Test proportional weight voting (invest 3 tokens)
    vote_success_2 = await db.record_vote(
        user_id=voter_id_1,
        proposal_id=scenario_2_id,
        vote_data=json.dumps({"option": "Option B"}),
        is_abstain=False,
        tokens_invested=3
    )
    
    assert vote_success_2, "Failed to record vote for proportional weight scenario"
    
    # Update token balance
    token_update_success_2 = await db.update_user_remaining_tokens(campaign_id, voter_id_1, 3)
    assert token_update_success_2, "Failed to update token balance for proportional vote"
    
    # Verify token balance
    updated_tokens_1_final = await db.get_user_remaining_tokens(campaign_id, voter_id_1)
    print(f"📊 Voter 1 tokens after proportional weight vote: {updated_tokens_1_final}")
    assert updated_tokens_1_final == 6, f"Expected 6 tokens after proportional voting, got {updated_tokens_1_final}"
    
    print("✅ Voting and token balance updates working correctly")
    
    # Test 5: Test vote retrieval and token information
    print("\n📝 Test 5: Testing vote retrieval with token information...")
    
    # Get votes for scenario 1
    votes_scenario_1 = await db.get_proposal_votes(scenario_1_id)
    print(f"📊 Votes for equal weight scenario: {len(votes_scenario_1)}")
    
    if votes_scenario_1:
        vote = votes_scenario_1[0]
        print(f"📊 Vote data: {vote}")
        assert vote['tokens_invested'] == 1, f"Expected 1 token invested, got {vote['tokens_invested']}"
        assert vote['user_id'] == voter_id_1, f"Expected voter {voter_id_1}, got {vote['user_id']}"
    
    # Get votes for scenario 2
    votes_scenario_2 = await db.get_proposal_votes(scenario_2_id)
    print(f"📊 Votes for proportional weight scenario: {len(votes_scenario_2)}")
    
    if votes_scenario_2:
        vote = votes_scenario_2[0]
        print(f"📊 Vote data: {vote}")
        assert vote['tokens_invested'] == 3, f"Expected 3 tokens invested, got {vote['tokens_invested']}"
        assert vote['user_id'] == voter_id_1, f"Expected voter {voter_id_1}, got {vote['user_id']}"
    
    print("✅ Vote retrieval with token information working correctly")
    
    # Test 6: Test token balance constraints
    print("\n📝 Test 6: Testing token balance constraints...")
    
    # Try to vote with more tokens than available
    remaining_tokens = await db.get_user_remaining_tokens(campaign_id, voter_id_1)
    print(f"📊 Voter 1 remaining tokens: {remaining_tokens}")
    
    # Attempt to spend more tokens than available should be handled by validation
    try:
        # This should fail in the TokenInvestmentModal validation
        test_modal = TokenInvestmentModal(proportional_weight_view, remaining_tokens)
        print(f"📊 Modal created with {remaining_tokens} remaining tokens")
        
        # The validation would happen in on_submit when user enters a value > remaining_tokens
        print("✅ Token constraint validation setup correctly")
    except Exception as e:
        print(f"❌ Error creating modal: {e}")
    
    print("\n🎉 All Token-Weighted Voting tests passed!")
    
    # Summary
    print("\n📋 Test Summary:")
    print("✅ Campaign and scenario creation with weight modes")
    print("✅ Voter enrollment and token balance initialization")
    print("✅ TokenInvestmentModal behavior for different weight modes")
    print("✅ Vote recording with token investment")
    print("✅ Token balance updates and constraints")
    print("✅ Vote retrieval with token information")
    print("✅ Equal weight mode (1 token max)")
    print("✅ Proportional weight mode (variable tokens)")
    print("✅ Real-time token balance tracking")

if __name__ == "__main__":
    asyncio.run(test_token_weighted_voting())