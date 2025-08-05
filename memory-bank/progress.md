# Progress: Current State and Future Work

## What Works

*   **Core Bot Functionality:** Bot runs, connects, basic command handling.
*   **Modular Structure:** Code organized into modules.
*   **Database Fixes & Enhancements (`db.py`):
    *   `add_vote` function corrected to prevent "no such column: id" error.
    *   `record_vote` function correctly handles INSERT/UPDATE logic for votes, including `tokens_invested` and `is_abstain`.
    *   `update_user_remaining_tokens` function available for campaign token management.
    *   Database Initialization & Schema (Weighted Campaigns - Phase A Core):
        *   `db.py` initializes tables, including new `campaigns` and `user_campaign_participation` tables.
        *   `proposals` table updated with `campaign_id`, `scenario_order`.
        *   `votes` table updated with `tokens_invested`, `is_abstain`.
*   **Voting Logic Fixes & Enhancements (`voting.py`):
    *   **`PluralityVoteView.option_callback`:** Resolved `AttributeError` by removing faulty logic for a non-existent submit button. Option clicks now correctly call `submit_vote_callback`.
    *   **Token Investment Flow:** The fix above ensures the `TokenInvestmentModal` is correctly triggered for campaign votes after an option is selected in plurality voting.
    *   **Campaign Voting DM Enhancements & Token Investment (Existing):**
        *   `TokenInvestmentModal` implemented for users to specify token amounts for campaign scenario votes.
        *   `BaseVoteView` updated to integrate the token investment modal, handle campaign context (remaining tokens, total scenarios), and manage the vote finalization process.
        *   `send_voting_dm` now correctly displays the user's remaining campaign tokens and total campaign scenarios in the DM embed.
        *   Voting logic now calls `db.record_vote` and `db.update_user_remaining_tokens` as appropriate.
    *   **FIXED: Hyperparameters Error:** Added robust JSON parsing and type checking in both `send_voting_dm` and `send_campaign_scenario_dms_to_user` to handle cases where hyperparameters is stored as a string instead of dict.
*   **Embed Formatting (`utils.py`):
    *   `create_proposal_embed` corrected to ensure `\n` renders as actual newlines in Discord embeds, fixing issues with proposal channel announcements.
*   **Campaign Creation & Scenario Definition UI (`proposals.py` - Phase A Core):
    *   `!propose` flow allows initiating a standalone proposal or a new Weighted Campaign.
    *   `CampaignSetupModal`, `DefineScenarioView`, and mechanism-specific modals handle campaign and scenario creation.
*   **Campaign Approval and Scenario Progression (Phase 1 Complete):
    *   Campaigns can be created, approved by admins (transitioning to 'setup').
    *   **FIXED: Auto-Approval Logic:** Scenarios for approved campaigns (status 'setup' or 'active') are now automatically set to 'ApprovedScenario' status, bypassing normal admin approval.
    *   **FIXED: Campaign Control Flow:** `CampaignControlView` now properly handles scenario definition and campaign starting with correct button states.
    *   `voting_utils.initiate_voting_for_proposal` handles starting votes for standalone proposals and campaign scenarios.
*   **Vote Tallying Logic - Initial Token Weighting (`voting_utils.py` - Phase A Foundation):
    *   Vote counting methods updated for `tokens_invested`.
    *   `format_vote_results` displays weighted results.
*   **Automated Server Guide Channel (`main.py`, `utils.py`):
    *   A `#server-guide` channel is automatically created when the bot joins a server or on startup for existing servers.
    *   This channel is populated with a single, comprehensive embed message explaining server channels, detailed explanations of available voting protocols (Plurality, Borda, Approval, Runoff, Copeland), and basic bot command usage.
    *   The channel is read-only for users, and the bot purges its previous guide message before sending a new one to keep it clean.

## What's Left to Build / Verify Thoroughly

*   **Thorough User Testing of Recent Fixes:**
    *   **Hyperparameters Error Fix:**
        *   Test creating normal proposals and verify DMs are sent without the `'str' object has no attribute 'items'` error
        *   Test campaign scenario DMs to ensure they also work correctly
    *   **Campaign Auto-Approval Flow:**
        *   Create a campaign, get it approved by admin
        *   Define scenario 1, verify it auto-approves to 'ApprovedScenario' status
        *   Use campaign control panel to start the campaign
        *   Define scenario 2 while campaign is active, verify it also auto-approves
        *   Verify proper progression through all campaign stages
    *   **Campaign/Proposal Isolation:**
        *   Ensure normal proposals still require approval when configured
        *   Verify campaign scenarios don't interfere with normal proposal approval flow
        *   Test that both systems can run simultaneously without conflicts
*   **Campaign Control Panel Testing:**
    *   Verify all button states and labels update correctly based on campaign status
    *   Test permission checks (creator vs admin access)
    *   Ensure proper error handling when campaigns or scenarios are not found
*   **Weighted Campaign Feature - Further Refinements & Testing:
    *   Review and test `process_vote` in `voting.py` for any remaining validation logic that should be in the View/Modal layer or `finalize_vote`.
    *   End-to-End Testing (All Mechanisms in Campaigns): Ensure all voting mechanisms function correctly with token weighting from DM to results.
*   **Campaign Management Features (Phase B - Future - Refined Scope):
    *   Display of user's overall token usage/status within a campaign.
    *   Admin commands for campaign overview and manual status changes.
    *   Automated campaign completion when all scenarios are 'Closed'.
*   **Robust Error Handling:** Especially around token investment, campaign state transitions, and interactions with the `CampaignControlView`.

## Known Issues & Considerations

*   **`TokenInvestmentModal` and `BaseVoteView.finalize_vote` (`voting.py`):** Implemented and recent plurality flow fix applied; requires thorough testing to ensure robustness and correct behavior in all scenarios (e.g., edge cases for token values, interaction deferrals).
*   **Validation in `process_vote` (`voting.py`):** Some validation logic might still reside in `process_vote`. Ideally, all user input validation should occur in the UI layer (Views/Modals) before calling `finalize_vote` or `process_vote`.
*   **`RankedVoteView` Implementation (Standalone/Campaign):** Still needs careful implementation/verification.

## Evolution of Project Decisions

*   **Modal Design for Proposal Creation:** Structure refined to 3 base + 2 specific inputs to meet Discord limits.
*   **Weighted Campaigns Introduced:** A major new feature involving new DB tables, UI flows for setup, and modifications to voting and results processing to incorporate token investment as vote weight.
*   **Refinement of Voting Flow (`voting.py`):** `BaseVoteView` and related components were significantly updated to handle the token investment modal and correctly process campaign votes, including direct calls to `db.record_vote` and `db.update_user_remaining_tokens`. The `PluralityVoteView.option_callback` was fixed to prevent an `AttributeError` and ensure proper call to `submit_vote_callback`, enabling the token investment flow.
*   **Database Error Resolution (`db.py`):** Queries updated to align with schema changes (e.g., exclusive use of `proposal_id`).
*   **Embed Formatting (`utils.py`):** Corrected newline handling for improved display.
*   **Campaign Auto-Approval Implementation (`proposals.py`):** Added logic to automatically approve scenarios for approved campaigns, eliminating manual approval bottleneck for campaign progression.
*   **Hyperparameters Error Resolution (`voting.py`):** Added robust JSON parsing with fallback handling to prevent string-dict type errors in DM sending functions.