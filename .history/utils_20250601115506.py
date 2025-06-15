# Create a new file called utils.py
# Move functions like:
# - extract_options_from_description
# - parse_duration
# - format_time_remaining
# - create_progress_bar
# - get_or_create_channel # This one interacts with discord.py, maybe keep it closer? Let's move for now and adjust if needed.
# - create_proposal_embed # This one interacts with discord.py, maybe keep it closer? Let's move for now and adjust if needed.
# - format_deadline # Used by voting_utils, move here.

# In utils.py:

import discord
from datetime import datetime, timedelta
import re
import db
import math  # Needed for create_progress_bar
from typing import List, Optional, Union, Dict, Any, Tuple

# ... (keep your existing imports and code above this) ...


def format_time_remaining(time_delta: timedelta) -> str:
    """
    Format a timedelta object into a human-readable string (e.g., '2 days, 3 hours').
    Handles past dates gracefully.
    """
    total_seconds = int(time_delta.total_seconds())

    if total_seconds <= 0:
        return "Expired"

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days > 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
    if minutes > 0 and not days:  # Only show minutes if less than a day
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    # Optionally show seconds for very short durations, or if no larger units
    # if seconds > 0 and not days and not hours and not minutes:
    #    parts.append(f"{seconds} second{'s' if seconds > 1 else ''}")

    if not parts:
        # Fallback for durations less than a minute but > 0 seconds
        if total_seconds > 0:
            return f"{total_seconds} second{'s' if total_seconds > 1 else ''}"
        return "Expired"  # Should not be reached if total_seconds > 0

    # Join the parts, showing at most the top two units
    return ", ".join(parts[:2])


def create_progress_bar(percentage: Union[int, float], length: int = 20) -> str:
    """
    Creates a simple text-based progress bar.

    Args:
        percentage: The percentage complete (0-100).
        length: The total length of the progress bar string.

    Returns:
        A string representing the progress bar.
    """
    percentage = max(0, min(100, percentage)
                     )  # Clamp percentage between 0 and 100
    # Use ceil to show at least 1 block for >0%
    filled_length = math.ceil(length * percentage // 100)
    bar = '▓' * filled_length + '░' * (length - filled_length)
    return f"[{bar}]"


def extract_options_from_description(description: str) -> Optional[List[str]]:
    """
    Extracts voting options from a text description.
    Looks for bullet points or specific keywords.

    Args:
        description: The text description of the proposal.

    Returns:
        A list of extracted options (strings), or None if no obvious options found.
    """
    if not description:
        return None

    # Look for bulleted lists (-, *, •)
    # This regex looks for lines starting with common bullet characters followed by whitespace
    # and captures the text after the bullet. It finds multiple such lines in a block.
    bullet_pattern = r'^[\s]*[*-•]\s*(.+)$'
    lines = description.split('\n')
    options = []
    # Optional: if you want to require a heading like "Options:"
    in_options_section = False
    # You could track if you are in an "Options:" block.
    # For now, check any line.

    for line in lines:
        match = re.match(bullet_pattern, line.strip())
        if match:
            option = match.group(1).strip()
            if option:  # Ensure extracted option is not empty
                options.append(option)
        # Optional: check for specific header to limit search
        # if re.search(r'^\s*(Options|Choices):\s*$', line.strip(), re.IGNORECASE):
        #     in_options_section = True
        # elif in_options_section and not line.strip(): # Blank line ends the section
        #     in_options_section = False

    if options:
        # Remove duplicates while preserving order as much as possible (using dict.fromkeys)
        # Then filter out empty strings
        unique_options = list(dict.fromkeys(options))
        return [opt for opt in unique_options if opt]

    # If no bullet points, check for common Yes/No keywords (less reliable)
    # Check for presence of common vote keywords like yes, no, for, against, approve, reject
    # This check is a fallback and might produce false positives if these words
    # appear incidentally in the description without being options.
    # You might prefer to *only* rely on bullet points.
    common_keywords = re.search(
        r'\b(?:yes|no|for|against|approve|reject)\b', description, re.IGNORECASE)
    if common_keywords:
        # This is a weak signal, maybe return None or just default ["Yes", "No"] later
        # The original logic just returned ["Yes", "No"] if this matched.
        # Let's match that behavior for now, although relying solely on structured lists is better.
        print("DEBUG: Found common voting keywords, suggesting Yes/No options.")
        # Return default Yes/No if keywords found and no bullet points
        return ["Yes", "No"]

    # If neither structured list nor keywords are found, return None
    return None


def parse_duration(duration_str):
    """Parse a duration string (e.g., '1d', '2h', '30m', '1w') into seconds"""
    if not duration_str:
        return 7 * 86400  # Default to 7 days if empty

    total_seconds = 0
    pattern = r'(\d+)([wdhms])'

    # First, try parsing with the regex
    for match in re.finditer(pattern, duration_str.lower()):
        value, unit = match.groups()
        value = int(value)

        if unit == 'w':
            total_seconds += value * 604800  # weeks to seconds
        elif unit == 'd':
            total_seconds += value * 86400   # days to seconds
        elif unit == 'h':
            total_seconds += value * 3600    # hours to seconds
        elif unit == 'm':
            total_seconds += value * 60      # minutes to seconds
        elif unit == 's':
            total_seconds += value           # seconds

    # If regex didn't find anything (total_seconds is still 0), try parsing as a simple number of days
    if total_seconds == 0:
        try:
            days = int(duration_str.strip())
            total_seconds = days * 86400
        except ValueError:
            # If parsing as a simple number of days also fails, default to 7 days
            print(
                f"DEBUG: Could not parse duration '{duration_str}' as regex or simple int. Defaulting to 7 days.")
            total_seconds = 7 * 86400  # <-- FIX: Set the default value here, ensuring it's returned

    return total_seconds  # Return the calculated or default total_seconds


def format_deadline(deadline_data):
    """Format the deadline data (string, datetime, or None) for display"""
    if isinstance(deadline_data, str):
        try:
            # Attempt to parse the string. replace('Z', '+00:00') handles ISO Z timezone.
            deadline_dt = datetime.fromisoformat(
                deadline_data.replace('Z', '+00:00'))
            return deadline_dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            print(
                f"ERROR: Could not parse deadline string in format_deadline: '{deadline_data}'")
            return "Invalid Date"
    elif isinstance(deadline_data, datetime):
        # It's already a datetime object (if detect_types worked)
        return deadline_data.strftime("%Y-%m-%d %H:%M UTC")
    else:
        # Handle None or unexpected types
        return "Not Set"

# Added bot_user_id arg
async def get_or_create_channel(guild, channel_name, bot_user_id=None):
    """Get or create a channel with the given name and set default permissions."""
    channel = discord.utils.get(guild.text_channels, name=channel_name)

    if not channel:
        print(
            f"Channel '{channel_name}' not found in '{guild.name}', creating...")
        # Define default overwrites: Deny send messages for everyone, allow read; allow bot to send
        # This is a safer default, then specific channels can override.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
        }
        # Add bot permissions if bot_user_id is provided
        bot_member = guild.me  # Get the bot's member object
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True)
        else:
            # Fallback: allow Admin role to send if bot's member object isn't found
            admin_role = discord.utils.get(guild.roles, name="Admin")
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True)

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                topic=f"Channel for {channel_name.replace('-', ' ')}."
            )
            print(f"Created channel #{channel_name} in '{guild.name}'.")

            # Send initial message and adjust specific permissions *after* creation
            if channel_name == "proposals":
                # Allow everyone to send messages in proposals channel
                # Need to fetch the channel object again after creation to set perms
                created_channel = discord.utils.get(
                    guild.text_channels, name=channel_name)
                if created_channel:
                    await created_channel.set_permissions(guild.default_role, send_messages=True)
                    await created_channel.send("📜 **Proposals Channel**\nThis channel is for posting and discussing governance proposals. Use the `!propose` command to start a new proposal.")
            elif channel_name == "voting-room":
                # Keep send_messages=False for everyone, voting is via DM/buttons on announcement
                created_channel = discord.utils.get(
                    guild.text_channels, name=channel_name)
                if created_channel:
                    await created_channel.send("🗳️ **Voting Room**\nThis channel announces active votes. You will receive DMs to cast your votes.")
            elif channel_name == "governance-results":
                # Keep send_messages=False for everyone here
                created_channel = discord.utils.get(
                    guild.text_channels, name=channel_name)
                if created_channel:
                    await created_channel.send("📊 **Governance Results**\nThis channel shows the results of completed votes.")
            elif channel_name == "audit-log":
                # Keep send_messages=False for everyone here, bot only
                created_channel = discord.utils.get(
                    guild.text_channels, name=channel_name)
                if created_channel:
                    await created_channel.send("🔒 **Audit Log Channel**\nThis channel logs important governance actions and permission changes.")
            # Add other channels as needed

        except discord.Forbidden:
            print(
                f"ERROR: Bot does not have permissions to create channel '{channel_name}' in '{guild.name}'.")
            return None
        except Exception as e:
            print(
                f"ERROR creating channel '{channel_name}' in '{guild.name}': {e}")
            return None

    else:
        print(
            f"Channel '{channel_name}' already exists in '{guild.name}'. Ensuring permissions...")
        # Ensure default permissions are correct if channel already existed
        try:
            # Deny send messages for everyone
            await channel.set_permissions(guild.default_role, send_messages=False, read_messages=True)
            # Allow bot to send
            bot_member = guild.me  # Get the bot's member object
            if bot_member:
                await channel.set_permissions(bot_member, send_messages=True, read_messages=True)
            else:
                # Fallback: allow Admin role to send if bot's member object isn't found
                admin_role = discord.utils.get(guild.roles, name="Admin")
                if admin_role:
                    await channel.set_permissions(admin_role, send_messages=True, read_messages=True)

            # Specific channel overrides
            if channel_name == "proposals":
                # Allow everyone to send
                await channel.set_permissions(guild.default_role, send_messages=True)
            # voting-room, governance-results, audit-log should remain send=False for @everyone

        except discord.Forbidden:
            print(
                f"ERROR: Bot does not have permissions to update channel permissions for '{channel_name}' in '{guild.name}'.")
        except Exception as e:
            print(
                f"ERROR updating permissions for channel '{channel_name}' in '{guild.name}': {e}")

    return channel


def create_proposal_embed(proposal_id, proposer, title, description, voting_mechanism, deadline, status, options, results=None, requires_approval=None, hyperparameters: Optional[Dict[str, Any]] = None, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, campaign_title: Optional[str] = None):
    """Creates a more detailed embed for a proposal, potentially including campaign context and hyperparameters."""
    color = discord.Color.blue()
    if status == "Voting":
        color = discord.Color.green()
    elif status == "Pending Approval" or status == "Pending":
        color = discord.Color.orange()
    elif status == "Closed" or status == "Rejected":
        color = discord.Color.dark_grey()
    elif status == "ApprovedScenario": # New status for campaign scenarios that are approved but not yet active
        color = discord.Color.teal() # A distinct color for this state

    proposer_mention = proposer.mention if isinstance(proposer, discord.Member) else f"<@{proposer}>"
    proposer_name = proposer.display_name if isinstance(proposer, discord.Member) else f"User ID: {proposer}"

    embed_title = f"Proposal #{proposal_id}: {title}"
    if campaign_id and scenario_order and campaign_title:
        embed_title = f"Campaign '{campaign_title}' (C:{campaign_id}) - Scenario {scenario_order} (P:{proposal_id}): {title}"
    elif campaign_id and scenario_order: # Fallback if campaign_title isn't passed for some reason
        embed_title = f"Campaign C:{campaign_id} - Scenario {scenario_order} (P:{proposal_id}): {title}"

    embed = discord.Embed(title=embed_title, description=description if description else "No description provided.", color=color)
    embed.set_author(name=f"Proposed by {proposer_name}", icon_url=proposer.display_avatar.url if isinstance(proposer, discord.Member) else None)

    embed.add_field(name="Status", value=status.replace("ApprovedScenario", "Scenario Approved (Pending Campaign Start)"), inline=True)
    embed.add_field(name="Voting Mechanism", value=voting_mechanism.title(), inline=True)

    deadline_formatted = format_deadline(deadline) # Uses the existing format_deadline utility
    embed.add_field(name="Voting Deadline", value=deadline_formatted, inline=True)

    if options:
        options_text = "\n".join([f"{i+1}. {option}" for i, option in enumerate(options)])
        embed.add_field(name=f"Options ({len(options)})", value=options_text if options_text else "No options defined.", inline=False)
    else:
        embed.add_field(name="Options", value="Default: Yes, No", inline=False)

    # Display Hyperparameters
    if hyperparameters:
        hyperparams_text_parts = []
        # Plurality Specific
        if voting_mechanism.lower() == "plurality": # Assuming mechanism_name_lower is available or passed
            if "allow_abstain" in hyperparameters:
                hyperparams_text_parts.append(f"- Allow Abstain: {'Yes' if hyperparameters['allow_abstain'] else 'No'}")
            threshold = hyperparameters.get("winning_threshold_percentage")
            if threshold is not None:
                hyperparams_text_parts.append(f"- Winning Threshold: {threshold}%")
            else:
                hyperparams_text_parts.append("- Winning Threshold: Simple Majority")
        # D'Hondt Specific
        elif voting_mechanism.lower() == "dhondt":
            if "allow_abstain" in hyperparameters:
                hyperparams_text_parts.append(f"- Allow Abstain: {'Yes' if hyperparameters['allow_abstain'] else 'No'}")
            num_seats = hyperparameters.get("num_seats")
            if num_seats is not None:
                hyperparams_text_parts.append(f"- Seats to Allocate: {num_seats}")
        # Generic for others (Borda, Approval, Runoff usually just have allow_abstain)
        else:
            if "allow_abstain" in hyperparameters:
                 hyperparams_text_parts.append(f"- Allow Abstain: {'Yes' if hyperparameters['allow_abstain'] else 'No'}")
            # Add other generic hyperparameter displays if any emerge

        if hyperparams_text_parts:
            embed.add_field(name="Voting Rules", value="\n".join(hyperparams_text_parts), inline=False)

    if results:
        results_text = ""
        if isinstance(results, dict) and "winners" in results:
            if results["winners"]:
                if isinstance(results["winners"], list):
                    winners_str = ", ".join([f"'{w}'" for w in results["winners"]])
                    results_text += f"🏆 **Winner(s):** {winners_str}\n"
                else: # Single winner string
                    results_text += f"🏆 **Winner:** '{results["winners"]}'\n"
            elif results.get("reason_for_no_winner"):
                 results_text += f"ℹ️ **No Winner:** {results['reason_for_no_winner']}\n"
            else:
                results_text += "ℹ️ No winner was determined.\n"

            if "full_results" in results and results["full_results"]:
                # Format full_results based on its type (string or dict/list)
                if isinstance(results["full_results"], str):
                    results_text += f"\n**Full Breakdown:**\n{results["full_results"]}"
                elif isinstance(results["full_results"], dict):
                    formatted_breakdown = "\n".join([f"- '{opt}': {score}" for opt, score in results["full_results"].items()])
                    results_text += f"\n**Full Breakdown:**\n{formatted_breakdown}"
                # Add more sophisticated formatting if full_results is a list of tuples etc.
            elif "details" in results: # Fallback for older format
                 results_text += f"\n**Details:**\n{results['details']}"

        else: # results is likely a simple string
            results_text = str(results)

        embed.add_field(name="Results", value=results_text if results_text else "Results are not yet available.", inline=False)

    embed.set_footer(text=f"Proposal ID: {proposal_id} | Use /vote id:{proposal_id} or react to vote.")
    if campaign_id:
        embed.set_footer(text=f"Campaign C:{campaign_id} S:{scenario_order} P:{proposal_id} | Use /vote id:{proposal_id} or react to vote.")

    return embed


def get_ordinal_suffix(n: int) -> str:
    """Gets the ordinal suffix for a number (e.g., 1st, 2nd, 3rd)."""
    if 11 <= n <= 13:
        return "th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return suffix
