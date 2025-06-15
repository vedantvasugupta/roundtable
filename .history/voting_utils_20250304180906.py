import discord
from datetime import datetime
import json
import db

class VotingMechanism:
    """Base class for voting mechanisms"""
    
    @staticmethod
    async def tally_votes(votes_data):
        raise NotImplementedError
    
    @staticmethod
    def get_description():
        raise NotImplementedError
    
    @staticmethod
    def get_vote_instructions():
        raise NotImplementedError

# Move all voting mechanism classes (PluralityVoting, BordaCount, etc.) here
# ...existing voting mechanism class implementations...

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

async def format_vote_results(results, proposal):
    """Format vote results into a Discord embed"""
    mechanism = results['mechanism']
    embed = discord.Embed(
        title=f"Voting Results: {proposal['title']}",
        description=f"Proposal #{proposal['proposal_id']}\n{proposal['description'][:200]}...",
        color=discord.Color.green()
    )
    
    # ...existing formatting code...
    return embed

async def check_expired_proposals():
    """Check for proposals with expired deadlines and close them"""
    try:
        active_proposals = await db.get_proposals_by_status('Voting')
        now = datetime.now()
        closed_proposals = []
        
        for proposal in active_proposals:
            try:
                deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
                if now > deadline:
                    proposal_id = proposal.get('proposal_id', proposal.get('id'))
                    results = await close_proposal(proposal_id)
                    if results:
                        closed_proposals.append((proposal, results))
            except Exception as e:
                print(f"Error processing proposal deadline: {e}")
                
        return closed_proposals
    except Exception as e:
        print(f"Error in check_expired_proposals: {e}")
        return []

async def close_and_announce_results(guild, proposal, results):
    """Close a proposal and announce the results"""
    try:
        proposal_id = proposal.get('proposal_id', proposal.get('id'))
        
        # Get results channel
        results_channel = discord.utils.get(guild.text_channels, name="governance-results")
        if not results_channel:
            return False
            
        # Format and send results with error handling
        try:
            if isinstance(results, dict):
                embed = discord.Embed(
                    title=f"Voting Results: {proposal['title']}",
                    description=f"Proposal #{proposal_id}\n{proposal['description'][:200]}...",
                    color=discord.Color.green()
                )
                
                embed.add_field(name="Status", value=proposal['status'], inline=False)
                
                if 'winner' in results:
                    embed.add_field(name="Winner", value=results['winner'], inline=False)
                
                if 'results' in results:
                    result_text = "\n".join([f"**{option}**: {count}" for option, count in results['results']])
                    embed.add_field(name="Results", value=result_text or "No votes cast", inline=False)
                
                await results_channel.send(embed=embed)
            else:
                await results_channel.send(f"🗳️ Voting has ended for Proposal #{proposal_id}")
            
            return True
            
        except Exception as e:
            print(f"Error formatting results embed: {e}")
            # Fallback to simple message
            await results_channel.send(
                f"🗳️ **Voting Results for Proposal #{proposal_id}**\n"
                f"**Title:** {proposal.get('title', 'Unknown')}\n"
                f"**Status:** {proposal.get('status', 'Unknown')}"
            )
            
        results = await mechanism.tally_votes(votes)
        if results:
            new_status = 'Passed' if votes and results.get('winner') else 'Failed'
            await db.update_proposal_status(proposal_id, new_status)
            
            results_json = json.dumps(results)
            await db.store_proposal_results(proposal_id, results_json)
            
        return results
    except Exception as e:
        print(f"Error in close_proposal: {e}")
        return None
