# Active Context: Current Work and Focus

## Current Task

*   **FIXED: AdminApprovalView Callback Error:** Fixed the `TypeError: AdminApprovalView.approve_button_callback() missing 1 required positional argument: 'button'` error by removing the `button` parameter from callback function signatures since they're manually assigned.
*   **FIXED: Campaign Control Panel Updates:** Added `_update_campaign_control_panel()` function that gets called after each scenario is created to keep the campaign management interface updated with current state.
*   **RESOLVED: DM Hyperparameters Error:** Fixed the `'str' object has no attribute 'items'` error in voting DM sending by adding proper type checking and JSON parsing in both `send_voting_dm` and `send_campaign_scenario_dms_to_user` functions.
*   **RESOLVED: Campaign Auto-Approval Logic:** Implemented auto-approval for campaign scenarios when the campaign is in 'setup' or 'active' status, ensuring proper campaign flow from approval to scenario definition to voting.

## Latest Fixes (Current Session)

1. **AdminApprovalView Button Callback Fix:**
   - **Problem:** `TypeError: AdminApprovalView.approve_button_callback() missing 1 required positional argument: 'button'` was occurring because the callback functions were manually assigned but still had the button parameter in their signatures.
   - **Solution:** Removed the `button` parameter from both `approve_button_callback` and `reject_button_callback` methods in `AdminApprovalView`.
   - **Location:** `proposals.py` lines ~926-970

2. **Campaign Control Panel Update System:**
   - **Problem:** After defining scenarios in campaigns, the control panel wasn't updating to reflect the new scenario count and button states.
   - **Solution:** Added `_update_campaign_control_panel()` function that:
     - Fetches the latest campaign data
     - Updates the control message embed with current scenario counts
     - Refreshes button states using `CampaignControlView.update_button_states()`
     - Gets called automatically after scenario creation in `_create_new_proposal_entry()`
   - **Location:** `proposals.py` lines ~1845-1919

## Resolved Issues (Previous Session)

*   **Issue: DM Hyperparameters Error (`voting.py`):**
    *   **Problem:** The error `'str' object has no attribute 'items'` occurred because `hyperparameters` was sometimes stored/retrieved as a JSON string instead of a dict.
    *   **Solution:** Added robust type checking in `send_voting_dm` and `send_campaign_scenario_dms_to_user` functions:
        *   Check if `hyperparameters` is a string and parse with `json.loads()`
        *   Fall back to empty dict `{}` if parsing fails
        *   Ensure `.items()` is only called on dict objects
    *   **Impact:** Normal proposals and campaign scenarios now send DMs properly without crashing.

*   **Issue: Campaign Auto-Approval Logic (`proposals.py`):**
    *   **Problem:** Campaign scenarios weren't being auto-approved when the campaign was already in 'setup' or 'active' status.
    *   **Solution:** Modified `_create_new_proposal_entry()` function to:
        *   Check campaign status when `campaign_id` is provided
        *   Set `initial_status = "ApprovedScenario"` and `requires_approval = False` for approved campaigns
        *   Keep normal approval flow for campaigns still pending approval
    *   **Impact:** Scenarios in approved campaigns are now auto-approved, allowing smooth campaign progression.

## Previous Completed Tasks

*   **DONE: Implement Server Guide Channel:** A new read-only channel (`#server-guide`) is now automatically created on server join/bot start. It contains a comprehensive embed detailing server channels, voting protocols (Plurality, Borda, Approval, Runoff, D'Hondt, Copeland), and bot usage.

## Next Steps

1. **Test Complete Campaign Flow:** Verify that:
   - Campaign creation and approval works
   - Scenario definition updates the control panel correctly  
   - Button states reflect the actual campaign status
   - Users can progress through all scenarios properly
   - Campaign can be started when scenarios are ready

2. **Monitor for Additional Issues:** Watch for any remaining edge cases in:
   - Concurrent scenario definitions
   - Campaign state transitions
   - Button interaction edge cases

## Recent Learning & Patterns

*   **Button Callback Pattern:** When manually assigning button callbacks in Discord.py views, don't include the `button` parameter in the callback function signature - it's only needed when using the `@discord.ui.button` decorator.
*   **Campaign State Management:** The control panel needs active updating after state changes since Discord doesn't automatically refresh UI elements. Manual refresh calls are essential for good UX.
*   **Error Handling for Hyperparameters:** Database fields storing JSON need robust parsing with fallbacks, as the data can be in different formats depending on how it was stored/retrieved.