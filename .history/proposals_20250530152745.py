import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import json
import db
import voting
import voting_utils
import utils
import traceback
import re
from typing import List, Optional, Dict, Any, Union

# Enable all intents (or specify only the necessary ones)
intents = discord.Intents.default()
intents.message_content = True  # Required for handling messages

# Initialize bot with intents
bot = commands.Bot(command_prefix="!", intents=intents)

# ========================
# 🔹 INTERACTIVE PROPOSAL CREATION


class ProposalMechanismSelectionView(discord.ui.View):
    """View with buttons to select a voting mechanism for a new proposal."""
    def __init__(self, original_interaction: Optional[discord.Interaction], invoker_id: int):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.original_interaction = original_interaction
        self.invoker_id = invoker_id

        mechanisms = [
            ("Plurality", "plurality", "🗳️"),
            ("Borda Count", "borda", "📊"),
            ("Approval Voting", "approval", "👍"),
            ("Runoff Voting", "runoff", "🔄"),
            ("D'Hondt Method", "dhondt", "⚖️"),  # Changed emoji for D'Hondt
        ]

        for i, (label, custom_id_suffix, emoji) in enumerate(mechanisms):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"select_mechanism_{custom_id_suffix}",
                emoji=emoji,
                row=i // 2  # Max 2 buttons per row
            )
            button.callback = self.mechanism_button_callback
            self.add_item(button)

    async def mechanism_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return

        custom_id = interaction.data["custom_id"]
        mechanism_name = custom_id.replace("select_mechanism_", "")

        modal: Optional[discord.ui.Modal] = None
        if mechanism_name == "plurality":
            modal = PluralityProposalModal(interaction, mechanism_name)
        elif mechanism_name == "borda":
            modal = BordaProposalModal(interaction, mechanism_name)
        elif mechanism_name == "approval":
            modal = ApprovalProposalModal(interaction, mechanism_name)
        elif mechanism_name == "runoff":
            modal = RunoffProposalModal(interaction, mechanism_name)
        elif mechanism_name == "dhondt":
            modal = DHondtProposalModal(interaction, mechanism_name)
        # Add other mechanisms here

        if modal:
            await interaction.response.send_modal(modal)
            self.stop()  # Stop this view once a modal is sent
            # Optionally edit the original message to remove buttons or indicate a modal was sent
            try:
                if self.original_interaction: # Check if original_interaction exists
                    await self.original_interaction.edit_original_response(content="Proposal creation form sent. Please fill it out.", view=None)
            except discord.HTTPException:
                pass  # Original interaction might have already been responded to or timed out
        else:
            await interaction.response.send_message(f"Modal for {mechanism_name} not implemented yet.", ephemeral=True)

    async def on_timeout(self):
        if self.original_interaction and not self.original_interaction.is_expired():
            try:
                # Try to edit the original message to indicate timeout
                # This only works if original_interaction was provided and is still valid.
                await self.original_interaction.edit_original_response(content="Proposal mechanism selection timed out. Please run the command again.", view=None)
            except discord.NotFound:
                print("INFO: Original interaction message not found on timeout, likely already deleted or handled.")
            except discord.HTTPException as e:
                print(f"ERROR: Failed to edit original interaction on timeout: {e}")
        else:
            # If no original_interaction or it's expired, we can't edit the message.
            # We might try to send a new message if we had the channel context, but the View itself doesn't store it.
            # For now, just log it.
            print(f"INFO: ProposalMechanismSelectionView timed out for invoker {self.invoker_id}. No original interaction to edit.")
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("You cannot interact with this button.", ephemeral=True)
            return False
        return True

class BaseProposalModal(discord.ui.Modal):
    """Base modal for creating a proposal with common fields."""
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, title: str):
        super().__init__(title=title)
        self.original_interaction = interaction  # The interaction from the button click
        self.mechanism_name = mechanism_name

        self.proposal_title_input = discord.ui.TextInput(
            label="Proposal Title",
            placeholder="Enter a concise title for your proposal",
            min_length=1,
            max_length=100,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.proposal_title_input)

        self.description_input = discord.ui.TextInput(
            label="Proposal Description",
            placeholder="Describe your proposal in detail. Optional. Leave blank if not needed.",
            min_length=0,
            max_length=4000,
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.description_input)

        self.options_input = discord.ui.TextInput(
            label="Options (One Per Line)",
            placeholder="Enter each voting option on a new line.\nDefault: Yes, No (if left blank)",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.options_input)

        self.deadline_input = discord.ui.TextInput(
            label="Voting Duration (e.g., 7d, 24h, 30m)",
            placeholder="Default: 7d (7 days)",
            required=False,
            style=discord.TextStyle.short,
            default="7d"
        )
        self.add_item(self.deadline_input)

    async def common_on_submit(self, interaction: discord.Interaction, specific_hyperparameters: Dict[str, Any]):
        """Common logic for modal submission, to be called by subclasses."""
        try:
            title = self.proposal_title_input.value
            description = self.description_input.value
            options_text = self.options_input.value
            deadline_str = self.deadline_input.value or "7d"

            options = [opt.strip() for opt in options_text.split('\n') if opt.strip()] if options_text else ["Yes", "No"]
            deadline_seconds = utils.parse_duration(deadline_str)
            deadline_days = deadline_seconds / 86400
            # Convert deadline_days back to a suitable string format (ISO 8601 datetime string)
            actual_deadline_datetime = datetime.utcnow() + timedelta(days=deadline_days)
            deadline_db_str = actual_deadline_datetime.strftime('%Y-%m-%d %H:%M:%S.%f') # Format with space

            # Defer the interaction response from the modal submission itself
            await interaction.response.defer(ephemeral=True, thinking=True)

            proposal_id = await _create_new_proposal_entry(
                interaction,  # Pass the modal's interaction object
                title,
                description,
                self.mechanism_name,
                options,
                deadline_db_str, # Pass space-formatted string
                hyperparameters=specific_hyperparameters
            )
            # _create_new_proposal_entry now handles sending followup messages.

        except Exception as e:
            print(f"Error in BaseProposalModal on_submit: {e}")
            traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message(f"An unexpected error occurred: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

class PluralityProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Plurality Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)

        self.winning_threshold_percentage_input = discord.ui.TextInput(
            label="Winning Threshold % (e.g., 40)",
            placeholder="Leave blank for simple majority. Enter 0-100.",
            required=False,
            max_length=3 # Max 3 digits for 0-100
        )
        self.add_item(self.winning_threshold_percentage_input)

    async def on_submit(self, interaction: discord.Interaction):
        allow_abstain = self.allow_abstain_input.value.lower() == 'yes'
        threshold_str = self.winning_threshold_percentage_input.value.strip()
        winning_threshold_percentage = None

        if threshold_str:
            try:
                threshold_val = int(threshold_str)
                if 0 <= threshold_val <= 100:
                    winning_threshold_percentage = threshold_val
                else:
                    # Send an error message and return if input is out of range
                    await interaction.response.send_message("Winning threshold percentage must be between 0 and 100.", ephemeral=True)
                    return
            except ValueError:
                # Send an error message and return if input is not a valid integer
                await interaction.response.send_message("Invalid input for winning threshold percentage. Please enter a number.", ephemeral=True)
                return

        hyperparameters = {
            "allow_abstain": allow_abstain,
            # Use winning_threshold_percentage, which can be None
            "winning_threshold_percentage": winning_threshold_percentage
        }
        # Remove the old "winning_threshold": None
        # hyperparameters.pop("winning_threshold", None) # This line is not needed as we are redefining the dict

        await self.common_on_submit(interaction, hyperparameters)

class BordaProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Borda Count Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes'
        }
        await self.common_on_submit(interaction, hyperparameters)

class ApprovalProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Approval Voting Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes'
        }
        await self.common_on_submit(interaction, hyperparameters)

class RunoffProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Runoff Voting Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes'
        }
        await self.common_on_submit(interaction, hyperparameters)

class DHondtProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New D'Hondt Method Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
        self.num_seats_input = discord.ui.TextInput(
            label="Number of 'Seats' to Allocate (Winners)",
            default="1",
            required=False
        )
        self.add_item(self.num_seats_input)
    async def on_submit(self, interaction: discord.Interaction):
        num_seats_val = self.num_seats_input.value
        try:
            num_seats = int(num_seats_val) if num_seats_val else 1
        except ValueError:
            await interaction.response.send_message("Invalid input for 'Number of Seats'. Must be a number.", ephemeral=True)
            return
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes',
            "num_seats": num_seats
        }
        await self.common_on_submit(interaction, hyperparameters)

# ========================
# 🔹 OLD INTERACTIVE PROPOSAL CREATION (To be removed or refactored)
# ========================

class ProposalModal(discord.ui.Modal):  # This is the OLD modal, will be replaced.
    """Modal form for creating a proposal"""

    def __init__(self, interaction: discord.Interaction):  # Accept interaction directly
        super().__init__(title="Create a Proposal")
        self.interaction = interaction  # Store the interaction
        # self.ctx = ctx # Removed ctx, use interaction instead
        self.voting_mechanism = "plurality"  # Default value
        self.deadline_value = "7d"  # Default value

        # Add form fields
        self.title_input = discord.ui.TextInput(
            label="Title",
            placeholder="Enter a title for your proposal",
            min_length=1,
            max_length=100,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.title_input)

        self.description_input = discord.ui.TextInput(
            label="Description",
            placeholder="Describe your proposal in detail. Optional.",
            min_length=0,
            max_length=4000,
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.description_input)

        self.options_input = discord.ui.TextInput(
            label="Options (one per line)",
            placeholder="Enter each voting option on a new line.\nLeave blank for Yes/No options.",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.options_input)

        self.deadline_input = discord.ui.TextInput(
            label="Deadline",
            placeholder="Enter deadline (e.g., 1d, 12h, 3d, 1w). Default: 7d",
            required=False,
            style=discord.TextStyle.short
        )
        self.add_item(self.deadline_input)

        self.voting_mechanism_input = discord.ui.TextInput(
            label="Voting Mechanism",
            placeholder="Enter voting mechanism (plurality, borda, approval, runoff, dhondt)",
            required=False,
            style=discord.TextStyle.short
        )
        self.add_item(self.voting_mechanism_input)
    # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    # THIS METHOD MUST BE INSIDE THE ProposalModal CLASS
    # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission"""
        try:
            print(f"🔍 {interaction.user} submitted a proposal form")

            title = self.title_input.value
            description = self.description_input.value or "No description provided."
            options_text = self.options_input.value
            voting_mechanism = self.voting_mechanism_input.value.strip().lower()

            # Validate voting mechanism
            valid_mechanisms = ["plurality", "borda",
                                "approval", "runoff", "dhondt"]
            if voting_mechanism not in valid_mechanisms:
                # Use interaction.response.send_message for the *initial* response to the modal
                # This is the correct way to respond immediately to a modal submission.
                await interaction.response.send_message(
                    f"❌ Invalid voting mechanism: `{voting_mechanism}`. Choose from {valid_mechanisms}.",
                    ephemeral=True
                )
                return  # Stop execution if validation fails

            options = [opt.strip() for opt in options_text.split(
                "\n") if opt.strip()] if options_text else ["Yes", "No"]
            deadline_value = self.deadline_input.value or self.deadline_value
            deadline_seconds = utils.parse_duration(
                deadline_value)  # Use utils.parse_duration
            deadline_days = deadline_seconds / 86400

            # Defer the response *after* initial validation if the following steps might take time.
            # This prevents the "Interaction failed" timeout.
            # The modal *should* close when response.defer() is successfully called.
            # Subsequent messages must use interaction.followup.send()
            # If initial validation fails and you send an immediate response, you don't defer.
            await interaction.response.defer(ephemeral=True)
            print(
                f"✅ Deferred response for modal submission from {interaction.user}")

            print(f"📢 Calling create_proposal for user: {interaction.user}")

            # ✅ Call create_proposal, which returns the proposal_id
            # Initialize proposal_id to None before the call
            proposal_id = None
            try:
                # Pass the original interaction object to create_proposal
                proposal_id = await create_proposal(
                    interaction,  # Pass interaction object
                    title, description, voting_mechanism, options, deadline_days
                )

                # create_proposal already handles sending success/failure messages via followup
                # So, no need to send a message here based on proposal_id
                # The messages sent by create_proposal (either success or failure) are the final followups.

            except Exception as e:
                # Handle exceptions during the create_proposal call itself
                print(
                    f"❌ Exception during create_proposal for user {interaction.user}: {e}")
                traceback.print_exc()
                # Use followup to send message after deferring
                await interaction.followup.send(f"❌ An error occurred while creating the proposal: `{str(e)}`. Please check the bot logs.", ephemeral=True)

        except Exception as e:
            # This outer catch handles errors *within* the on_submit method itself,
            # before or after the create_proposal call, but before cleanup.
            print(f"❌ Unexpected error in ProposalModal on_submit: {e}")
            traceback.print_exc()

            # Attempt to send an error message using followup if defer succeeded,
            # or using response if defer failed or didn't happen.
            try:
                # Check if response was already sent (e.g., by initial validation or defer)
                if not interaction.response.is_done():
                    # If response is not done, send an immediate error response
                    await interaction.response.send_message(f"❌ An unexpected error occurred: `{str(e)}`. Please check the logs.", ephemeral=True)
                else:
                    # If response is done (e.g., deferred), use followup
                    await interaction.followup.send(f"❌ An unexpected error occurred: `{str(e)}`. Please check the logs.", ephemeral=True)
            except Exception as followup_e:
                print(
                    f"⚠️ Could not send final error message after unexpected error: {followup_e}")

# Helper function to be called by BaseProposalModal
async def announce_new_proposal(guild, proposal_id, title, description, proposer_id, voting_mechanism, deadline, options, requires_approval):
    """Announce a new proposal in the proposals channel."""
    try:
        print(f"DEBUG: Starting announce_new_proposal for proposal #{proposal_id}")

        # First try to get the channel by name
        proposals_channel = discord.utils.get(guild.text_channels, name="proposals")
        if not proposals_channel:
            print(f"DEBUG: Channel 'proposals' not found by exact name, trying case-insensitive search")
            # Try case-insensitive search
            proposals_channel = discord.utils.get(guild.text_channels,
                                               name=lambda n: n.lower() == "proposals")

        # If still not found, try with utils function
        if not proposals_channel:
            print(f"DEBUG: Channel 'proposals' not found by name search, trying utils.get_or_create_channel")
            proposals_channel = await utils.get_or_create_channel(guild, "proposals", guild.me.id if guild.me else None)

        # Last resort: look for any channel with 'proposal' in the name
        if not proposals_channel:
            print(f"DEBUG: Still couldn't find proposals channel, looking for any channel with 'proposal' in the name")
            for channel in guild.text_channels:
                if 'proposal' in channel.name.lower():
                    proposals_channel = channel
                    print(f"DEBUG: Found channel with 'proposal' in name: #{channel.name}")
                    break

        # If we still can't find a suitable channel, use the first text channel as a fallback
        if not proposals_channel:
            print(f"DEBUG: No proposals channel found, using first available text channel as fallback")
            if guild.text_channels:
                proposals_channel = guild.text_channels[0]
                print(f"DEBUG: Using #{proposals_channel.name} as fallback")
            else:
                print(f"ERROR: No text channels found in guild {guild.name}")
                return False

        print(f"DEBUG: Using channel #{proposals_channel.name} to announce proposal")

        # Create an embed for the proposal
        try:
            print(f"DEBUG: Creating embed for proposal #{proposal_id}")
            embed = discord.Embed(
                title=f"📝 New Proposal #{proposal_id}: {title}",
                description=description if description else "No description provided.",
                color=discord.Color.blue() if requires_approval else discord.Color.green()
            )

            # Add metadata
            embed.add_field(name="Status", value="Pending Approval" if requires_approval else "Voting Open", inline=True)
            embed.add_field(name="Proposer", value=f"<@{proposer_id}>", inline=True)
            embed.add_field(name="Voting Mechanism", value=voting_mechanism.title(), inline=True)

            # Format deadline
            if isinstance(deadline, str):
                try:
                    deadline_dt = datetime.fromisoformat(deadline.replace(' ', 'T').replace('Z', '+00:00'))
                    deadline_str = deadline_dt.strftime("%Y-%m-%d %H:%M UTC")
                except Exception as e:
                    print(f"DEBUG: Error formatting deadline string: {e}")
                    deadline_str = deadline
            else:
                deadline_str = deadline.strftime("%Y-%m-%d %H:%M UTC") if hasattr(deadline, "strftime") else str(deadline)

            embed.add_field(name="Deadline", value=deadline_str, inline=True)

            # Add options
            options_text = "\n".join([f"• {option}" for option in options])
            embed.add_field(name="Options", value=options_text, inline=False)

            # Add footer with timestamp
            embed.set_footer(text=f"Proposal created at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

            print(f"DEBUG: Embed created successfully")
        except Exception as e:
            print(f"ERROR creating embed for proposal #{proposal_id}: {e}")
            traceback.print_exc()
            # Create a simple embed as fallback
            embed = discord.Embed(
                title=f"New Proposal #{proposal_id}",
                description=f"A new proposal has been created: {title}",
                color=discord.Color.blue()
            )

        # Send the announcement
        try:
            print(f"DEBUG: Sending announcement to #{proposals_channel.name}")
            sent_message = await proposals_channel.send(embed=embed)
            print(f"DEBUG: Announcement sent successfully, message ID: {sent_message.id}")

            # Send a direct message as a fallback to ensure visibility
            try:
                await proposals_channel.send(f"**New Proposal #{proposal_id}**: {title}\nCreated by <@{proposer_id}>")
                print(f"DEBUG: Sent plain text announcement as backup")
            except Exception as e:
                print(f"DEBUG: Could not send plain text announcement: {e}")

            return True
        except discord.Forbidden:
            print(f"ERROR: Bot doesn't have permission to send messages in #{proposals_channel.name}")
            # Try to find another channel where we can send
            for channel in guild.text_channels:
                try:
                    await channel.send(f"**IMPORTANT**: Could not announce proposal in #{proposals_channel.name} due to permission issues. New proposal #{proposal_id}: {title}")
                    print(f"DEBUG: Sent permission error notice to #{channel.name}")
                    break
                except:
                    continue
            return False
        except Exception as e:
            print(f"ERROR sending announcement for proposal #{proposal_id}: {e}")
            traceback.print_exc()
            return False
    except Exception as e:
        print(f"CRITICAL ERROR in announce_new_proposal: {e}")
        traceback.print_exc()
        return False

async def _create_new_proposal_entry(interaction: discord.Interaction, title: str, description: str, mechanism_name: str, options: List[str], deadline_db_str: str, hyperparameters: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Handles the actual proposal creation in DB and sends feedback."""
    try:
        print(f"DEBUG: Starting _create_new_proposal_entry for {title}")
        server_id = interaction.guild_id
        proposer_id = interaction.user.id
        description_to_store = description if description.strip() else "No description provided."

        requires_approval = True
        try:
            const_vars = await db.get_constitutional_variables(server_id)
            if const_vars and "proposal_requires_approval" in const_vars:
                requires_approval_val = const_vars["proposal_requires_approval"]["value"]
                if isinstance(requires_approval_val, str):
                    requires_approval = requires_approval_val.lower() == "true"
                elif isinstance(requires_approval_val, (bool, int)): # Handle if it's already bool or 0/1
                    requires_approval = bool(requires_approval_val)
                print(f"DEBUG: Fetched requires_approval={requires_approval} from constitutional variables")
        except Exception as e_cv:
            print(f"Notice: Could not fetch constitutional_vars for proposal_requires_approval, defaulting to True. Error: {e_cv}")

        print(f"DEBUG: Creating proposal in database: {title}")
        proposal_id = await db.create_proposal(
            server_id,
            proposer_id,
            title,
            description_to_store,
            mechanism_name,
            deadline_db_str,
            requires_approval=requires_approval, # This will set status to "Pending" if true
            hyperparameters=hyperparameters
        )

        if proposal_id:
            print(f"DEBUG: Proposal created with ID {proposal_id}, storing options")
            await db.add_proposal_options(proposal_id, options)

            user_feedback_message = f"✅ Proposal #{proposal_id} ('{title}') created successfully!"

            if requires_approval:
                user_feedback_message += " It has been submitted for admin approval."

                # Send a specific notification to an admin channel with Approve/Reject buttons
                admin_channel_name = "proposals" # Or a dedicated admin channel like "admin-notifications"
                admin_channel = await utils.get_or_create_channel(interaction.guild, admin_channel_name, interaction.client.user.id)

                if admin_channel:
                    admin_embed = discord.Embed(
                        title=f"🆕 Proposal Submitted for Approval: #{proposal_id} - {title}",
                        description=f"Proposed by: {interaction.user.mention}\n\n**Description:**\n{description_to_store}",
                        color=discord.Color.orange()
                    )
                    admin_embed.add_field(name="Voting Mechanism", value=mechanism_name.title(), inline=True)
                    admin_embed.add_field(name="Options", value="\n".join([f"• {opt}" for opt in options]) or "Default: Yes/No", inline=False)
                    admin_embed.add_field(name="Requested Voting Deadline", value=utils.format_deadline(deadline_db_str), inline=True)

                    # Display hyperparameters if they exist
                    if hyperparameters:
                        hyperparams_text_parts = []
                        if "allow_abstain" in hyperparameters:
                            hyperparams_text_parts.append(f"Allow Abstain: {'Yes' if hyperparameters['allow_abstain'] else 'No'}")
                        if "winning_threshold_percentage" in hyperparameters and hyperparameters["winning_threshold_percentage"] is not None:
                            hyperparams_text_parts.append(f"Winning Threshold: {hyperparameters['winning_threshold_percentage']}%" )
                        # Add other hyperparameter displays here as they are implemented for other mechanisms

                        if hyperparams_text_parts:
                            admin_embed.add_field(name="Voting Rules", value="\n".join(hyperparams_text_parts), inline=False)
                        elif mechanism_name == "plurality": # Specifically for plurality, if no threshold is set, mention simple majority
                             admin_embed.add_field(name="Voting Rules", value="Winning Threshold: Simple Majority", inline=False)


                    admin_embed.set_footer(text=f"Proposal ID: {proposal_id} | Submitted at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

                    current_view = AdminApprovalView(proposal_id=proposal_id, bot_instance=interaction.client) # interaction.client is the bot instance
                    await admin_channel.send(
                        content=f"Admins, a new proposal requires your attention. Please Approve or Reject:", # Consider pinging an admin role here
                        embed=admin_embed,
                        view=current_view
                    )
                    print(f"DEBUG: Sent approval request with buttons for P#{proposal_id} to #{admin_channel.name}")
                else:
                    print(f"ERROR: Could not find admin channel '{admin_channel_name}' to send approval request for P#{proposal_id}")
                    user_feedback_message += " However, there was an issue notifying admins."

            else: # Does not require approval, voting starts immediately
                user_feedback_message += " Voting has started."
                # Announce voting started in public channels (as it was before, now consolidated from _perform_approve)
                proposals_public_channel_name = "proposals"
                proposals_public_channel = await utils.get_or_create_channel(interaction.guild, proposals_public_channel_name, interaction.client.user.id)
                if proposals_public_channel:
                    proposer_member = interaction.guild.get_member(proposer_id) or proposer_id
                    public_embed = utils.create_proposal_embed(
                        proposal_id, proposer_member, title, description_to_store,
                        mechanism_name, deadline_db_str, "Voting", options,
                        hyperparameters=hyperparameters # Pass hyperparameters
                    )
                    await proposals_public_channel.send(content=f"🎉 Voting has started for Proposal #{proposal_id}!", embed=public_embed)

                voting_room_channel_name = "voting-room"
                voting_room_channel = await utils.get_or_create_channel(interaction.guild, voting_room_channel_name, interaction.client.user.id)
                if voting_room_channel:
                    await voting_utils.update_vote_tracking(interaction.guild, proposal_id)

            await interaction.followup.send(user_feedback_message, ephemeral=False) # Send to the user who created the proposal
            print(f"✅ Proposal #{proposal_id} by {interaction.user} processed. Followup sent.")
            return proposal_id
        else:
            print(f"ERROR: Failed to create proposal in database for {title}")
            await interaction.followup.send("❌ Failed to create proposal in the database.", ephemeral=True)
            return None

    except Exception as e:
        print(f"ERROR in _create_new_proposal_entry: {e}")
        traceback.print_exc()
        if interaction and not interaction.is_expired():
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"An error occurred while finalizing the proposal: {e}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"An error occurred while finalizing the proposal: {e}", ephemeral=True)
            except discord.HTTPException as http_e:
                print(f"Error sending followup error message in _create_new_proposal_entry: {http_e}")
        return None


async def notify_admins_of_pending_proposals(guild):
    """Send notification to admins about pending proposals"""
    try:
        print(f"DEBUG: Starting notify_admins_of_pending_proposals for guild {guild.name}")

        # Get all pending proposals
        pending_proposals = await db.get_server_proposals(guild.id, "Pending")
        if not pending_proposals:
            print(f"DEBUG: No pending proposals found for guild {guild.name}")
            return

        print(f"DEBUG: Found {len(pending_proposals)} pending proposals")

        # Get admin role
        admin_role = discord.utils.get(guild.roles, name="Admin")
        if not admin_role:
            print(f"DEBUG: Admin role not found in guild {guild.name}, looking for administrator users")
            # Fallback: try to find users with administrator permissions
            admin_users = [member for member in guild.members if member.guild_permissions.administrator and not member.bot]
            if not admin_users:
                print(f"WARNING: No admin role or admin users found in guild {guild.name}")
                return
            admin_mentions = " ".join([user.mention for user in admin_users[:5]])  # Limit to 5 mentions
        else:
            admin_mentions = admin_role.mention
            print(f"DEBUG: Found Admin role with ID {admin_role.id}")

        # Get proposals channel
        proposals_channel = discord.utils.get(guild.text_channels, name="proposals")
        if not proposals_channel:
            print(f"DEBUG: Proposals channel not found by name, trying case-insensitive search")
            # Try case-insensitive search
            proposals_channel = discord.utils.get(guild.text_channels,
                                               name=lambda n: n.lower() == "proposals")

        # If still not found, try with utils function
        if not proposals_channel:
            print(f"DEBUG: Proposals channel not found by name search, trying utils.get_or_create_channel")
            proposals_channel = await utils.get_or_create_channel(guild, "proposals", guild.me.id if guild.me else None)

        # Last resort: look for any channel with 'proposal' in the name
        if not proposals_channel:
            print(f"DEBUG: Still couldn't find proposals channel, looking for any channel with 'proposal' in the name")
            for channel in guild.text_channels:
                if 'proposal' in channel.name.lower():
                    proposals_channel = channel
                    print(f"DEBUG: Found channel with 'proposal' in name: #{channel.name}")
                    break

        # If we still can't find a suitable channel, use the first text channel as a fallback
        if not proposals_channel:
            print(f"DEBUG: No proposals channel found, trying to use general channel")
            proposals_channel = discord.utils.get(guild.text_channels, name="general")
            if not proposals_channel and guild.text_channels:
                proposals_channel = guild.text_channels[0]
                print(f"DEBUG: Using #{proposals_channel.name} as fallback")
            else:
                print(f"ERROR: No text channels found in guild {guild.name}")
                return

        print(f"DEBUG: Using channel #{proposals_channel.name} to notify admins")

        # Create notification embed
        try:
            print(f"DEBUG: Creating notification embed")
            embed = discord.Embed(
                title="🔔 Pending Proposals Require Approval",
                description=f"There are {len(pending_proposals)} proposals waiting for admin approval.",
                color=discord.Color.orange()
            )

            # Add each proposal
            for proposal in pending_proposals[:5]:  # Limit to 5 to avoid embed limits
                embed.add_field(
                    name=f"#{proposal['proposal_id']}: {proposal['title']}",
                    value=f"Proposed by <@{proposal['proposer_id']}>",
                    inline=False
                )

            print(f"DEBUG: Notification embed created successfully")
        except Exception as e:
            print(f"ERROR creating notification embed: {e}")
            traceback.print_exc()
            # Create a simple embed as fallback
            embed = discord.Embed(
                title="🔔 Pending Proposals",
                description=f"There are {len(pending_proposals)} proposals waiting for approval.",
                color=discord.Color.orange()
            )

        # Send notification
        try:
            print(f"DEBUG: Sending notification to #{proposals_channel.name}")
            await proposals_channel.send(
                content=f"{admin_mentions} Please review pending proposals",
                embed=embed
            )
            print(f"DEBUG: Admin notification sent successfully")
            return True
        except discord.Forbidden:
            print(f"ERROR: Bot doesn't have permission to send messages in #{proposals_channel.name}")
            # Try to find another channel where we can send
            for channel in guild.text_channels:
                try:
                    await channel.send(f"**IMPORTANT**: Could not notify admins in #{proposals_channel.name} due to permission issues. There are {len(pending_proposals)} pending proposals.")
                    print(f"DEBUG: Sent permission error notice to #{channel.name}")
                    break
                except:
                    continue
            return False
        except Exception as e:
            print(f"ERROR sending admin notification: {e}")
            traceback.print_exc()
            return False
    except Exception as e:
        print(f"CRITICAL ERROR in notify_admins_of_pending_proposals: {e}")
        traceback.print_exc()
        return False


class EarlyTerminationView(discord.ui.View):
    """View for early termination of proposals"""
    def __init__(self, proposal_id):
        super().__init__(timeout=None)  # No timeout for persistent views
        self.proposal_id = proposal_id

    @discord.ui.button(label="🛑 Terminate Early", style=discord.ButtonStyle.danger)
    async def terminate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if the user has permission
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You don't have permission to terminate proposals early!", ephemeral=True)
            return

        # Confirm termination
        await interaction.response.send_message(
            "Are you sure you want to terminate this proposal early and count the votes?",
            view=ConfirmTerminationView(self.proposal_id),
            ephemeral=True
        )

class ConfirmTerminationView(discord.ui.View):
    """View for confirming early termination"""
    def __init__(self, proposal_id):
        super().__init__(timeout=60)
        self.proposal_id = proposal_id

    @discord.ui.button(label="✅ Yes, terminate now", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        # Process early termination
        await terminate_proposal_early(interaction, self.proposal_id)

    @discord.ui.button(label="❌ No, keep voting", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Just close the confirmation dialog
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Early termination cancelled.", view=self)

async def terminate_proposal_early(interaction, proposal_id):
    """Terminate a proposal early and count the votes"""
    # Get the proposal
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await interaction.followup.send(f"❌ Proposal #{proposal_id} not found.")
        return

    if proposal['status'] != "Voting":
        await interaction.followup.send(f"❌ Proposal #{proposal_id} is not in voting status.")
        return

    # Import voting module here to avoid circular imports
    from voting_utils import close_proposal

    # Close the proposal
    results = await close_proposal(proposal_id)

    # Announce the results
    await voting_utils.close_and_announce_results(interaction.guild, proposal, results)

    # Send confirmation message
    await interaction.followup.send(f"✅ Proposal #{proposal_id} has been terminated early and the votes have been counted.")

    # Log to audit channel
    audit_channel = discord.utils.get(interaction.guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"🛑 **Proposal Terminated Early**: #{proposal_id} '{proposal['title']}' has been terminated early by {interaction.user.mention}")

async def start_proposal_creation_flow(ctx: commands.Context):
    """Starts the new two-step proposal creation flow."""
    try:
        # Check if the command was invoked in a guild or DM
        if ctx.guild is None:
            await ctx.send("The `!propose` command can only be used in a server channel, not in DMs.")
            print(f"INFO: {ctx.author} attempted to use !propose in DMs.")
            return

        print(f"🔍 {ctx.author} invoked proposal creation in {ctx.guild.name}")

        # For the new UI flow with ProposalMechanismSelectionView, we need an interaction
        # to be able to edit the original message later (e.g., on timeout or after selection).
        # If ctx.interaction is None (typical for pure message commands without a prior interaction),
        # editing the message that contains the view might be problematic from the view itself.
        # The view callbacks (button clicks) will generate their own interactions.

        initial_interaction = ctx.interaction # This will be None if it's a pure message command.

        # Pass the invoker's ID to the view for interaction checks.
        # Pass the initial_interaction (which might be None) for potential message editing on timeout.
        view = ProposalMechanismSelectionView(original_interaction=initial_interaction, invoker_id=ctx.author.id)

        if initial_interaction:
            # If we have an interaction, we can respond to it directly and the view can edit this response.
            await initial_interaction.response.send_message("Please select a voting mechanism for your proposal:", view=view, ephemeral=False) # Send non-ephemeral so others can see it if desired
            print(f"✅ Sent ProposalMechanismSelectionView to {ctx.author} via interaction response.")
        else:
            # If it's a message command (no initial interaction), send a new message with the view.
            # The view won't be able to easily edit *this* message on timeout without more complex state management.
            # However, button clicks on the view will still work and create their own interactions.
            sent_message = await ctx.send("Please select a voting mechanism for your proposal:", view=view)
            print(f"✅ Sent ProposalMechanismSelectionView to {ctx.author} via new message (ID: {sent_message.id}).")
            # We could store sent_message.id if we wanted to try and edit/delete it later,
            # but that requires more complex handling within the view or a separate manager.
            # For now, on timeout, if original_interaction was None, the view just stops without editing a message.

    except Exception as e:
        print(f"❌ Error in start_proposal_creation_flow: {e}")
        traceback.print_exc()
        error_message = f"❌ An error occurred: {e}"
        try:
            if initial_interaction and not initial_interaction.response.is_done():
                await initial_interaction.response.send_message(error_message, ephemeral=True)
            elif ctx.channel: # Check if ctx.channel is not None
                await ctx.send(error_message)
            else: # Fallback if no channel context (e.g., if ctx was somehow None, very unlikely)
                print(f"CRITICAL: Could not send error to user {ctx.author.id if ctx.author else 'Unknown User'} in start_proposal_creation_flow")
        except Exception as e_report:
            print(f"Error sending error report: {e_report}")
            pass

# New View for Admin Approval Buttons
class AdminApprovalView(discord.ui.View):
    def __init__(self, proposal_id: int, bot_instance: commands.Bot):
        super().__init__(timeout=None) # Persist until manually stopped or message deleted
        self.proposal_id = proposal_id
        self.bot = bot_instance

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="admin_approve_proposal")
    async def approve_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to approve proposals.", ephemeral=True)
            return

        # Defer to prevent interaction timeout if action takes time
        await interaction.response.defer()

        success, message_content = await _perform_approve_proposal_action(interaction, self.proposal_id, self.bot)

        # Edit the original message with the outcome
        original_embed = interaction.message.embeds[0] if interaction.message.embeds else None
        new_embed = None
        if original_embed:
            new_embed = original_embed.copy()
            if success:
                new_embed.colour = discord.Color.green()
                new_embed.set_field_at(0, name="Status", value=f"Approved by {interaction.user.mention}", inline=True) # Assuming status is first field
            else:
                new_embed.colour = discord.Color.red()
            # Remove or update other fields as necessary, e.g., a footer indicating action taken
            new_embed.set_footer(text=f"Actioned by {interaction.user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

        # Disable buttons
        for item in self.children:
            item.disabled = True

        await interaction.message.edit(content=message_content, embed=new_embed, view=self)
        # Optionally send a followup if more details are needed or if the original message edit isn't enough
        # await interaction.followup.send(message_content, ephemeral=True if not success else False)


    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="admin_reject_proposal")
    async def reject_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to reject proposals.", ephemeral=True)
            return

        # Send modal to get reason
        modal = RejectReasonModal(proposal_id=self.proposal_id, original_button_interaction=interaction, bot_instance=self.bot, parent_view=self)
        await interaction.response.send_modal(modal)

# New Modal for Rejection Reason
class RejectReasonModal(discord.ui.Modal, title="Reject Proposal"):
    def __init__(self, proposal_id: int, original_button_interaction: discord.Interaction, bot_instance: commands.Bot, parent_view: AdminApprovalView):
        super().__init__()
        self.proposal_id = proposal_id
        self.original_button_interaction = original_button_interaction
        self.bot = bot_instance
        self.parent_view = parent_view

        self.reason_input = discord.ui.TextInput(
            label="Reason for Rejection",
            style=discord.TextStyle.paragraph,
            placeholder="Please provide a reason for rejecting this proposal.",
            required=True,
            min_length=10
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer() # Defer immediately
        reason = self.reason_input.value

        success, message_content = await _perform_reject_proposal_action(interaction, self.proposal_id, reason, self.bot)

        original_embed = self.original_button_interaction.message.embeds[0] if self.original_button_interaction.message.embeds else None
        new_embed = None
        if original_embed:
            new_embed = original_embed.copy()
            if success:
                new_embed.colour = discord.Color.red()
                new_embed.set_field_at(0, name="Status", value=f"Rejected by {interaction.user.mention}", inline=True)
                new_embed.add_field(name="Rejection Reason", value=reason, inline=False)
            # Remove or update other fields as necessary
            new_embed.set_footer(text=f"Actioned by {interaction.user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

        # Disable buttons on the original view
        for item in self.parent_view.children:
            item.disabled = True

        await self.original_button_interaction.message.edit(content=message_content, embed=new_embed, view=self.parent_view)
        # await interaction.followup.send(message_content, ephemeral=True) # Send a confirmation of the modal submission


async def _perform_approve_proposal_action(interaction_or_ctx: Union[discord.Interaction, commands.Context], proposal_id: int, bot_instance: commands.Bot) -> tuple[bool, str]:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if isinstance(interaction_or_ctx, discord.Interaction) else interaction_or_ctx.author

    try:
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, f"❌ Proposal #{proposal_id} not found."

        if proposal['status'] not in ["Pending", "Pending Approval"]:
            return False, f"❌ Proposal #{proposal_id} is not pending approval (current status: {proposal['status']})."

        await db.update_proposal_status(proposal_id, "Voting")

        # Announce in proposals channel
        proposals_channel_name = "proposals"
        proposals_channel = await utils.get_or_create_channel(guild, proposals_channel_name, bot_instance.user.id)

        if proposals_channel:
            options = await db.get_proposal_options(proposal_id)
            option_names = options if options else ["Yes", "No"]
            proposer_member = guild.get_member(proposal['proposer_id']) or proposal['proposer_id']

            embed = utils.create_proposal_embed(
                proposal_id, proposer_member, proposal['title'], proposal['description'],
                proposal['voting_mechanism'], proposal['deadline'], "Voting", option_names,
                hyperparameters=proposal.get('hyperparameters') # Pass hyperparameters
            )
            await proposals_channel.send(content=f"🎉 Voting has started for Proposal #{proposal_id}!", embed=embed)
        else:
            print(f"Warning: Could not find '{proposals_channel_name}' channel to announce vote start for P#{proposal_id}.")


        voting_room_channel_name = "voting-room"
        voting_room_channel = await utils.get_or_create_channel(guild, voting_room_channel_name, bot_instance.user.id)
        if voting_room_channel:
            await voting_utils.update_vote_tracking(guild, proposal_id) # This sends the tracking message
            # await voting_room_channel.send(f"🗳️ Voting for Proposal #{proposal_id} ('{proposal['title']}') is now open...") # Redundant if update_vote_tracking handles it well
        else:
            print(f"Warning: Could not find '{voting_room_channel_name}' for P#{proposal_id}.")

        # Send DMs to eligible voters
        dm_info_message = "" # For appending to the final success message
        try:
            # Refetch full proposal details as 'proposal' might be stale or incomplete for DM sending
            full_proposal_details_for_dm = await db.get_proposal(proposal_id)
            if not full_proposal_details_for_dm:
                print(f"ERROR: P#{proposal_id} - Could not refetch proposal details before sending DMs.")
                dm_info_message = " (DM sending skipped: proposal details unavailable)"
            else:
                # Use the fresh details for getting eligible voters
                eligible_voters_list = await voting_utils.get_eligible_voters(guild, full_proposal_details_for_dm)

                # Fetch proposal options as a list of strings
                proposal_options_dicts = await db.get_proposal_options(proposal_id)
                # Ensure option_names_list is correctly formed (list of strings)
                option_names_list = proposal_options_dicts if proposal_options_dicts else ["Yes", "No"]

                if not eligible_voters_list:
                    print(f"INFO: P#{proposal_id} - No eligible voters found to send DMs.")
                    dm_info_message = " (No eligible voters for DMs)"
                else:
                    print(f"INFO: P#{proposal_id} - Attempting to send voting DMs to {len(eligible_voters_list)} eligible members.")
                    successful_dms_count = 0
                    failed_dms_count = 0
                    for member_to_dm in eligible_voters_list:
                        # get_eligible_voters should already filter out bots, but this is a safeguard.
                        if member_to_dm.bot:
                            print(f"DEBUG: P#{proposal_id} - Skipping DM to bot user {member_to_dm.name} ({member_to_dm.id}).")
                            continue

                        # Use the fresh full_proposal_details_for_dm for sending DMs
                        dm_sent_successfully = await voting.send_voting_dm(member_to_dm, full_proposal_details_for_dm, option_names_list)
                        if dm_sent_successfully:
                            successful_dms_count += 1
                            # Record that this voter was invited/DM'd
                            await db.add_voting_invite(proposal_id, member_to_dm.id)
                        else:
                            failed_dms_count += 1

                    dm_info_message = f" ({successful_dms_count} DMs sent, {failed_dms_count} failed)"
                    print(f"INFO: P#{proposal_id} - DM sending complete: {successful_dms_count} successful, {failed_dms_count} failed.")
        except Exception as e_dm:
            print(f"ERROR: P#{proposal_id} - An error occurred during the DM sending process: {e_dm}")
            traceback.print_exc()
            dm_info_message = " (Error during DM sending)"

        audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
        if audit_channel:
            await audit_channel.send(f"✅ **Proposal Approved**: #{proposal_id} ('{proposal['title']}') approved by {user.mention}. Voting started.{dm_info_message}")

        return True, f"✅ Proposal #{proposal_id} ('{proposal['title']}') has been approved and voting has started!{dm_info_message}"

    except Exception as e:
        print(f"Error in _perform_approve_proposal_action for P#{proposal_id}: {e}")
        traceback.print_exc()
        return False, f"❌ An error occurred while approving proposal #{proposal_id}: {e}"

async def _perform_reject_proposal_action(interaction_or_ctx: Union[discord.Interaction, commands.Context], proposal_id: int, reason: str, bot_instance: commands.Bot) -> tuple[bool, str]:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if isinstance(interaction_or_ctx, discord.Interaction) else interaction_or_ctx.author

    try:
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, f"❌ Proposal #{proposal_id} not found."

        if proposal['status'] not in ["Pending", "Pending Approval"]:
            return False, f"❌ Proposal #{proposal_id} cannot be rejected (current status: {proposal['status']})."

        await db.update_proposal_status(proposal_id, "Rejected")

        try:
            proposer = await guild.fetch_member(proposal['proposer_id'])
            if proposer:
                await proposer.send(f"Your proposal #{proposal_id} ('{proposal['title']}') was rejected by an administrator. Reason: {reason}")
        except discord.NotFound:
            print(f"Could not find proposer {proposal['proposer_id']} to notify of rejection for P#{proposal_id}.")
        except discord.Forbidden:
            print(f"Could not DM proposer {proposal['proposer_id']} (DMs disabled/blocked) for P#{proposal_id}.")

        audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
        if audit_channel:
            await audit_channel.send(f"🚫 **Proposal Rejected**: #{proposal_id} ('{proposal['title']}') rejected by {user.mention}. Reason: {reason}")

        return True, f"❌ Proposal #{proposal_id} ('{proposal['title']}') has been rejected. Reason: {reason}"

    except Exception as e:
        print(f"Error in _perform_reject_proposal_action for P#{proposal_id}: {e}")
        traceback.print_exc()
        return False, f"❌ An error occurred while rejecting proposal #{proposal_id}: {e}"

# Refactored command handlers
async def approve_proposal(ctx: commands.Context, proposal_id: int):
    """Approve a pending proposal and start voting. (Command)"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("You do not have permission to approve proposals.")
        return
    success, message = await _perform_approve_proposal_action(ctx, proposal_id, ctx.bot)
    await ctx.send(message)

async def reject_proposal(ctx: commands.Context, proposal_id: int, *, reason: str):
    """Reject a pending proposal. (Command)"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("You do not have permission to reject proposals.")
        return
    success, message = await _perform_reject_proposal_action(ctx, proposal_id, reason, ctx.bot)
    await ctx.send(message)
