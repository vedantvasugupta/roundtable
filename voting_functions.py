# voting_functions.py
import asyncio
import main            # has main.bot and announce_pending_results
from datetime import datetime
import json

async def process_vote(user_id, proposal_id, vote_data):
    """Validate, record, and (when complete) finalise a vote."""
    # ── 1. basic checks ────────────────────────────────────────────────────────
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        return False, "Proposal not found or invalid proposal ID."
    if proposal["status"] != "Voting":
        return False, f"This proposal is not open for voting. Current status: {proposal['status']}"

    deadline = datetime.fromisoformat(proposal["deadline"].replace("Z", "+00:00"))
    if datetime.now() > deadline:
        await db.update_proposal_status(proposal_id, "Closed")
        return False, "The voting deadline for this proposal has passed."

    # ── 2. store / update the user's vote ─────────────────────────────────────
    existing_vote = await db.get_user_vote(proposal_id, user_id)
    if existing_vote:
        await db.update_vote(existing_vote["vote_id"], vote_data)
        message = "Your vote has been updated."
    else:
        await db.add_vote(proposal_id, user_id, vote_data)
        message = "Your vote has been recorded."

    # ── 3. check if voting is now complete ────────────────────────────────────
    try:
        all_votes      = await db.get_proposal_votes(proposal_id)
        server_id      = proposal["server_id"]
        const_vars     = await db.get_constitutional_variables(server_id)
        server_info    = await db.get_server_info(server_id)

        eligible_role  = const_vars.get("eligible_voters_role", {"value": "everyone"})["value"]
        show_progress  = const_vars.get("show_vote_count",     {"value": "true"})["value"].lower() == "true"

        # optional live‑progress update (requires guild object, omitted here)

        # Get actual eligible voters instead of using an estimate
        guild = main.bot.get_guild(server_id)
        if eligible_role.lower() == "everyone" and server_info and guild:
            print('PROCESSING VOTE')
            # Primary method: Use get_eligible_voters to get actual eligible members
            eligible_voters = await get_eligible_voters(guild, proposal)
            eligible_count = len([m for m in eligible_voters if not m.bot])

            # Fallback method: Check against invited voters in database
            if eligible_count == 0:
                invited_voters = await db.get_invited_voters(proposal_id)
                eligible_count = len(invited_voters) if invited_voters else max(1, int(server_info["member_count"] * 0.9))

            print("Number of votes:", len(all_votes), "Eligible count:", eligible_count)
            if len(all_votes) >= eligible_count and eligible_count > 0:
                results = await close_proposal(proposal_id, guild)

                # ── 3a. flag proposal & announce instantly ───────────────────
                if results:
                    await db.update_proposal(
                        proposal_id,
                        {"results_pending_announcement": 1}
                    )
                    asyncio.create_task(
                        main.announce_pending_results(main.bot)
                    )
                    message += " All eligible voters have voted—results are now available."

    except Exception as exc:                      # never abort voting on errors
        print(f"[process_vote] post‑vote checks failed: {exc!r}")

    return True, message


async def close_proposal(proposal_id, guild):
    """Close a proposal and tally the votes"""
    # Get proposal details
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        return None

    # Get all votes for this proposal
    votes = await db.get_proposal_votes(proposal_id)

    # Get the voting mechanism
    mechanism = get_voting_mechanism(proposal['voting_mechanism'])
    if not mechanism:
        return None

    # Tally the votes
    results = await mechanism.tally_votes(votes)

    # Update proposal status based on votes
    new_status = 'Passed' if votes else 'Failed'
    await db.update_proposal_status(proposal_id, new_status)

    # Store results in the database
    results_json = json.dumps(results)
    await db.store_proposal_results(proposal_id, results_json)

    return results

async def check_expired_proposals():
    """Check for proposals with expired deadlines and close them"""
    # Get all proposals with status 'Voting'
    active_proposals = await db.get_proposals_by_status('Voting')

    now = datetime.now()
    closed_proposals = []

    for proposal in active_proposals:
        deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
        if now > deadline:
            # Close the proposal
            import main
            bot = main.bot
            guild = bot.get_guild(proposal['server_id'])
            results = await close_proposal(proposal['proposal_id'], guild)
            if results:
                closed_proposals.append((proposal, results))

    return closed_proposals

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
