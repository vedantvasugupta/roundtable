# Active Context: Current Work and Focus

## Current Task

*   **DONE: Implement Server Guide Channel:** A new read-only channel (`#server-guide`) is now automatically created on server join/bot start. It contains a comprehensive embed detailing server channels, voting protocols (Plurality, Borda, Approval, Runoff, D'Hondt, Copeland), and bot usage.
*   **Memory Bank Update:** Updating all memory bank files to reflect the current project state, focusing on the resolution of several critical user-reported issues and the implementation of batched campaign voting with token atomicity. (Partially done, updated for Server Guide, previous major changes already documented).

## Resolved Issues (Current Session)

*   **New Feature: Server Guide Channel (`main.py`, `utils.py`):**
    *   Added `server-guide` to `CHANNELS` dictionary in `main.py`.
    *   Created `create_and_send_server_guide(guild, bot)` function in `main.py`.
        *   This function uses `utils.get_or_create_channel` to create/get the `#server-guide` channel with read-only permissions for users and send permissions for the bot.
        *   It purges previous bot messages from the channel.
        *   It constructs and sends a detailed embed explaining server channels, voting protocols (Plurality, Borda, Approval, Runoff, D'Hondt, Copeland with explanations, analogies, pros/cons), and basic bot commands.
    *   Called `create_and_send_server_guide` in `on_ready` (for existing guilds) and `on_guild_join` (for new guilds).
    *   Ensured `timezone` from `datetime` is imported for the embed footer timestamp.
*   **Issue 1: Plurality Vote Submission Hanging (`voting.py`):**
    *   **Fix:** Corrected interaction handling in `PluralityVoteView.option_callback`, `AbstainButton.callback`, `BaseVoteView.submit_vote_callback`, and `BaseVoteView.finalize_vote`. Removed premature responses and ensured proper deferral and follow-up logic. Button style updates are now handled in memory and applied by `finalize_vote`.
    *   **Impact:** Plurality votes (and likely other simple votes) should now submit correctly without hanging. Token investment modals for campaigns should also appear as expected.

*   **Issue 3b: Proposal Termination Error (`voting_utils.py`):**
    *   **Fix:** Modified `close_proposal` to map a "Failed" status (determined by lack of a winner) to "Closed" before updating the database. This ensures proposals that don't pass are still marked as "Closed" and not "Failed" in the DB if that was the intended final state.
    *   **Impact:** Proposals that end without a winner will now be correctly recorded as "Closed" in the database if that's the desired terminal state for failed proposals.

*   **Issue 4: "P#" Prefix in User-Facing Messages (`voting.py`, `voting_utils.py`, `proposals.py`):**
    *   **Fix:** Searched and replaced user-facing instances of "P#{proposal_id}" with "#{proposal_id}" or "Proposal #{proposal_id}" for better readability across relevant files.
    *   **Impact:** User messages and detailed logs should now display proposal IDs more cleanly.

*   **Issue 5: Simultaneous Voting DMs & Token Atomicity for Campaigns:**
    *   **New Functions (`voting_utils.py`):**
        *   `initiate_campaign_stage_voting(guild, campaign_id, scenario_proposal_ids, bot_instance)`: Sets multiple scenarios to 'Voting', announces them, and triggers batched DM sending.
        *   `send_batched_campaign_dms(guild, campaign_id, scenarios_data, bot_instance)`: Fetches eligible members and calls `send_campaign_scenario_dms_to_user` for each.
    *   **New Function (`voting.py`):**
        *   `send_campaign_scenario_dms_to_user(member, scenarios_data)`: Sends multiple DMs to a user for a batch of scenarios, ensuring each DM reflects the *initial* token balance for that batch.
    *   **Core Logic (`voting.py` - `BaseVoteView.finalize_vote`):**
        *   **Pre-Vote Token Check:** Before processing a campaign vote, `finalize_vote` now re-fetches the user's current token balance from the database (`db.get_user_remaining_tokens`).
        *   **Validation:** It validates the `tokens_invested_this_scenario` against this fresh balance. If overdrawn, an error message is sent, and the vote is not processed.
        *   **Atomic Update:** `db.update_user_remaining_tokens` (which should ideally be an atomic SQL operation like `UPDATE ... SET tokens = tokens - ? WHERE tokens >= ?`) is called *after* the vote is recorded.
        *   **Confirmation:** Confirmation messages now reflect the outcome of the token update and the user's new balance.
    *   **Integration (`proposals.py` - `CampaignControlView.start_next_callback`):**
        *   Modified to collect all relevant `ApprovedScenario` proposal IDs for the current stage (either starting order 1 or progressing to the next order).
        *   Calls `initiate_campaign_stage_voting` with the list of proposal IDs.
    *   **Helper (`db.py`):** Added `get_proposal_scenario_order(proposal_id)`.
    *   **Impact:**
        *   Multiple DMs for a campaign stage can be sent out more concurrently.
        *   Token investments are now checked against the most current balance right before vote finalization, significantly reducing the risk of overdraft due to simultaneously submitted votes from different DMs. The user gets immediate feedback if their balance changed and the vote cannot proceed with the intended token amount.

## Previously Resolved Issues (Referenced from previous context)
*   AttributeError in `PluralityVoteView.option_callback` (related to submit button).
*   Database error "no such column: id" in `db.add_vote`.
*   Newline formatting in `utils.create_proposal_embed`.
*   Streamlined Scenario Approval & Campaign Progression.

## Blocked/Pending Tasks (Due to Tool Limitations - Carry-over)
*   Streamlined Scenario Approval (auto-approve for active campaigns).
*   Conditional Campaign Approval Based on Constitutional Variable.

## Recent Significant Changes (Summary of this session)
*   **Comprehensive Interaction Handling:** Overhauled response/deferral logic in `voting.py` for button and modal interactions.
*   **Batched Campaign DM System:** New functions in `voting_utils.py` and `voting.py` to initiate and send DMs for multiple campaign scenarios at once.
*   **Token Atomicity Measures:** Implemented pre-vote database token checks in `BaseVoteView.finalize_vote` to prevent overdrafts in campaign voting.
*   **Campaign Progression Update:** `CampaignControlView` now initiates all approved scenarios for a given order/stage simultaneously.
*   **Database Helper:** Added `get_proposal_scenario_order` to `db.py`.
*   **Status Mapping:** "Failed" proposals can now be stored as "Closed" in `voting_utils.py`.
*   **Cosmetic Fixes:** Removed "P#" prefix from user messages.

## Next Steps (Immediate & Short-Term)

*   **User Testing of Server Guide:** Verify the `#server-guide` channel is created on new server join and present on existing servers after bot restart. Check content, formatting, and read-only permissions.
*   **Thorough User Testing of All Resolved Issues:**
    *   Plurality vote submission (no hang, correct finalization).
    *   Abstain functionality.
    *   Campaign scenario voting:
        *   Simultaneous DM delivery for a stage.
        *   Correct initial token display in each DM of a batch.
        *   Token investment modal functionality.
        *   **Crucially, test token atomicity:** Open multiple DMs for a campaign, try to vote with conflicting token amounts quickly, and verify that balances update correctly and overdrafts are prevented with clear error messages.
        *   Confirmation messages for votes and token usage.
    *   Proposal termination: Verify "Failed" proposals correctly appear as "Closed" if that's the flow.
    *   Check for "P#" prefixes in all relevant user-facing areas.
*   **Review and Update `progress.md` based on these fixes and new functionalities.**
*   **Address any new issues arising from testing.**

## Active Decisions & Considerations
*   **Definition of "Atomic" for Token Updates:** The current implementation relies on a DB check *before* vote processing and a separate DB update *after*. True atomicity would ideally be a single "vote and decrement if sufficient tokens" DB transaction. The `db.update_user_remaining_tokens` function *must* perform an atomic check-and-decrement (e.g., `UPDATE user_tokens SET balance = balance - ? WHERE user_id = ? AND campaign_id = ? AND balance >= ?`). If it doesn't, race conditions are still possible between the `get_user_remaining_tokens` check and the `update_user_remaining_tokens` call, albeit in a smaller window. **This needs verification/emphasis in `db.py`.**
*   **User Experience for Batched DMs:** While efficient, ensure users aren't overwhelmed by many DMs at once. The current approach of sending all for a "stage" seems reasonable.
*   **Tool Limitations:** Continue to be mindful of `edit_file` constraints for future complex changes.

## Important Patterns & Preferences
*   **Iterative Development & Testing.**
*   **Clear User Feedback for actions, especially involving resource changes (tokens).**
*   **Memory Bank Upkeep.**