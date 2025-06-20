# Active Context: Current Work and Focus

## Current Task

*   **FIXED: Approval Voting Submit Button:** Added a "Submit Vote" button to approval voting that allows users to select multiple options and then submit when ready, fixing both normal and campaign approval voting flows.
*   **FIXED: AdminApprovalView Callback Error:** Fixed the `TypeError: AdminApprovalView.approve_button_callback() missing 1 required positional argument: 'button'` error by removing the `button` parameter from callback function signatures since they're manually assigned.
*   **FIXED: Campaign Control Panel Updates:** Added `_update_campaign_control_panel()` function that gets called after each scenario is created to keep the campaign management interface updated with current state.
*   **RESOLVED: DM Hyperparameters Error:** Fixed the `'str' object has no attribute 'items'` error in voting DM sending by adding proper type checking and JSON parsing in both `send_voting_dm` and `send_campaign_scenario_dms_to_user` functions.
*   **RESOLVED: Campaign Auto-Approval Logic:** Implemented auto-approval for campaign scenarios when the campaign is in 'setup' or 'active' status, ensuring proper campaign flow from approval to scenario definition to voting.

## Latest Fixes (Current Session)

1. **Approval Voting Submit Button Fix:**
   - **Problem:** Approval voting was immediately submitting after each option click, not allowing users to select multiple options and submit when ready. This affected both normal proposals and campaign scenarios.
   - **Root Cause:** The `ApprovalVoteView.option_callback()` was calling `submit_vote_callback()` after each option selection, causing immediate submission instead of allowing multiple selections.
   - **Solution:** 
     - Added a "Submit Vote" button that is initially disabled
     - Modified `option_callback()` to only toggle option selection and update the submit button state
     - Created `submit_button_callback()` that handles the actual submission
     - Submit button shows the count of selected options and is enabled/disabled based on selections
     - Updated user feedback to guide users to click "Submit Vote" when ready
   - **Location:** `voting.py` lines ~590-720

2. **AdminApprovalView Button Callback Fix:**
   - **Problem:** `TypeError: AdminApprovalView.approve_button_callback() missing 1 required positional argument: 'button'` was occurring because the callback functions were manually assigned but still had the button parameter in their signatures.
   - **Solution:** Removed the `button` parameter from both `approve_button_callback` and `reject_button_callback` methods in `AdminApprovalView`.
   - **Location:** `proposals.py` lines ~926-970

3. **Campaign Control Panel Update System:**
   - **Problem:** After defining scenarios in campaigns, the control panel wasn't updating to reflect the new scenario count and button states.
   - **Solution:** Added `_update_campaign_control_panel()` function that:
     - Fetches the latest campaign data
     - Updates the control message embed with current scenario counts
     - Refreshes button states using `CampaignControlView.update_button_states()`
     - Gets called automatically after scenario creation in `_create_new_proposal_entry()`
   - **Location:** `proposals.py` lines ~1845-1919

## Impact of Latest Fixes

*   **Normal Approval Voting:** Users can now select multiple options and have a clear submit button to finalize their vote
*   **Campaign Approval Voting:** Users can select multiple options, and the token investment modal will appear correctly when they click "Submit Vote"
*   **Admin Proposal Approval:** Normal proposals can now be approved without callback errors
*   **Campaign Management:** Control panels update immediately after scenario creation, showing accurate button states and scenario counts

## Next Steps

1. **Test Complete Flows:** Verify that:
   - Normal approval voting works with submit button
   - Campaign approval voting shows token investment modal correctly
   - Admin approval of normal proposals works without errors
   - Campaign scenario progression flows properly

2. **Monitor User Experience:** Ensure the new approval voting UX is intuitive and the submit button guidance is clear

## Recent Learning & Patterns

*   **Approval Voting UX Pattern:** For multi-selection voting mechanisms, provide clear visual feedback about current selections and require explicit submission rather than auto-submitting after each selection.
*   **Button Callback Pattern:** When manually assigning button callbacks in Discord.py views, don't include the `button` parameter in the callback function signature - it's only needed when using the `@discord.ui.button` decorator.
*   **Campaign State Management:** The control panel needs active updating after state changes since Discord doesn't automatically refresh UI elements. Manual refresh calls are essential for good UX.
*   **Error Handling for Hyperparameters:** Database fields storing JSON need robust parsing with fallbacks, as the data can be in different formats depending on how it was stored/retrieved.