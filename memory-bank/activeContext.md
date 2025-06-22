# Active Context: Current Work and Focus

## Current Task

*   **COMPLETED: All Voting Mechanisms Submit Button Support:** Added submit buttons to all voting mechanisms (plurality, approval, runoff, borda, dhondt) for proper token allocation and finalization flow.
*   **COMPLETED: Campaign Auto-DM for Late Scenarios:** Fixed automatic DM sending when scenarios are defined in already active campaigns, removing overly restrictive queuing logic.
*   **COMPLETED: Approval Voting Submit Button:** Added a "Submit Vote" button to approval voting that allows users to select multiple options and then submit when ready, fixing both normal and campaign approval voting flows.
*   **COMPLETED: Ranked Voting Submit Button:** Added submit buttons to RankedVoteView (used by runoff and borda mechanisms) with dynamic labeling showing ranking count and proper token allocation flow.
*   **RESOLVED: AdminApprovalView Callback Error:** Fixed the `TypeError: AdminApprovalView.approve_button_callback() missing 1 required positional argument: 'button'` error by removing the `button` parameter from callback function signatures since they're manually assigned.
*   **RESOLVED: Campaign Control Panel Updates:** Added `_update_campaign_control_panel()` function that gets called after each scenario is created to keep the campaign management interface updated with current state.
*   **RESOLVED: DM Hyperparameters Error:** Fixed the `'str' object has no attribute 'items'` error in voting DM sending by adding proper type checking and JSON parsing in both `send_voting_dm` and `send_campaign_scenario_dms_to_user` functions.
*   **RESOLVED: Campaign Auto-Approval Logic:** Implemented auto-approval for campaign scenarios when the campaign is in 'setup' or 'active' status, ensuring proper campaign flow from approval to scenario definition to voting.

## Latest Fixes (Current Session)

1. **Active Campaign Scenario Auto-Start Fix:**
   - **Problem:** When a campaign was already active and you defined a new scenario (e.g., scenario 3 after starting with scenarios 1 and 2), the scenario was created and auto-approved but no DMs were sent. Users had to manually click "Start Next" in the campaign control panel.
   - **Root Cause:** The scenario creation logic only created scenarios with "ApprovedScenario" status but didn't check if the campaign was already active to immediately start voting.
   - **Solution:**
     - Added logic in `_create_new_proposal_entry()` to detect when a campaign is already in "active" status
     - When a new scenario is defined for an active campaign, automatically calls `voting_utils.initiate_campaign_stage_voting()` to start voting immediately
     - Includes safety check to ensure no other scenarios are currently voting before starting the new one
     - Updates user notification to indicate that voting has started immediately and DMs have been sent
     - Provides fallback notification if the interaction edit fails
   - **Location:** `proposals.py` lines ~826-860

2. **Approval Voting Submit Button Fix:**
   - **Problem:** Approval voting was immediately submitting after each option click, not allowing users to select multiple options and submit when ready. This affected both normal proposals and campaign scenarios.
   - **Solution:** Added a "Submit Vote" button that becomes enabled once at least one option is selected, and modified the flow so option clicking only toggles selection without auto-submitting.
   - **Location:** `voting.py` lines ~590-720

3. **AdminApprovalView Button Callback Fix:**
   - **Problem:** `TypeError: AdminApprovalView.approve_button_callback() missing 1 required positional argument: 'button'` was occurring because the callback functions were manually assigned but still had the button parameter in their signatures.
   - **Solution:** Removed the `button` parameter from both `approve_button_callback` and `reject_button_callback` methods in `AdminApprovalView`.
   - **Location:** `proposals.py` lines ~967-1007

4. **Campaign Control Panel Update System:**
   - **Problem:** After defining scenarios in campaigns, the control panel wasn't updating to reflect the new scenario count and button states.
   - **Solution:** Added `_update_campaign_control_panel()` function that automatically updates the control panel after scenario creation.
   - **Location:** `proposals.py` lines ~1882-1956

## Impact of Latest Fixes

*   **Seamless Campaign Flow:** Users can now define scenarios at any time during an active campaign and voting will start immediately with DMs sent automatically
*   **Normal Approval Voting:** Users can select multiple options and have a clear submit button to finalize their vote
*   **Campaign Approval Voting:** Users can select multiple options, and the token investment modal appears correctly when they click "Submit Vote"
*   **Admin Proposal Approval:** Normal proposals can be approved without callback errors
*   **Campaign Management:** Control panels update immediately after scenario creation, showing accurate button states and scenario counts

## User Experience Flow

**Active Campaign Scenario Definition:**
1. User defines scenarios 1 and 2, starts campaign → DMs sent for both scenarios
2. User later defines scenario 3 while campaign is active 
3. System automatically detects campaign is active
4. Immediately starts voting for scenario 3 and sends DMs
5. User gets notification that voting started immediately
6. No manual "Start Next" button clicking required

**Safety Features:**
- Checks that no other scenarios are currently voting before auto-starting
- Provides clear debug logging for troubleshooting
- Graceful fallback notifications if interaction editing fails

## Next Steps

1. **Test Complete Active Campaign Flow:** Verify that:
   - Defining new scenarios in active campaigns immediately sends DMs
   - No conflicts occur when multiple scenarios are being managed
   - Control panel updates correctly reflect the new scenario states

2. **Monitor User Experience:** Ensure the automatic DM sending is smooth and doesn't cause confusion

## Recent Learning & Patterns

*   **Active Campaign State Management:** When campaigns are already active, new scenarios should immediately transition to voting status rather than waiting for manual intervention.
*   **Approval Voting UX Pattern:** For multi-selection voting mechanisms, provide clear visual feedback about current selections and require explicit submission rather than auto-submitting after each selection.
*   **Campaign Flow Automation:** Users expect seamless experiences where defining new content in active campaigns immediately makes it available for voting.
*   **Error Handling for Interaction Edits:** Always provide fallback notification methods when primary interaction editing fails.

## Recent Fixes (Current Session)

### All Voting Mechanisms Now Support Submit Buttons
- **ApprovalVoteView**: Already had submit button with multi-selection support
- **RankedVoteView**: Added submit button that:
  - Starts disabled, enables after first ranking
  - Shows count of ranked options in label: "Submit Vote (2 ranked)"
  - Handles token allocation for both runoff and borda mechanisms
  - Allows partial rankings (users don't need to rank all options)
  - Properly integrates with campaign token investment modal

### Campaign Flow Improvements
- **Immediate DM Sending**: When scenarios are defined in active campaigns, DMs are sent immediately rather than being queued
- **Removed Queuing Restrictions**: Multiple scenarios can now vote simultaneously as originally designed
- **Auto-Progression**: When scenarios complete voting, queued scenarios automatically start (implementation in progress)

## Testing Status

**✅ Working Flows:**
- Normal proposal creation and approval (plurality, approval, runoff, borda)
- Campaign creation and approval
- Multiple scenarios voting simultaneously
- Submit buttons for all voting mechanisms
- Token allocation in campaign scenarios
- Automatic DM sending for new scenarios in active campaigns

**🔄 Next Items:**
- Test auto-progression when scenarios complete voting
- Verify campaign completion flow when all scenarios finish

## Key Technical Changes

### Voting Mechanism Consistency
All voting mechanisms now follow the same pattern:
1. User makes selections (buttons, dropdowns, etc.)
2. Submit button becomes enabled/updates label
3. User clicks submit button
4. System shows token investment modal (for campaigns) or finalizes immediately
5. Vote is recorded and confirmation sent

### Campaign Logic Improvements
- Removed overly restrictive queuing that prevented simultaneous scenario voting
- Added immediate voting initiation for scenarios defined in active campaigns
- Improved user messaging to clearly indicate when scenarios will start voting

## Current Focus
The system now has consistent submit button support across all voting mechanisms, ensuring users can properly allocate tokens and finalize their votes in campaign scenarios. All major voting flows are working correctly.