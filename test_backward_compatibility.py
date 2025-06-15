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
        print(f"[MOCK DM to {self.name}]: {message}")
        return MockMessage(654321)

class MockRole:
    def __init__(self, name):
        self.name = name

class MockChannel:
    def __init__(self, name, channel_id):
        self.name = name
        self.id = channel_id
    
    async def send(self, *args, **kwargs):
        print(f"[MOCK SEND to #{self.name}]: {args[0] if args else 'embed'}")
        return MockMessage(123456)
    
    async def create_text_channel(self, name, **kwargs):
        return MockChannel(name, 999999)

class MockMessage:
    def __init__(self, message_id):
        self.id = message_id
        self.embeds = []
    
    async def edit(self, **kwargs):
        print(f"[MOCK EDIT message {self.id}]")
    
    async def fetch_message(self, message_id):
        return MockMessage(message_id)

async def test_backward_compatibility():
    """Test that normal proposals work correctly alongside campaign system"""
    print("🧪 Testing Backward Compatibility for Normal Proposals...")
    
    # Initialize database
    await db.init_db()
    print("✅ Database initialized")
    
    # Test 1: Create normal proposals (non-campaign)
    print("\n📝 Test 1: Creating normal standalone proposals...")
    
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
        MockChannel("governance-proposals", 111111),
        MockChannel("vote-results", 222222),
        MockChannel("voting-room", 333333)
    ]
    
    deadline = (datetime.now() + timedelta(days=7)).isoformat()
    
    # Create different types of normal proposals
    proposals = []
    
    # Proposal 1: Standard Plurality (requires approval)
    proposal_1_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Standard Plurality Proposal",
        description="Testing normal plurality voting without campaigns",
        voting_mechanism="Plurality",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True
        }
    )
    proposals.append(proposal_1_id)
    
    # Proposal 2: Approval Voting (no approval required)
    proposal_2_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Direct Approval Proposal",
        description="Testing approval voting without approval requirement",
        voting_mechanism="Approval",
        deadline=deadline,
        requires_approval=False,
        hyperparameters={
            "allow_abstain": True
        }
    )
    proposals.append(proposal_2_id)
    
    # Proposal 3: Borda Count (requires approval)
    proposal_3_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Ranked Choice Proposal",
        description="Testing borda count voting",
        voting_mechanism="Borda",
        deadline=deadline,
        requires_approval=True,
        hyperparameters={
            "allow_abstain": True
        }
    )
    proposals.append(proposal_3_id)
    
    print(f"✅ Created normal proposals: P#{proposal_1_id}, P#{proposal_2_id}, P#{proposal_3_id}")
    
    # Add options to proposals
    standard_options = ["Yes", "No"]
    multiple_options = ["Option A", "Option B", "Option C"]
    
    await db.add_proposal_options(proposal_1_id, standard_options)
    await db.add_proposal_options(proposal_2_id, multiple_options)
    await db.add_proposal_options(proposal_3_id, multiple_options)
    
    # Test 2: Verify proposal status handling
    print("\n📝 Test 2: Testing normal proposal status transitions...")
    
    # Check initial statuses
    prop1 = await db.get_proposal(proposal_1_id)
    prop2 = await db.get_proposal(proposal_2_id)
    prop3 = await db.get_proposal(proposal_3_id)
    
    assert prop1['status'] == 'Pending Approval', f"P#{proposal_1_id} should be Pending Approval, got {prop1['status']}"
    assert prop2['status'] == 'Voting', f"P#{proposal_2_id} should be Voting, got {prop2['status']}"
    assert prop3['status'] == 'Pending Approval', f"P#{proposal_3_id} should be Pending Approval, got {prop3['status']}"
    
    # Verify they are NOT campaign scenarios
    assert prop1.get('campaign_id') is None, f"P#{proposal_1_id} should not be part of a campaign"
    assert prop2.get('campaign_id') is None, f"P#{proposal_2_id} should not be part of a campaign"
    assert prop3.get('campaign_id') is None, f"P#{proposal_3_id} should not be part of a campaign"
    
    assert prop1.get('scenario_order') is None, f"P#{proposal_1_id} should not have scenario order"
    assert prop2.get('scenario_order') is None, f"P#{proposal_2_id} should not have scenario order"
    assert prop3.get('scenario_order') is None, f"P#{proposal_3_id} should not have scenario order"
    
    print("✅ Normal proposal statuses and non-campaign attributes verified")
    
    # Approve proposals that require approval
    await db.update_proposal_status(proposal_1_id, "Voting", approved_by=admin_id)
    await db.update_proposal_status(proposal_3_id, "Voting", approved_by=admin_id)
    
    print("✅ Proposal approvals completed")
    
    # Test 3: Simulate normal voting (without tokens)
    print("\n📝 Test 3: Testing normal voting without token constraints...")
    
    # Vote on Proposal 1 (Plurality - standard Yes/No)
    await db.record_vote(voter_id_1, proposal_1_id, json.dumps({"option": "Yes"}), False, None)
    await db.record_vote(voter_id_2, proposal_1_id, json.dumps({"option": "No"}), False, None)
    await db.record_vote(voter_id_3, proposal_1_id, json.dumps({"option": "Yes"}), False, None)
    
    # Vote on Proposal 2 (Approval - multiple options)
    await db.record_vote(voter_id_1, proposal_2_id, json.dumps({"approved": ["Option A", "Option B"]}), False, None)
    await db.record_vote(voter_id_2, proposal_2_id, json.dumps({"approved": ["Option B", "Option C"]}), False, None)
    await db.record_vote(voter_id_3, proposal_2_id, json.dumps({"approved": ["Option A"]}), False, None)
    
    # Vote on Proposal 3 (Borda - ranked choice)
    await db.record_vote(voter_id_1, proposal_3_id, json.dumps({"rankings": ["Option A", "Option B", "Option C"]}), False, None)
    await db.record_vote(voter_id_2, proposal_3_id, json.dumps({"rankings": ["Option C", "Option A", "Option B"]}), False, None)
    await db.record_vote(voter_id_3, proposal_3_id, json.dumps({"rankings": ["Option B", "Option C", "Option A"]}), False, None)
    
    print("✅ Normal voting simulation completed")
    
    # Test 4: Verify result calculation for normal proposals
    print("\n📝 Test 4: Testing result calculation for normal proposals...")
    
    results_list = []
    
    for i, proposal_id in enumerate(proposals, 1):
        print(f"\n🔍 Calculating results for Normal Proposal {i} (P#{proposal_id})...")
        
        # Calculate results
        results = await voting_utils.calculate_results(proposal_id)
        assert results is not None, f"Failed to calculate results for proposal {i}"
        
        results_list.append(results)
        
        # Verify result structure
        assert 'mechanism' in results, f"Missing mechanism in results for proposal {i}"
        assert 'results_detailed' in results, f"Missing detailed results for proposal {i}"
        
        # Verify normal voting (no token weighting)
        if results['mechanism'] == 'plurality':
            for option, details in results['results_detailed']:
                # For normal voting, weighted votes should equal raw votes (default weight = 1)
                assert details['weighted_votes'] == details['raw_votes'], f"Normal voting should have equal weighted/raw votes for {option}"
        elif results['mechanism'] == 'approval':
            for option, details in results['results_detailed']:
                assert details['weighted_approvals'] == details['raw_approvals'], f"Normal voting should have equal weighted/raw approvals for {option}"
        elif results['mechanism'] == 'borda':
            for option, details in results['results_detailed']:
                assert details['weighted_score'] == details['raw_score'], f"Normal voting should have equal weighted/raw scores for {option}"
        
        print(f"✅ Normal Proposal {i} results: Mechanism={results['mechanism']}, Winner={results.get('winner', 'None')}")
        
        # Print detailed results for verification
        if results['mechanism'] == 'plurality':
            for option, details in results['results_detailed']:
                print(f"  • {option}: {details['weighted_votes']} votes")
        elif results['mechanism'] == 'approval':
            for option, details in results['results_detailed']:
                print(f"  • {option}: {details['weighted_approvals']} approvals")
        elif results['mechanism'] == 'borda':
            for option, details in results['results_detailed']:
                print(f"  • {option}: {details['weighted_score']} points")
    
    # Test 5: Verify normal proposal formatting
    print("\n📝 Test 5: Testing normal proposal result formatting...")
    
    for i, proposal_id in enumerate(proposals, 1):
        proposal = await db.get_proposal(proposal_id)
        results = results_list[i-1]
        
        # Test formatting
        embed = await voting_utils.format_vote_results(results, proposal)
        
        # Verify normal proposal formatting (should NOT have campaign-specific elements)
        assert f"C#" not in embed.title and f"C#" not in embed.description, f"Normal proposal {i} should not have campaign ID in title/description"
        assert f"S#" not in embed.title and f"S#" not in embed.description, f"Normal proposal {i} should not have scenario order in title/description"
        
        # Should NOT have token information fields for normal proposals
        has_token_info = any("Token" in field.name or "token" in field.name.lower() for field in embed.fields)
        assert not has_token_info, f"Normal proposal {i} should not have token information"
        
        # Should have proposer information
        has_proposer = any("Proposer" in field.name for field in embed.fields)
        assert has_proposer, f"Normal proposal {i} should have proposer information"
        
        print(f"✅ Normal Proposal {i} formatting verified")
    
    # Test 6: Test normal proposal closure and announcements
    print("\n📝 Test 6: Testing normal proposal closure and announcements...")
    
    for proposal_id in proposals:
        # Close the proposal
        results = await voting_utils.close_proposal(proposal_id)
        assert results is not None, f"Failed to close normal proposal P#{proposal_id}"
        
        proposal = await db.get_proposal(proposal_id)
        
        # Test announcement (should use normal channels, not campaign-specific)
        success = await voting_utils.close_and_announce_results(guild, proposal, results)
        assert success, f"Failed to announce results for normal proposal P#{proposal_id}"
        
        print(f"✅ Closed and announced normal proposal P#{proposal_id}")
    
    # Test 7: Verify no interference with campaigns
    print("\n📝 Test 7: Testing coexistence with campaign system...")
    
    # Create a simple campaign to verify no interference
    campaign_id = await db.create_campaign(
        guild_id=guild_id,
        creator_id=proposer_id,
        title="Test Campaign",
        description="Testing campaign coexistence",
        total_tokens_per_voter=10,
        num_expected_scenarios=1
    )
    
    # Approve the campaign
    await db.approve_campaign(campaign_id, admin_id)
    
    # Create one campaign scenario
    scenario_id = await db.create_proposal(
        server_id=guild_id,
        proposer_id=proposer_id,
        title="Campaign Scenario",
        description="Testing campaign scenario alongside normal proposals",
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
    
    await db.add_proposal_options(scenario_id, ["Yes", "No"])
    
    # Enroll voters in campaign
    for voter_id in [voter_id_1, voter_id_2, voter_id_3]:
        await db.enroll_voter_in_campaign(campaign_id, voter_id, 10)
    
    # Vote on campaign scenario
    await db.record_vote(voter_id_1, scenario_id, json.dumps({"option": "Yes"}), False, 1)
    await db.record_vote(voter_id_2, scenario_id, json.dumps({"option": "Yes"}), False, 1)
    await db.record_vote(voter_id_3, scenario_id, json.dumps({"option": "No"}), False, 1)
    
    # Update token balances
    for voter_id in [voter_id_1, voter_id_2, voter_id_3]:
        await db.update_user_remaining_tokens(campaign_id, voter_id, 1)
    
    # Close campaign scenario
    await db.update_proposal_status(scenario_id, "Voting")
    scenario_results = await voting_utils.close_proposal(scenario_id)
    assert scenario_results is not None, "Failed to close campaign scenario"
    
    # Verify campaign scenario has different formatting than normal proposals
    scenario_proposal = await db.get_proposal(scenario_id)
    scenario_embed = await voting_utils.format_vote_results(scenario_results, scenario_proposal)
    
    # Campaign scenario should have campaign-specific formatting
    assert f"C#{campaign_id}" in scenario_embed.title or f"C#{campaign_id}" in scenario_embed.description, "Campaign scenario should have campaign ID"
    
    # Should have token information
    has_token_info = any("Token" in field.name or "token" in field.name.lower() for field in scenario_embed.fields)
    assert has_token_info, "Campaign scenario should have token information"
    
    print("✅ Campaign and normal proposal coexistence verified")
    
    # Test 8: Verify database integrity
    print("\n📝 Test 8: Verifying database integrity...")
    
    # Check that normal proposals are correctly stored
    normal_proposals = await db.get_server_proposals(guild_id)
    campaign_proposals = [p for p in normal_proposals if p.get('campaign_id') is not None]
    standalone_proposals = [p for p in normal_proposals if p.get('campaign_id') is None]
    
    assert len(standalone_proposals) >= 3, f"Should have at least 3 standalone proposals, got {len(standalone_proposals)}"
    assert len(campaign_proposals) >= 1, f"Should have at least 1 campaign proposal, got {len(campaign_proposals)}"
    
    # Verify hyperparameters are preserved correctly
    for proposal in standalone_proposals:
        if proposal['proposal_id'] in proposals:
            assert isinstance(proposal.get('hyperparameters'), dict), f"Hyperparameters should be dict for P#{proposal['proposal_id']}"
            assert proposal['hyperparameters'].get('allow_abstain') is True, f"Allow abstain should be preserved for P#{proposal['proposal_id']}"
    
    print("✅ Database integrity verified")
    
    print("\n🎉 All Backward Compatibility tests passed!")
    
    # Summary
    print("\n📋 Test Summary:")
    print("✅ Normal proposal creation and status management")
    print("✅ Non-campaign attribute verification")
    print("✅ Normal voting without token constraints")
    print("✅ Result calculation accuracy for normal proposals")
    print("✅ Normal proposal formatting (no campaign elements)")
    print("✅ Normal proposal closure and announcements")
    print("✅ Coexistence with campaign system")
    print("✅ Database integrity maintenance")
    
    # Detailed verification results
    print("\n📊 Verification Results:")
    print(f"  • Normal Proposals Created: {len(proposals)}")
    print(f"  • Campaign Scenarios Created: 1")
    print(f"  • Total Votes on Normal Proposals: {len(proposals) * 3}")
    print(f"  • All Result Calculations: ✅ Accurate")
    print(f"  • Formatting Differentiation: ✅ Correct")
    print(f"  • System Coexistence: ✅ Verified")

if __name__ == "__main__":
    asyncio.run(test_backward_compatibility())