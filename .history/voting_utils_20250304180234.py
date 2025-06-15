import discord
import asyncio
from datetime import datetime, timedelta
import json
import db
import random
from typing import List, Dict, Any, Optional, Union

# ========================
# 🔹 VOTING MECHANISMS
# ========================

class VotingMechanism:
    """Base class for voting mechanisms"""
    
    @staticmethod
    async def tally_votes(votes_data):
        """To be implemented by subclasses"""
        raise NotImplementedError
    
    @staticmethod
    def get_description():
        """Returns a description of the voting mechanism"""
        raise NotImplementedError
    
    @staticmethod
    def get_vote_instructions():
        """Returns instructions for how to vote using this mechanism"""
        raise NotImplementedError

class PluralityVoting(VotingMechanism):
    """Simple majority voting"""
    
    @staticmethod
    async def tally_votes(votes_data):
        """
        Tally votes using plurality voting (most votes wins)
        
        votes_data: List of vote objects with 'vote_data' field containing JSON string
                   with format: {"option": "option_name"}
        """
        vote_counts = {}
        
        for vote in votes_data:
            try:
                vote_value = json.loads(vote['vote_data'])
                option = vote_value.get('option')
                if option:
                    vote_counts[option] = vote_counts.get(option, 0) + 1
            except (json.JSONDecodeError, KeyError):
                continue
        
        # Sort options by vote count (descending)
        sorted_results = sorted(vote_counts.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'mechanism': 'plurality',
            'results': sorted_results,
            'winner': sorted_results[0][0] if sorted_results else None,
            'vote_counts': vote_counts
        }
    
    @staticmethod
    def get_description():
        return "Simple majority voting - the option with the most votes wins."
    
    @staticmethod
    def get_vote_instructions():
        return "To vote, use `!vote <proposal_id> <option>` where option is your preferred choice."

class BordaCount(VotingMechanism):
    """Ranked voting with points"""
    
    @staticmethod
    async def tally_votes(votes_data):
        """
        Tally votes using Borda count
        
        votes_data: List of vote objects with 'vote_data' field containing JSON string
                   with format: {"rankings": ["option1", "option2", "option3"]}
        """
        # First, collect all options from all votes
        all_options = set()
        rankings = []
        
        for vote in votes_data:
            try:
                vote_value = json.loads(vote['vote_data'])
                voter_ranking = vote_value.get('rankings', [])
                if voter_ranking:
                    rankings.append(voter_ranking)
                    all_options.update(voter_ranking)
            except (json.JSONDecodeError, KeyError):
                continue
        
        # Calculate Borda points
        points = {option: 0 for option in all_options}
        num_options = len(all_options)
        
        for ranking in rankings:
            for i, option in enumerate(ranking):
                # Points are (n-position), where n is the number of options
                # So first place gets n-1 points, second gets n-2, etc.
                points[option] += num_options - i - 1
        
        # Sort options by points (descending)
        sorted_results = sorted(points.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'mechanism': 'borda',
            'results': sorted_results,
            'winner': sorted_results[0][0] if sorted_results else None,
            'points': points
        }
    
    @staticmethod
    def get_description():
        return "Ranked voting - voters rank options and points are assigned based on ranking position."
    
    @staticmethod
    def get_vote_instructions():
        return "To vote, use `!vote <proposal_id> rank option1,option2,option3,...` where options are listed in your order of preference."

class ApprovalVoting(VotingMechanism):
    """Vote for multiple options"""
    
    @staticmethod
    async def tally_votes(votes_data):
        """
        Tally votes using approval voting
        
        votes_data: List of vote objects with 'vote_data' field containing JSON string
                   with format: {"approved": ["option1", "option2"]}
        """
        approval_counts = {}
        
        for vote in votes_data:
            try:
                vote_value = json.loads(vote['vote_data'])
                approved_options = vote_value.get('approved', [])
                for option in approved_options:
                    approval_counts[option] = approval_counts.get(option, 0) + 1
            except (json.JSONDecodeError, KeyError):
                continue
        
        # Sort options by approval count (descending)
        sorted_results = sorted(approval_counts.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'mechanism': 'approval',
            'results': sorted_results,
            'winner': sorted_results[0][0] if sorted_results else None,
            'approval_counts': approval_counts
        }
    
    @staticmethod
    def get_description():
        return "Approval voting - voters can approve multiple options, and the option with the most approvals wins."
    
    @staticmethod
    def get_vote_instructions():
        return "To vote, use `!vote <proposal_id> approve option1,option2,...` where you list all options you approve of."

class RunoffVoting(VotingMechanism):
    """Multiple rounds if needed"""
    
    @staticmethod
    async def tally_votes(votes_data):
        """
        Tally votes using runoff voting
        
        votes_data: List of vote objects with 'vote_data' field containing JSON string
                   with format: {"rankings": ["option1", "option2", "option3"]}
        """
        # First, collect all options from all votes
        all_options = set()
        rankings = []
        
        for vote in votes_data:
            try:
                vote_value = json.loads(vote['vote_data'])
                voter_ranking = vote_value.get('rankings', [])
                if voter_ranking:
                    rankings.append(voter_ranking)
                    all_options.update(voter_ranking)
            except (json.JSONDecodeError, KeyError):
                continue
        
        all_options = list(all_options)
        total_votes = len(rankings)
        
        # Simulate runoff rounds
        rounds = []
        remaining_options = all_options.copy()
        winner = None
        
        while remaining_options and not winner:
            # Count first-choice votes for each remaining option
            first_choice_counts = {option: 0 for option in remaining_options}
            
            for ranking in rankings:
                # Find the first choice that's still in the running
                for option in ranking:
                    if option in remaining_options:
                        first_choice_counts[option] += 1
                        break
            
            # Calculate percentages
            percentages = {option: (count / total_votes * 100) if total_votes > 0 else 0 
                          for option, count in first_choice_counts.items()}
            
            # Record this round's results
            round_results = {
                'counts': first_choice_counts,
                'percentages': percentages,
                'options': remaining_options.copy()
            }
            rounds.append(round_results)
            
            # Check if any option has majority
            for option, count in first_choice_counts.items():
                if count > total_votes / 2:
                    winner = option
                    break
            
            # If no winner, eliminate the option with fewest votes
            if not winner and len(remaining_options) > 1:
                min_votes = min(first_choice_counts.values())
                to_eliminate = [option for option, count in first_choice_counts.items() if count == min_votes]
                
                # If there's a tie for elimination, randomly choose one
                eliminated = random.choice(to_eliminate)
                remaining_options.remove(eliminated)
            elif not winner and remaining_options:
                # If only one option remains and no majority, it's the winner by default
                winner = remaining_options[0]
        
        return {
            'mechanism': 'runoff',
            'results': rounds,
            'winner': winner,
            'rounds': len(rounds)
        }
    
    @staticmethod
    def get_description():
        return "Runoff voting - if no option gets a majority, the lowest-ranked option is eliminated and another round occurs."
    
    @staticmethod
    def get_vote_instructions():
        return "To vote, use `!vote <proposal_id> rank option1,option2,option3,...` where options are listed in your order of preference."

class DHondtMethod(VotingMechanism):
    """Proportional representation"""
    
    @staticmethod
    async def tally_votes(votes_data):
        """
        Tally votes using D'Hondt method for proportional representation
        
        votes_data: List of vote objects with 'vote_data' field containing JSON string
                   with format: {"option": "option_name"}
        
        Note: This is typically used for allocating seats to parties, so we'll adapt it
        to allocate a fixed number of "seats" to options based on votes.
        """
        vote_counts = {}
        
        for vote in votes_data:
            try:
                vote_value = json.loads(vote['vote_data'])
                option = vote_value.get('option')
                if option:
                    vote_counts[option] = vote_counts.get(option, 0) + 1
            except (json.JSONDecodeError, KeyError):
                continue
        
        # Number of seats to allocate (can be configurable)
        seats = 10
        
        # Apply D'Hondt formula
        allocations = {option: 0 for option in vote_counts.keys()}
        
        for _ in range(seats):
            # Calculate quotients for each option
            quotients = {option: vote_counts[option] / (allocations[option] + 1) for option in vote_counts.keys()}
            
            # Find option with highest quotient
            max_quotient = 0
            max_option = None
            
            for option, quotient in quotients.items():
                if quotient > max_quotient:
                    max_quotient = quotient
                    max_option = option
            
            if max_option:
                allocations[max_option] += 1
        
        # Sort by allocation (descending)
        sorted_results = sorted(allocations.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'mechanism': 'dhondt',
            'results': sorted_results,
            'allocations': allocations,
            'total_seats': seats,
            'vote_counts': vote_counts
        }
    
    @staticmethod
    def get_description():
        return "D'Hondt method - a proportional representation system that allocates seats based on vote share."
    
    @staticmethod
    def get_vote_instructions():
        return "To vote, use `!vote <proposal_id> <option>` where option is your preferred choice."

# Factory to get the appropriate voting mechanism
def get_voting_mechanism(mechanism_name):
    """Returns the appropriate voting mechanism class based on name"""
    mechanisms = {
        "plurality": PluralityVoting,
        "borda": BordaCount,
        "approval": ApprovalVoting,
        "runoff": RunoffVoting,
        "dhondt": DHondtMethod
    }
    return mechanisms.get(mechanism_name.lower())

# ========================
# 🔹 SHARED VOTING UTILITIES
# ========================

async def format_vote_results(results, proposal):
    """Format vote results into a Discord embed"""
    mechanism = results['mechanism']
    embed = discord.Embed(
        title=f"Voting Results: {proposal['title']}",
        description=f"Proposal #{proposal['proposal_id']}\n{proposal['description'][:200]}...",
        color=discord.Color.green()
    )
    
    embed.add_field(name="Voting Mechanism", value=mechanism.title(), inline=False)
    
    if mechanism == 'plurality':
        # Format plurality results
        result_text = "\n".join([f"**{option}**: {count} votes" for option, count in results['results']])
        embed.add_field(name="Results", value=result_text or "No votes cast", inline=False)
        
        if results['winner']:
            embed.add_field(name="Winner", value=results['winner'], inline=False)
    
    elif mechanism == 'borda':
        # Format Borda count results
        result_text = "\n".join([f"**{option}**: {points} points" for option, points in results['results']])
        embed.add_field(name="Results", value=result_text or "No votes cast", inline=False)
        
        if results['winner']:
            embed.add_field(name="Winner", value=results['winner'], inline=False)
    
    elif mechanism == 'approval':
        # Format approval voting results
        result_text = "\n".join([f"**{option}**: {count} approvals" for option, count in results['results']])
        embed.add_field(name="Results", value=result_text or "No votes cast", inline=False)
        
        if results['winner']:
            embed.add_field(name="Winner", value=results['winner'], inline=False)
    
    elif mechanism == 'runoff':
        # Format runoff voting results
        embed.add_field(name="Total Rounds", value=str(results['rounds']), inline=False)
        
        for i, round_data in enumerate(results['results']):
            round_text = "\n".join([f"**{option}**: {round_data['counts'][option]} votes ({round_data['percentages'][option]:.1f}%)" 
                                  for option in round_data['options']])
            embed.add_field(name=f"Round {i+1}", value=round_text, inline=False)
        
        if results['winner']:
            embed.add_field(name="Winner", value=results['winner'], inline=False)
    
    elif mechanism == 'dhondt':
        # Format D'Hondt method results
        result_text = "\n".join([f"**{option}**: {seats} seats" for option, seats in results['results']])
        embed.add_field(name="Seat Allocation", value=result_text or "No votes cast", inline=False)
        
        vote_text = "\n".join([f"**{option}**: {count} votes" for option, count in sorted(
            results['vote_counts'].items(), key=lambda x: x[1], reverse=True)])
        embed.add_field(name="Vote Counts", value=vote_text or "No votes cast", inline=False)
    
    # Add timestamp
    embed.set_footer(text=f"Results calculated at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    
    return embed

async def close_proposal(proposal_id):
    """Close a proposal and tally the votes"""
    try:
        # Get proposal details
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return None
        
        # Get all votes for this proposal
        votes = await db.get_proposal_votes(proposal_id)
        print(f"📊 Found {len(votes)} votes for proposal #{proposal_id}")
        
        # Get the voting mechanism
        mechanism_name = proposal['voting_mechanism'].lower()
        mechanism = get_voting_mechanism(mechanism_name)
        if not mechanism:
            return None
        
        # Tally the votes
        results = await mechanism.tally_votes(votes)
        if not results:
            return None
        
        # Update proposal status based on votes
        new_status = 'Passed' if votes and results.get('winner') else 'Failed'
        await db.update_proposal_status(proposal_id, new_status)
        print(f"📝 Updated proposal #{proposal_id} status to {new_status}")
        
        # Store results in the database
        results_json = json.dumps(results)
        success = await db.store_proposal_results(proposal_id, results_json)
        
        if success:
            print(f"✅ Results stored for proposal #{proposal_id}")
        else:
            print(f"❌ Failed to store results for proposal #{proposal_id}")
            
        return results
    except Exception as e:
        print(f"❌ Error in close_proposal: {e}")
        return None

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
                    results = await close_proposal(proposal_id)
                    
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
            embed = await format_vote_results(results, proposal)
            
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

async def get_or_create_channel(guild, channel_name):
    """Get or create a channel with the given name"""
    channel = discord.utils.get(guild.text_channels, name=channel_name)
    
    if not channel:
        # Create the channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        
        # Send initial message based on channel type
        if channel_name == "proposals":
            await channel.send("📜 **Proposals Channel**\nThis channel is for posting and discussing governance proposals.")
        elif channel_name == "voting-room":
            await channel.send("🗳️ **Voting Room**\nThis channel announces active votes. You will receive DMs to cast your votes.")
        elif channel_name == "governance-results":
            await channel.send("📊 **Governance Results**\nThis channel shows the results of completed votes.")
    
    return channel

async def get_eligible_voters(guild, proposal):
    """Get all members eligible to vote on a proposal"""
    # Get constitutional variables
    const_vars = await db.get_constitutional_variables(guild.id)
    eligible_voters_role = const_vars.get("eligible_voters_role", {"value": "everyone"})["value"]
    
    if eligible_voters_role.lower() == "everyone":
        # Everyone can vote (except bots)
        return [member for member in guild.members if not member.bot]
    else:
        # Only members with the specified role can vote
        role = discord.utils.get(guild.roles, name=eligible_voters_role)
        if role:
            return [member for member in guild.members if role in member.roles and not member.bot]
        else:
            # Role not found, default to everyone
            return [member for member in guild.members if not member.bot]
