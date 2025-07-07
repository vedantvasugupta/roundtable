"""
Vote Result Fix

This script contains improved versions of the functions that handle proposal
result announcements. It fixes several issues with the current implementation:

1. Better error handling to ensure results are always announced
2. DM notifications to all eligible voters when results are ready
3. Fallback to simpler messages if Discord embed fails
4. Better logging for debugging purposes
5. Handles both ID and proposal_ID fields properly
6. Includes vote counts in results
"""

import discord
import asyncio
import json
from datetime import datetime
import db
import voting

# The fixes should be copied into proposals.py to replace the old function
async def close_and_announce_results(guild, proposal, results):
    """Close a proposal and announce the results"""
    try:
        print(f"⚙️ Announcing results for Proposal #{proposal.get('proposal_id', proposal.get('id'))} in {guild.name}")
        
        # Get proposal ID 
        proposal_id = proposal.get('proposal_id', proposal.get('id'))
        if not proposal_id:
            print(f"❌ Error: Couldn't determine proposal ID from: {proposal}")
            return False
            
        # Get results channel
        results_channel = await get_or_create_channel(guild, "governance-results")
        if not results_channel:
            print(f"❌ Error: Couldn't create or find results channel in {guild.name}")
            return False
            
        # Format results embed
        try:
            embed = await voting.format_vote_results(results, proposal)
            
            # Add proposal status
            embed.add_field(name="Final Status", value=proposal['status'], inline=False)
            
            # Add vote count
            votes = await db.get_proposal_votes(proposal_id)
            if votes:
                embed.add_field(name="Total Votes", value=str(len(votes)), inline=True)
                
            # Send results to channel
            await results_channel.send(embed=embed)
            print(f"✅ Results sent to {results_channel.name} channel")
        except Exception as e:
            # If the embed is too large or there's another error, send a simpler message
            print(f"⚠️ Error formatting results embed: {e}")
            winner = results.get('winner', 'No clear winner')
            mechanism = results.get('mechanism', 'unknown')
            
            simple_message = (
                f"🗳️ **Voting Results for Proposal #{proposal_id}**\n"
                f"**Title:** {proposal.get('title', 'Unknown')}\n"
                f"**Status:** {proposal.get('status', 'Unknown')}\n"
                f"**Voting Mechanism:** {mechanism}\n"
                f"**Winner:** {winner}\n\n"
                f"*Note: Full results couldn't be displayed due to an error.*"
            )
            await results_channel.send(simple_message)
        
        # Also send to proposals channel
        proposals_channel = discord.utils.get(guild.text_channels, name="proposals")
        if proposals_channel:
            await proposals_channel.send(f"🗳️ Voting has ended for Proposal #{proposal_id}. See <#{results_channel.id}> for results.")
            print(f"✅ Notification sent to {proposals_channel.name} channel")
            
        # Notify eligible voters via DM
        eligible_voters = await get_eligible_voters(guild, proposal)
        if eligible_voters:
            for member in eligible_voters:
                if not member.bot:
                    try:
                        dm_channel = await member.create_dm()
                        await dm_channel.send(
                            f"🗳️ Voting has ended for Proposal #{proposal_id}: {proposal.get('title', 'Unknown')}.\n"
                            f"The result is: **{proposal.get('status', 'Unknown')}**.\n"
                            f"Check the {results_channel.mention} channel for full details."
                        )
                    except Exception as dm_error:
                        print(f"⚠️ Couldn't send DM to {member.name}: {dm_error}")
                        
        return True
            
    except Exception as e:
        print(f"❌ Error announcing results: {e}")
        # Try a final fallback announcement
        try:
            general_channel = discord.utils.get(guild.text_channels, name="general")
            if general_channel:
                await general_channel.send(
                    f"🗳️ **Voting Results: Proposal #{proposal.get('proposal_id', proposal.get('id'))}**\n"
                    f"The vote has ended. Please check the governance channels for results."
                )
                print(f"✅ Emergency fallback notification sent to general channel")
        except:
            pass
        return False

# Fix for the check_expired_proposals function in voting.py
async def check_expired_proposals():
    """Check for proposals with expired deadlines and close them"""
    try:
        # Get all proposals with status 'Voting'
        active_proposals = await db.get_proposals_by_status('Voting')
        
        now = datetime.now()
        closed_proposals = []
        
        for proposal in active_proposals:
            try:
                # Get proposal deadline
                deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
                
                # Check if deadline has passed
                if now > deadline:
                    print(f"⏰ Proposal #{proposal.get('proposal_id', proposal.get('id'))} has passed its deadline")
                    
                    # Close the proposal and get results
                    proposal_id = proposal.get('proposal_id', proposal.get('id'))
                    # Obtain guild using bot from main
                    import main
                    guild = main.bot.get_guild(proposal['server_id'])
                    results = await close_proposal(proposal_id, guild)
                    
                    if results:
                        closed_proposals.append((proposal, results))
                        print(f"✅ Proposal #{proposal_id} closed successfully")
                    else:
                        print(f"❌ Failed to close proposal #{proposal_id} or get results")
            except Exception as e:
                print(f"❌ Error processing proposal deadline: {e}")
                continue
                
        return closed_proposals
    except Exception as e:
        print(f"❌ Error in check_expired_proposals: {e}")
        return []

# Fix for the close_proposal function in voting.py
async def close_proposal(proposal_id, guild):
    """Close a proposal and tally the votes"""
    try:
        # Get proposal details
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            print(f"❌ Proposal #{proposal_id} not found")
            return None
        
        # Get all votes for this proposal
        votes = await db.get_proposal_votes(proposal_id)
        print(f"📊 Found {len(votes)} votes for proposal #{proposal_id}")
        
        # Get the voting mechanism
        mechanism_name = proposal['voting_mechanism'].lower()
        mechanism = voting.get_voting_mechanism(mechanism_name)
        if not mechanism:
            print(f"❌ Invalid voting mechanism: {mechanism_name}")
            return None
        
        # Tally the votes
        results = await mechanism.tally_votes(votes)
        if not results:
            print(f"❌ Failed to tally votes for proposal #{proposal_id}")
            # Create a basic result with the mechanism name
            results = {
                'mechanism': mechanism_name,
                'results': [],
                'winner': 'No votes' if not votes else 'Error in vote tallying'
            }
        
        # Update proposal status based on votes
        new_status = 'Passed' if votes and results.get('winner') else 'Failed'
        await db.update_proposal_status(proposal_id, new_status)
        print(f"📝 Updated proposal #{proposal_id} status to {new_status}")
        
        # Store results in the database
        results_json = json.dumps(results)
        success = await db.store_proposal_results(proposal_id, results_json)
        
        if success:
            print(f"💾 Stored results for proposal #{proposal_id}")
        else:
            print(f"⚠️ Failed to store results for proposal #{proposal_id}")
            
        return results
    except Exception as e:
        print(f"❌ Error in close_proposal: {e}")
        return None

"""
TESTING PLAN FOR SMALL SERVER (2 USERS):

This is a step-by-step guide to test the vote results announcement functionality
in a small server environment (2 users):

1. SETUP:
   - Create a test server with at least 2 human users
   - Invite the bot to the server
   - Ensure both users have permission to create proposals and vote

2. PROPOSAL CREATION:
   - User 1 creates a proposal with a short deadline (5-10 minutes)
   - Use simple plurality voting for the first test
   - Use options like "Option A" and "Option B"

3. VOTING PROCESS:
   - Both users should receive DMs to vote
   - Each user votes for a different option to test tie-breaking
   - Verify votes are being recorded by checking the database

4. RESULT ANNOUNCEMENT:
   - Wait for the deadline to pass
   - Verify that results are announced in the governance-results channel
   - Check that the proposals channel receives a notification
   - Verify that both users receive DM notifications about results

5. EDGE CASES:
   - Test with no votes cast
   - Test with both users voting for the same option
   - Test with a more complex voting mechanism (Borda, Runoff)
   - Test with very long proposal descriptions/titles
   - Test manually closing a proposal

6. DEBUGGING:
   - Enable verbose console output by adding print statements
   - Check the server logs for any errors
   - Use the test_proposal.py script to verify database operations

IMPLEMENTATION INSTRUCTIONS:
1. Copy the improved close_and_announce_results function to proposals.py
2. Copy the improved check_expired_proposals function to voting.py
3. Copy the improved close_proposal function to voting.py
4. Test using the plan above
"""