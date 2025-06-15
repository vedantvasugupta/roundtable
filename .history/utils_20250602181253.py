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
from datetime import datetime, timedelta, timezone
import re
import db
import math  # Needed for create_progress_bar
from typing import List, Optional, Union, Dict, Any, Tuple
import json

CHANNELS: Dict[str, str] = {
    "proposals": "proposals",
    "voting": "voting-room",  # Alias for voting-room
    "voting-room": "voting-room",
    "results": "governance-results", # Alias for governance-results
    "governance-results": "governance-results",
    "audit": "audit-log", # Alias for audit-log
    "audit-log": "audit-log",
    "campaign_management": "campaign-management",
    # Add other channel mappings here as needed
}

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
    elif status == "Closed" or status == "Rejected" or status == "Cancelled" or status == "ApprovedScenario":
        color = discord.Color.dark_grey()

    # Ensure hyperparameters is a dict, default to empty if None or not a dict
    if not isinstance(hyperparameters, dict):
        if isinstance(hyperparameters, str) and hyperparameters.strip():
            try:
                hyperparameters = json.loads(hyperparameters)
                if not isinstance(hyperparameters, dict): # Ensure loaded JSON is a dict
                    hyperparameters = {}
            except json.JSONDecodeError:
                hyperparameters = {} # Invalid JSON, default to empty
        else:
            hyperparameters = {} # None or empty string or other non-dict type

    proposer_mention = f"<@{proposer}>" if isinstance(proposer, int) else proposer.mention if hasattr(proposer, 'mention') else str(proposer)
    proposer_name = proposer.display_name if isinstance(proposer, discord.Member) else f"User ID: {proposer}"

    embed_title = f"🗳️ Proposal P#{proposal_id}: {title}"
    if campaign_id and scenario_order:
        campaign_ctx_str = f" (Campaign C#{campaign_id}"
        if campaign_title:
            campaign_ctx_str += f": \'{campaign_title}\'"
        campaign_ctx_str += f", Scenario S#{scenario_order})"
        embed_title += campaign_ctx_str

    embed = discord.Embed(title=embed_title, description=description, color=color)
    embed.set_author(name=f"Proposed by {proposer_name}", icon_url=proposer.display_avatar.url if isinstance(proposer, discord.Member) else None)

    embed.add_field(name="Proposer", value=proposer_mention, inline=True)
    embed.add_field(name="Voting Mechanism", value=voting_mechanism, inline=True)

    # Format deadline
    try:
        if isinstance(deadline, str):
            deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00')) if 'Z' in deadline else datetime.fromisoformat(deadline)
        elif isinstance(deadline, datetime):
            deadline_dt = deadline
        else: # Handle unexpected types
            deadline_dt = None

        if deadline_dt:
            # Ensure it's offset-aware for correct Discord timestamp formatting
            if deadline_dt.tzinfo is None or deadline_dt.tzinfo.utcoffset(deadline_dt) is None:
                 deadline_dt = deadline_dt.replace(tzinfo=timezone.utc) # Assume UTC if naive
            discord_timestamp = f"<t:{int(deadline_dt.timestamp())}:F>"
            embed.add_field(name="Deadline", value=discord_timestamp, inline=True)
        else:
            embed.add_field(name="Deadline", value="Not set or invalid", inline=True)

    except (ValueError, TypeError) as e:
        print(f"Error parsing deadline \'{deadline}\' for proposal P#{proposal_id}: {e}")
        embed.add_field(name="Deadline", value=str(deadline) if deadline else "Error parsing", inline=True)

    embed.add_field(name="Status", value=status, inline=True)
    if requires_approval is not None:
        embed.add_field(name="Requires Approval", value="Yes" if requires_approval else "No", inline=True)

    # Display hyperparameters
    if hyperparameters: # Check if hyperparameters dict is not empty
        # Handle allow_abstain specifically for boolean conversion
        allow_abstain_val = hyperparameters.get("allow_abstain")
        abstain_display = "N/A"
        if isinstance(allow_abstain_val, str):
            if allow_abstain_val.lower() == 'true':
                abstain_display = "Yes"
            elif allow_abstain_val.lower() == 'false':
                abstain_display = "No"
            # If it's some other string, it remains "N/A" or you could log an error
        elif isinstance(allow_abstain_val, bool):
            abstain_display = "Yes" if allow_abstain_val else "No"

        hp_text = f"Allow Abstain: {abstain_display}\n"
        for key, value in hyperparameters.items():
            if key != "allow_abstain": # Already handled
                 hp_text += f"{key.replace('_', ' ').title()}: {value}\n"
        if hp_text.strip(): # Ensure there's something to display
            embed.add_field(name="Voting Rules", value=hp_text.strip(), inline=False)

    # Display options
    if options:
        options_text = ""
        for i, opt_text in enumerate(options):
            options_text += f"{i+1}. {opt_text}\n"
        embed.add_field(name="Options", value=options_text.strip(), inline=False)

    # Display results if available
    if results:
        results_text = ""
        if isinstance(results, dict):
            for option, count in results.items():
                results_text += f"{option}: {count} votes\n"
        elif isinstance(results, str): # Handle if results are still a string
            results_text = results
        else:
            results_text = "Results format not recognized."
        embed.add_field(name="Results", value=results_text.strip() if results_text else "No results yet.", inline=False)

    embed.set_footer(text=f"Proposal ID: {proposal_id} | Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if campaign_id:
        embed.set_footer(text=f"Campaign C:{campaign_id} S:{scenario_order} P:{proposal_id} | Use /vote id:{proposal_id} or react to vote.")

    return embed


def get_ordinal_suffix(n: int) -> str:
    """Gets the ordinal suffix for a number (e.g., 1st, 2nd, 3rd)."""
    if 11 <= n <= 13:
        return "th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return suffix
