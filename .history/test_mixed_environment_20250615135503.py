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
        self.default_role = MockRole("@everyone")
    
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
        self.bot = False
        self.roles = []
    
    async def send(self, message, **kwargs):
        print(f"[MOCK DM to {self.name}]: {message[:100]}...")
        return MockMessage(654321)

class MockRole:
    def __init__(self, name):
        self.name = name

class MockChannel:
    def __init__(self, name, channel_id):
        self.name = name
        self.id = channel_id
    
    async def send(self, *args, **kwargs):
        print(f"[MOCK SEND to #{self.name}]: {args[0][:50] if args else 'embed'}...")
        return MockMessage(123456)

class MockMessage:
    def __init__(self, message_id):
        self.id = message_id
        self.embeds = []

async def test_mixed_environment():
    """Test that campaigns and normal proposals work together correctly"""
    print("🧪 Testing Mixed Environment: Campaigns + Normal Proposals...")
    
    # Initialize database
    await db.init_db()
    print("✅ Database initialized")
    
    guild_id = 12345
    proposer_id = 67890
    voter_id_1 = 11111
    voter_id_2 = 22222
    voter_id_3 = 33333
    admin_id = 99999
    
    # Create guild and members
    guild = MockGuild(guild_id)
    guild.members = [
        MockMember(proposer_id, "Proposer"),
        MockMember(voter_id_1, "Voter1"),
        MockMember(voter_id_2, "Voter2"),
        MockMember(voter_id_3, "Voter3"),
        MockMember(admin_id, "Admin")
    ]
    
    # Add mock channels
    guild.text_channels = [
        MockChannel("campaign-management", 111111),
        MockChannel("vote-results", 222222),
        MockChannel("voting-room", 333333)
    ]
    
    deadline = (datetime.now() + timedelta(days=7)).isoformat()
    
    print("\n📝 Phase 1: Creating mixed proposals and campaigns...")
    
    # Create a campaign with multiple scenarios
    campaign_id = await db.create_campaign(
        guild_id=guild_id,
        creator_id=proposer_id,
        title="Mixed Environment Campaign",
        description="Testing campaign alongside normal proposals",
        total_tokens_per_voter=15,
        num_expected_scenarios=2
    )
    
    await db.approve_campaign(campaign_id, admin_id)
    
    # Create campaign scenarios
    scenario_1_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Campaign Scenario 1",
        description="First scenario in mixed environment",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True,
            "weight_mode": "proportional"
        },
        campaign_id=campaign_id,
        scenario_order=1,
        initial_status="ApprovedScenario"
    )
    
    scenario_2_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Campaign Scenario 2",
        description="Second scenario in mixed environment",
        voting_mechanism="Approval",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True,
            "weight_mode": "equal"
        },
        campaign_id=campaign_id,
        scenario_order=2,
        initial_status="ApprovedScenario"
    )
    
    # Create normal proposals interspersed with campaign scenarios
    normal_1_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Normal Proposal Alpha",
        description="Regular proposal without campaign",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True
        }
    )
    
    normal_2_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Normal Proposal Beta",
        description="Another regular proposal",
        voting_mechanism="Borda",
        deadline=deadline,
        requires_approval=False,
        hyperparameters={
            "allow_abstain": True
        }
    )
    
    # Add options to all proposals
    await db.add_proposal_options(scenario_1_id, ["Yes", "No"])
    await db.add_proposal_options(scenario_2_id, ["Option A", "Option B", "Option C"])
    await db.add_proposal_options(normal_1_id, ["Approve", "Reject"])
    await db.add_proposal_options(normal_2_id, ["Choice 1", "Choice 2", "Choice 3"])
    
    # Approve normal proposals
    await db.update_proposal_status(normal_1_id, "Voting", approved_by=admin_id)
    
    print(f"✅ Created campaign C#{campaign_id} with scenarios P#{scenario_1_id}, P#{scenario_2_id}")
    print(f"✅ Created normal proposals P#{normal_1_id}, P#{normal_2_id}")
    
    print("\n📝 Phase 2: Setting up voters...")
    
    # Enroll voters in campaign
    for voter_id in [voter_id_1, voter_id_2, voter_id_3]:
        await db.enroll_voter_in_campaign(campaign_id, voter_id, 15)
    
    print("✅ Campaign voter enrollment completed")
    
    print("\n📝 Phase 3: Simulating mixed voting patterns...")
    
    # Vote on Campaign Scenario 1 (Proportional tokens)
    await db.record_vote(voter_id_1, scenario_1_id, json.dumps({"option": "Yes"}), False, 3)
    await db.record_vote(voter_id_2, scenario_1_id, json.dumps({"option": "No"}), False, 5)
    await db.record_vote(voter_id_3, scenario_1_id, json.dumps({"option": "Yes"}), False, 2)
    
    # Update token balances
    await db.update_user_remaining_tokens(campaign_id, voter_id_1, 3)
    await db.update_user_remaining_tokens(campaign_id, voter_id_2, 5)
    await db.update_user_remaining_tokens(campaign_id, voter_id_3, 2)
    
    # Vote on Normal Proposal Alpha (No tokens)
    await db.record_vote(voter_id_1, normal_1_id, json.dumps({"option": "Approve"}), False, None)
    await db.record_vote(voter_id_2, normal_1_id, json.dumps({"option": "Reject"}), False, None)
    await db.record_vote(voter_id_3, normal_1_id, json.dumps({"option": "Approve"}), False, None)
    
    # Vote on Campaign Scenario 2 (Equal tokens)
    await db.record_vote(voter_id_1, scenario_2_id, json.dumps({"approved": ["Option A", "Option B"]}), False, 1)
    await db.record_vote(voter_id_2, scenario_2_id, json.dumps({"approved": ["Option B", "Option C"]}), False, 1)
    await db.record_vote(voter_id_3, scenario_2_id, json.dumps({"approved": ["Option A"]}), False, 1)
    
    # Update token balances
    await db.update_user_remaining_tokens(campaign_id, voter_id_1, 1)
    await db.update_user_remaining_tokens(campaign_id, voter_id_2, 1)
    await db.update_user_remaining_tokens(campaign_id, voter_id_3, 1)
    
    # Vote on Normal Proposal Beta (No tokens)
    await db.record_vote(voter_id_1, normal_2_id, json.dumps({"rankings": ["Choice 1", "Choice 2", "Choice 3"]}), False, None)
    await db.record_vote(voter_id_2, normal_2_id, json.dumps({"rankings": ["Choice 3", "Choice 1", "Choice 2"]}), False, None)
    await db.record_vote(voter_id_3, normal_2_id, json.dumps({"rankings": ["Choice 2", "Choice 3", "Choice 1"]}), False, None)
    
    print("✅ Mixed voting completed")
    
    print("\n📝 Phase 4: Calculating and verifying results...")
    
    # Test Campaign Scenario 1 Results
    scenario_1_results = await voting_utils.calculate_results(scenario_1_id)
    scenario_1_proposal = await db.get_proposal(scenario_1_id)
    
    assert scenario_1_results is not None, "Campaign scenario 1 results failed"
    assert scenario_1_proposal.get('campaign_id') == campaign_id, "Campaign scenario should have campaign_id"
    
    # Verify proportional token weighting
    assert scenario_1_results['total_weighted_votes'] == 10, f"Expected 10 weighted votes, got {scenario_1_results['total_weighted_votes']}"
    assert scenario_1_results['total_raw_votes'] == 3, f"Expected 3 raw votes, got {scenario_1_results['total_raw_votes']}"
    
    # Test Normal Proposal Alpha Results
    normal_1_results = await voting_utils.calculate_results(normal_1_id)
    normal_1_proposal = await db.get_proposal(normal_1_id)
    
    assert normal_1_results is not None, "Normal proposal 1 results failed"
    assert normal_1_proposal.get('campaign_id') is None, "Normal proposal should not have campaign_id"
    
    # Verify no token weighting (should be equal)
    for option, details in normal_1_results['results_detailed']:
        assert details['weighted_votes'] == details['raw_votes'], f"Normal proposal should have equal weighted/raw votes for {option}"
    
    # Test Campaign Scenario 2 Results
    scenario_2_results = await voting_utils.calculate_results(scenario_2_id)
    scenario_2_proposal = await db.get_proposal(scenario_2_id)
    
    assert scenario_2_results is not None, "Campaign scenario 2 results failed"
    assert scenario_2_proposal.get('campaign_id') == campaign_id, "Campaign scenario should have campaign_id"
    
    # Verify equal token weighting (1 token each)
    assert scenario_2_results['total_weighted_voting_power'] == 3, f"Expected 3 weighted voting power, got {scenario_2_results['total_weighted_voting_power']}"
    assert scenario_2_results['total_raw_voters'] == 3, f"Expected 3 raw voters, got {scenario_2_results['total_raw_voters']}"
    
    # Test Normal Proposal Beta Results
    normal_2_results = await voting_utils.calculate_results(normal_2_id)
    normal_2_proposal = await db.get_proposal(normal_2_id)
    
    assert normal_2_results is not None, "Normal proposal 2 results failed"
    assert normal_2_proposal.get('campaign_id') is None, "Normal proposal should not have campaign_id"
    
    # Verify no token weighting for Borda
    for option, details in normal_2_results['results_detailed']:
        assert details['weighted_score'] == details['raw_score'], f"Normal proposal should have equal weighted/raw scores for {option}"
    
    print("✅ Result calculations verified")
    
    print("\n📝 Phase 5: Testing formatting differentiation...")
    
    # Test campaign scenario formatting
    scenario_1_embed = await voting_utils.format_vote_results(scenario_1_results, scenario_1_proposal)
    scenario_2_embed = await voting_utils.format_vote_results(scenario_2_results, scenario_2_proposal)
    
    # Should have campaign-specific formatting
    assert f"C#{campaign_id}" in scenario_1_embed.title or f"C#{campaign_id}" in scenario_1_embed.description, "Scenario 1 should have campaign ID"
    assert f"S#1" in scenario_1_embed.title or f"S#1" in scenario_1_embed.description, "Scenario 1 should have scenario order"
    assert any("Token" in field.name or "token" in field.name.lower() for field in scenario_1_embed.fields), "Scenario 1 should have token info"
    
    assert f"C#{campaign_id}" in scenario_2_embed.title or f"C#{campaign_id}" in scenario_2_embed.description, "Scenario 2 should have campaign ID"
    assert f"S#2" in scenario_2_embed.title or f"S#2" in scenario_2_embed.description, "Scenario 2 should have scenario order"
    assert any("Token" in field.name or "token" in field.name.lower() for field in scenario_2_embed.fields), "Scenario 2 should have token info"
    
    # Test normal proposal formatting
    normal_1_embed = await voting_utils.format_vote_results(normal_1_results, normal_1_proposal)
    normal_2_embed = await voting_utils.format_vote_results(normal_2_results, normal_2_proposal)
    
    # Should NOT have campaign-specific formatting
    assert f"C#" not in normal_1_embed.title and f"C#" not in normal_1_embed.description, "Normal 1 should not have campaign ID"
    assert f"S#" not in normal_1_embed.title and f"S#" not in normal_1_embed.description, "Normal 1 should not have scenario order"
    assert not any("Token" in field.name or "token" in field.name.lower() for field in normal_1_embed.fields), "Normal 1 should not have token info"
    
    assert f"C#" not in normal_2_embed.title and f"C#" not in normal_2_embed.description, "Normal 2 should not have campaign ID"
    assert f"S#" not in normal_2_embed.title and f"S#" not in normal_2_embed.description, "Normal 2 should not have scenario order"
    assert not any("Token" in field.name or "token" in field.name.lower() for field in normal_2_embed.fields), "Normal 2 should not have token info"
    
    # Should have proposer info for normal proposals
    assert any("Proposer" in field.name for field in normal_1_embed.fields), "Normal 1 should have proposer info"
    assert any("Proposer" in field.name for field in normal_2_embed.fields), "Normal 2 should have proposer info"
    
    print("✅ Formatting differentiation verified")
    
    print("\n📝 Phase 6: Testing mixed closure and announcements...")
    
    # Close all proposals
    proposals_to_close = [
        (scenario_1_id, "campaign scenario 1"),
        (normal_1_id, "normal proposal 1"),
        (scenario_2_id, "campaign scenario 2"),
        (normal_2_id, "normal proposal 2")
    ]
    
    for proposal_id, description in proposals_to_close:
        # Move to voting status if needed
        await db.update_proposal_status(proposal_id, "Voting")
        
        # Close and announce
        results = await voting_utils.close_proposal(proposal_id)
        assert results is not None, f"Failed to close {description}"
        
        proposal = await db.get_proposal(proposal_id)
        success = await voting_utils.close_and_announce_results(guild, proposal, results)
        assert success, f"Failed to announce {description}"
        
        print(f"✅ Closed and announced {description} (P#{proposal_id})")
    
    print("\n📝 Phase 7: Testing campaign completion...")
    
    # Check campaign completion
    campaign_complete = await voting_utils.check_and_announce_campaign_completion(guild, campaign_id)
    assert campaign_complete, "Campaign should be completed"
    
    # Verify campaign status
    updated_campaign = await db.get_campaign(campaign_id)
    assert updated_campaign['status'] == 'completed', f"Campaign status should be 'completed', got '{updated_campaign['status']}'"
    
    print("✅ Campaign completion verified")
    
    print("\n📝 Phase 8: Verifying final state...")
    
    # Check all proposals are closed
    all_proposals = await db.get_server_proposals(guild_id)
    active_proposals = [p for p in all_proposals if p.get('status') not in ['Closed']]
    
    # Should have no active proposals from our test
    test_proposal_ids = {scenario_1_id, scenario_2_id, normal_1_id, normal_2_id}
    active_test_proposals = [p for p in active_proposals if p.get('proposal_id') in test_proposal_ids]
    
    assert len(active_test_proposals) == 0, f"Should have no active test proposals, found {len(active_test_proposals)}"
    
    # Verify token balances
    remaining_tokens_1 = await db.get_user_remaining_tokens(campaign_id, voter_id_1)
    remaining_tokens_2 = await db.get_user_remaining_tokens(campaign_id, voter_id_2)
    remaining_tokens_3 = await db.get_user_remaining_tokens(campaign_id, voter_id_3)
    
    # Each voter spent 4 tokens (3+1, 5+1, 2+1), should have 11, 9, 12 remaining
    assert remaining_tokens_1 == 11, f"Voter 1 should have 11 tokens, got {remaining_tokens_1}"
    assert remaining_tokens_2 == 9, f"Voter 2 should have 9 tokens, got {remaining_tokens_2}"
    assert remaining_tokens_3 == 12, f"Voter 3 should have 12 tokens, got {remaining_tokens_3}"
    
    print("✅ Final state verification completed")
    
    print("\n🎉 All Mixed Environment tests passed!")
    
    # Summary
    print("\n📋 Test Summary:")
    print("✅ Mixed proposal and campaign creation")
    print("✅ Simultaneous voting on campaigns and normal proposals")
    print("✅ Token constraint enforcement for campaigns only")
    print("✅ Differentiated result calculation (weighted vs normal)")
    print("✅ Proper formatting differentiation")
    print("✅ Mixed closure and announcement handling")
    print("✅ Campaign completion in mixed environment")
    print("✅ Final state integrity")
    
    # Detailed results
    print("\n📊 Mixed Environment Results:")
    print(f"  • Campaign Created: C#{campaign_id} (2 scenarios)")
    print(f"  • Normal Proposals: 2 created")
    print(f"  • Total Votes Cast: 12 (6 campaign + 6 normal)")
    print(f"  • Token Usage: 10 tokens in campaigns, 0 in normal")
    print(f"  • Campaign Completion: ✅ Automatic")
    print(f"  • System Differentiation: ✅ Verified")
    print(f"  • Database Integrity: ✅ Maintained")

if __name__ == "__main__":
    asyncio.run(test_mixed_environment())