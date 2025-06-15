import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import json
import db
import random
from typing import List, Dict, Any, Optional, Union
from voting_utils import (
    get_voting_mechanism,
    format_vote_results,
    close_proposal,
    close_and_announce_results
)

# Remove voting mechanism classes as they're now in voting_utils.py

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
    if member.bot:
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
    if member.bot:
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
        return False, f"This proposal is not open for voting. Current status: {proposal['status']}"
    
    # Check if deadline has passed
    deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
    if datetime.now() > deadline:
        # Update proposal status if deadline passed
        await db.update_proposal_status(proposal_id, 'Closed')
        return False, "The voting deadline for this proposal has passed."
    
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
    
    # Check if all eligible voters have voted
    try:
        # Get all votes for this proposal
        all_votes = await db.get_proposal_votes(proposal_id)
        
        # Get the server ID from the proposal
        server_id = proposal['server_id']
        
        # Get the constitutional variables to check eligible_voters_role
        const_vars = await db.get_constitutional_variables(server_id)
        eligible_voters_role = const_vars.get("eligible_voters_role", {"value": "everyone"})["value"]
        
        # Get the server info to get member count
        server_info = await db.get_server_info(server_id)
        
        # Get vote tracking setting
        show_vote_count = const_vars.get("show_vote_count", {"value": "true"})["value"].lower() == "true"
        
        # If vote tracking is enabled, update the voting channel with current count
        if show_vote_count and server_info:
            # We'll need to get the guild object to find the voting channel
            # This will be handled by the main bot in proposals.py
            pass
            
        # If eligible_voters_role is not "everyone", we can't reliably check if all have voted
        # without the guild object, so we'll skip this check
        if eligible_voters_role.lower() == "everyone" and server_info and 'member_count' in server_info:
            # Compare vote count with member count (excluding bots)
            # This is an approximation since we can't filter out bots without the guild object
            # We'll assume about 10% of members are bots
            estimated_human_members = max(1, int(server_info['member_count'] * 0.9))
            
            # If all members have voted (approximately)
            if len(all_votes) >= estimated_human_members:
                # Close the proposal
                results = await close_proposal(proposal_id)
                
                # We can't announce results without the guild object, but we can
                # add to the message that results are available
                if results:
                    message += " All eligible voters have voted, so the results are now available."
    except Exception as e:
        print(f"Error checking if all voters have voted: {e}")
    
    return True, message

async def close_proposal(proposal_id):
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
        mechanism = get_voting_mechanism(mechanism_name)
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