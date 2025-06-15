import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import json
import db
import random
from typing import List, Dict, Any, Optional, Union
from proposals import close_and_announce_results
from main import bot  # Ensure the bot instance is imported

# Voting mechanism implementations

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

# Helper functions for voting

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

# ========================
# 🔹 INTERACTIVE VOTING UI
# ========================

class PluralityVoteView(discord.ui.View):
    """Interactive UI for plurality voting"""
    
    def __init__(self, proposal_id: int, options: List[str], user_id: int):
        super().__init__(timeout=None)  # No timeout
        self.proposal_id = proposal_id
        self.options = options
        self.user_id = user_id
        self.selected_option = None
        self.is_submitted = False
        
        print("DEBUG: Creating PluralityVoteView with proposal_id =", proposal_id)  # Debug print
        
        # Add option buttons
        for i, option in enumerate(options):
            button = discord.ui.Button(
                label=option,
                style=discord.ButtonStyle.secondary,
                custom_id=f"vote_{proposal_id}_{i}"
            )
            button.callback = self.option_callback
            self.add_item(button)
        
        # Add submit button
        submit_button = discord.ui.Button(
            label="Submit Vote",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_{proposal_id}",
            disabled=True
        )
        submit_button.callback = self.submit_callback
        self.add_item(submit_button)
    
    async def option_callback(self, interaction: discord.Interaction):
        """Handle option selection"""
        # Only allow the original user to interact with buttons
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your vote!", ephemeral=True)
            return
        
        # If already submitted, don't allow changes
        if self.is_submitted:
            await interaction.response.send_message("Your vote has already been submitted and cannot be changed.", ephemeral=True)
            return
        
        # Get the selected option
        button_id = interaction.data["custom_id"]
        option_index = int(button_id.split("_")[2])
        self.selected_option = self.options[option_index]
        
        # Update button styles
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id.startswith("vote_"):
                index = int(child.custom_id.split("_")[2])
                if index == option_index:
                    child.style = discord.ButtonStyle.primary
                else:
                    child.style = discord.ButtonStyle.secondary
        
        # Enable submit button
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id.startswith("submit_"):
                child.disabled = False
        
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"You selected: {self.selected_option}", ephemeral=True)
    
    async def submit_callback(self, interaction: discord.Interaction):
        """Handle vote submission"""
        # Only allow the original user to interact with buttons
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your vote!", ephemeral=True)
            return
        
        # If already submitted, don't allow changes
        if self.is_submitted:
            await interaction.response.send_message("Your vote has already been submitted and cannot be changed.", ephemeral=True)
            return
        
        if not self.selected_option:
            await interaction.response.send_message("Please select an option first!", ephemeral=True)
            return
        
        # Mark as submitted
        self.is_submitted = True
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True
        
        # Process the vote
        vote_data = {"option": self.selected_option}
        success, message = await process_vote(interaction.user.id, self.proposal_id, vote_data)
        
        # Update the message
        await interaction.response.edit_message(view=self)
        
        if success:
            await interaction.followup.send("✅ Your vote has been submitted and cannot be changed.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Error: {message}", ephemeral=True)


class RankedVoteView(discord.ui.View):
    """Interactive UI for ranked voting (Borda/Runoff)"""
    
    def __init__(self, proposal_id: int, options: List[str], user_id: int):
        super().__init__(timeout=None)  # No timeout
        self.proposal_id = proposal_id
        self.options = options.copy()
        self.user_id = user_id
        self.rankings = []
        self.is_submitted = False
        
        print("DEBUG: Creating RankedVoteView with proposal_id =", proposal_id)  # Debug print
        
        # Add dropdown for ranking
        self.update_dropdown()
        
        # Add submit button
        submit_button = discord.ui.Button(
            label="Submit Vote",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_{proposal_id}",
            disabled=True
        )
        submit_button.callback = self.submit_callback
        self.add_item(submit_button)
    
    def update_dropdown(self):
        """Update the dropdown with remaining options"""
        # Remove existing dropdown if any
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                self.remove_item(child)
        
        # Skip if no options left or already submitted
        if not self.options or self.is_submitted:
            return
        
        # Create new dropdown
        rank_position = len(self.rankings) + 1
        select = discord.ui.Select(
            placeholder=f"Select your #{rank_position} choice",
            custom_id=f"rank_{self.proposal_id}_{rank_position}"
        )
        
        # Add options to dropdown
        for i, option in enumerate(self.options):
            select.add_option(label=option, value=str(i))
        
        select.callback = self.rank_callback
        self.add_item(select)
    
    async def rank_callback(self, interaction: discord.Interaction):
        """Handle ranking selection"""
        # Only allow the original user to interact
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your vote!", ephemeral=True)
            return
        
        # If already submitted, don't allow changes
        if self.is_submitted:
            await interaction.response.send_message("Your vote has already been submitted and cannot be changed.", ephemeral=True)
            return
        
        # Get the selected option
        option_index = int(interaction.data["values"][0])
        selected_option = self.options.pop(option_index)
        self.rankings.append(selected_option)
        
        # Update the UI
        self.update_dropdown()
        
        # Enable submit button if at least one option ranked
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id.startswith("submit_"):
                child.disabled = False
        
        await interaction.response.edit_message(view=self)
        
        # Create status message
        status = "Current ranking:\n"
        for i, option in enumerate(self.rankings):
            status += f"{i+1}. {option}\n"
        
        if self.options:
            status += f"\nRemaining options: {len(self.options)}"
        else:
            status += "\nAll options ranked!"
        
        await interaction.followup.send(status, ephemeral=True)
    
    async def submit_callback(self, interaction: discord.Interaction):
        """Handle vote submission"""
        # Only allow the original user to interact
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your vote!", ephemeral=True)
            return
        
        # If already submitted, don't allow changes
        if self.is_submitted:
            await interaction.response.send_message("Your vote has already been submitted and cannot be changed.", ephemeral=True)
            return
        
        if not self.rankings:
            await interaction.response.send_message("Please rank at least one option first!", ephemeral=True)
            return
        
        # Mark as submitted
        self.is_submitted = True
        
        # Disable all UI elements
        for child in self.children:
            child.disabled = True
        
        # Process the vote
        vote_data = {"rankings": self.rankings}
        success, message = await process_vote(interaction.user.id, self.proposal_id, vote_data)
        
        # Update the message
        await interaction.response.edit_message(view=self)
        
        if success:
            await interaction.followup.send("✅ Your vote has been submitted and cannot be changed.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Error: {message}", ephemeral=True)


class ApprovalVoteView(discord.ui.View):
    """Interactive UI for approval voting"""
    
    def __init__(self, proposal_id: int, options: List[str], user_id: int):
        super().__init__(timeout=None)  # No timeout
        self.proposal_id = proposal_id
        self.options = options
        self.user_id = user_id
        self.approved_options = []
        self.is_submitted = False
        
        print("DEBUG: Creating ApprovalVoteView with proposal_id =", proposal_id)  # Debug print
        
        # Add option buttons
        for i, option in enumerate(options):
            button = discord.ui.Button(
                label=option,
                style=discord.ButtonStyle.secondary,
                custom_id=f"approve_{proposal_id}_{i}"
            )
            button.callback = self.option_callback
            self.add_item(button)
        
        # Add submit button
        submit_button = discord.ui.Button(
            label="Submit Vote",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_{proposal_id}",
            disabled=True
        )
        submit_button.callback = self.submit_callback
        self.add_item(submit_button)
    
    async def option_callback(self, interaction: discord.Interaction):
        """Handle option approval/unapproval"""
        # Only allow the original user to interact
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your vote!", ephemeral=True)
            return
        
        # If already submitted, don't allow changes
        if self.is_submitted:
            await interaction.response.send_message("Your vote has already been submitted and cannot be changed.", ephemeral=True)
            return
        
        # Get the selected option
        button_id = interaction.data["custom_id"]
        option_index = int(button_id.split("_")[2])
        option = self.options[option_index]
        
        # Toggle approval
        if option in self.approved_options:
            self.approved_options.remove(option)
        else:
            self.approved_options.append(option)
        
        # Update button styles
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id.startswith("approve_"):
                index = int(child.custom_id.split("_")[2])
                if self.options[index] in self.approved_options:
                    child.style = discord.ButtonStyle.primary
                else:
                    child.style = discord.ButtonStyle.secondary
        
        # Enable submit button if at least one option approved
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id.startswith("submit_"):
                child.disabled = len(self.approved_options) == 0
        
        await interaction.response.edit_message(view=self)
        
        # Create status message
        if self.approved_options:
            status = "Currently approved options:\n"
            for option in self.approved_options:
                status += f"• {option}\n"
        else:
            status = "No options approved yet."
        
        await interaction.followup.send(status, ephemeral=True)
    
    async def submit_callback(self, interaction: discord.Interaction):
        """Handle vote submission"""
        # Only allow the original user to interact
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your vote!", ephemeral=True)
            return
        
        # If already submitted, don't allow changes
        if self.is_submitted:
            await interaction.response.send_message("Your vote has already been submitted and cannot be changed.", ephemeral=True)
            return
        
        if not self.approved_options:
            await interaction.response.send_message("Please approve at least one option first!", ephemeral=True)
            return
        
        # Mark as submitted
        self.is_submitted = True
        
        # Disable all UI elements
        for child in self.children:
            child.disabled = True
        
        # Process the vote
        vote_data = {"approved": self.approved_options}
        success, message = await process_vote(interaction.user.id, self.proposal_id, vote_data)
        
        # Update the message
        await interaction.response.edit_message(view=self)
        
        if success:
            await interaction.followup.send("✅ Your vote has been submitted and cannot be changed.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Error: {message}", ephemeral=True)


async def send_voting_dm(member, proposal, options):
    """Send a DM to a member with interactive voting options"""
    # Skip if member is a bot
    if (member.bot):
        return False
        
    # Get the voting mechanism
    mechanism = get_voting_mechanism(proposal['voting_mechanism'])
    if not mechanism:
        return False
    
    # Create embed with proposal details
    embed = discord.Embed(
        title=f"Proposal #{proposal['proposal_id']}: {proposal['title']}",
        description=proposal['description'],
        color=discord.Color.blue()
    )
    
    # Add voting mechanism info
    embed.add_field(name="Voting Mechanism", value=f"{proposal['voting_mechanism'].title()}", inline=False)
    embed.add_field(name="Description", value=mechanism.get_description(), inline=False)
    
    # Format deadline
    deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
    embed.add_field(name="Deadline", value=deadline.strftime("%Y-%m-%d %H:%M UTC"), inline=False)
    
    # Add options
    options_text = "\n".join([f"• {option}" for option in options])
    embed.add_field(name="Options", value=options_text, inline=False)
    
    # Create the appropriate voting UI
    voting_mechanism = proposal['voting_mechanism'].lower()
    proposal_id = proposal['proposal_id']
    
    if voting_mechanism in ["plurality", "dhondt"]:
        view = PluralityVoteView(proposal_id, options, member.id)
    elif voting_mechanism in ["borda", "runoff"]:
        view = RankedVoteView(proposal_id, options, member.id)
    elif voting_mechanism == "approval":
        view = ApprovalVoteView(proposal_id, options, member.id)
    else:
        # Fallback to text-based voting for unsupported mechanisms
        view = None
    
    # Send initial message
    try:
        dm_channel = await member.create_dm()
        
        # Send with interactive UI if available
        if view:
            await dm_channel.send(
                content="You're invited to vote on a new proposal. Use the buttons below to cast your vote:",
                embed=embed,
                view=view
            )
            
            # Add note about text-based voting as fallback
            await dm_channel.send(
                "If the interactive buttons don't work, you can also vote using text commands:\n"
                f"• `!vote {proposal_id} <option>` for plurality/D'Hondt\n"
                f"• `!vote {proposal_id} rank option1,option2,...` for Borda/Runoff\n"
                f"• `!vote {proposal_id} approve option1,option2,...` for approval"
            )
        else:
            # Fallback to old text-based instructions
            await dm_channel.send(
                content="You're invited to vote on a new proposal:",
                embed=embed
            )
            
            # Add instructions based on voting mechanism
            instructions = mechanism.get_vote_instructions()
            await dm_channel.send(f"**How to vote:**\n{instructions}")
            
            # For convenience, add a formatted command example
            example = ""
            if voting_mechanism == 'plurality' or voting_mechanism == 'dhondt':
                example = f"Example: `!vote {proposal_id} {options[0]}`"
            elif voting_mechanism == 'borda' or voting_mechanism == 'runoff':
                example = f"Example: `!vote {proposal_id} rank {','.join(options[:2])}`"
            elif voting_mechanism == 'approval':
                example = f"Example: `!vote {proposal_id} approve {','.join(options[:2])}`"
            
            await dm_channel.send(example)
        
        # Record the voting invite
        await db.add_voting_invite(proposal['proposal_id'], member.id)
        
        return True
    except discord.Forbidden:
        # User has DMs disabled
        return False

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

async def process_vote(user_id, proposal_id, vote_data):
    """Process and record a vote"""
    # Check if proposal exists and is in voting stage
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        return False, "Proposal not found or invalid proposal ID."
    
    if proposal['status'] != 'Voting':
        return False, "Proposal is not in the voting stage."
    
    # Check if deadline has passed
    deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
    if datetime.now() > deadline:
        # Update proposal status if deadline passed
        await db.update_proposal_status(proposal_id, 'Closed')
        return False, "Voting has ended for this proposal."
    
    # Check if user has already voted
    existing_vote = await db.get_user_vote(proposal_id, user_id)
    if existing_vote:
        # Update existing vote
        await db.update_vote(existing_vote['vote_id'], vote_data)
        message = "Your vote has been updated."
    else:
        # Record new vote
        await db.add_vote(proposal_id, user_id, vote_data)
        message = "Your vote has been recorded."
    
    # Log the vote submission
    print(f"[VOTE SUBMISSION] User {user_id} submitted vote for proposal {proposal_id}: {vote_data}")
    
    # Check if all eligible voters have voted
    try:
        # Get all votes for this proposal
        all_votes = await db.get_proposal_votes(proposal_id)
        
        # Get the list of invited voters
        invited_voters = await db.get_invited_voters(proposal_id)
        print(f"[DEBUG] Total votes: {len(all_votes)}, Invited voters: {len(invited_voters)} for proposal {proposal_id}")
        
        if invited_voters and len(all_votes) >= len(invited_voters):
            print(f"[DEBUG] All invited voters have cast their vote for proposal {proposal_id}. Announcing results.")
            
            # Retrieve the guild object
            guild = bot.get_guild(proposal['server_id'])
            
            if guild is None:
                print(f"[ERROR] Guild with ID {proposal['server_id']} not found.")
            else:
                results = await close_proposal(proposal_id)
                # Pass the actual guild object to close_and_announce_results
                await close_and_announce_results(guild, proposal, results)
    except Exception as e:
        print(f"Error checking if all voters have voted: {e}")
    
    return True, message

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

async def get_vote_count(proposal_id):
    """Get the current vote count for a proposal"""
    votes = await db.get_proposal_votes(proposal_id)
    return len(votes)

async def update_vote_count_message(guild, proposal_id, voting_channel=None):
    """Update the vote count message in the voting channel"""
    if not voting_channel:
        voting_channel = discord.utils.get(guild.text_channels, name="voting-room")
        if not voting_channel:
            return False
    
    # Get proposal details
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        return False
    
    # Get vote count
    vote_count = await get_vote_count(proposal_id)
    
    # Get eligible voters count
    eligible_voters = await get_eligible_voters(guild, proposal)
    eligible_count = len([m for m in eligible_voters if not m.bot])
    
    # Create or update vote count message
    embed = discord.Embed(
        title=f"Voting Progress: Proposal #{proposal_id}",
        description=f"**{proposal['title']}**",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Votes Cast", value=f"{vote_count}/{eligible_count} eligible voters", inline=False)
    embed.add_field(name="Progress", value=f"{vote_count/eligible_count*100:.1f}% complete", inline=False)
    
    # Add deadline
    deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
    time_left = deadline - datetime.now()
    days, seconds = time_left.days, time_left.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    if days > 0:
        time_left_str = f"{days} days, {hours} hours"
    elif hours > 0:
        time_left_str = f"{hours} hours, {minutes} minutes"
    else:
        time_left_str = f"{minutes} minutes"
    
    embed.add_field(name="Time Remaining", value=time_left_str, inline=False)
    
    # Send or update message
    # This would require tracking the message ID, which is beyond the scope of this fix
    # For now, we'll just send a new message
    await voting_channel.send(embed=embed)
    
    return True