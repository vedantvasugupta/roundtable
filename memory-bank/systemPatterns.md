# System Patterns: Governance Bot Architecture

## High-Level Architecture

The system is a Python-based Discord bot designed for asynchronous operation using the `discord.py` library. It interacts with an SQLite database via `aiosqlite` for persistent storage of all governance-related data.

## Core Components and Modules

*   **`main.py`**: Entry point of the bot. Initializes the bot, loads cogs/modules, sets up event listeners (e.g., `on_ready`), and background tasks.
*   **`db.py`**: Handles all database interactions. Includes functions for initializing the database schema, and CRUD (Create, Read, Update, Delete) operations for proposals, votes, settings, users, campaigns, etc. It uses `aiosqlite` for asynchronous database access.
*   **`proposals.py`**: Manages the proposal lifecycle. This includes:
    *   The new two-step proposal creation UX (Mechanism Selection View with buttons -> Mechanism-specific Modal).
    *   Logic for creating, approving, and rejecting proposals.
    *   **Campaign and Scenario Definition UI:** Includes `CampaignSetupModal` for overall campaign settings and `DefineScenarioView` for stepping through scenario creation, linking to `ProposalMechanismSelectionView` for each scenario.
    *   **Campaign Approval and Progression Logic:** Handles admin approval for campaigns. If a campaign is approved (status 'setup' or 'active'), subsequently defined scenarios are automatically set to 'ApprovedScenario'. Manages the `CampaignControlView` for starting campaigns and advancing through scenarios.
    *   Interaction with `db.py` to store and retrieve proposal and campaign data.
*   **`voting.py`**: Handles the voting process. This includes:
    *   Generating voting interfaces (e.g., DMs with buttons/dropdowns for different voting mechanisms).
    *   **Campaign Voting Context:** DM interfaces (`BaseVoteView` subclasses) are being updated to handle campaign context (displaying campaign info, user's remaining tokens) and prompt for token investment using `TokenInvestmentModal`.
    *   Processing and validating votes.
    *   Storing votes in the database via `db.py` (including `tokens_invested` and `is_abstain`).
*   **`voting_utils.py`**: Contains utility functions related to voting, such as vote counting for different mechanisms, result formatting, deadline checking, and result announcement.
    *   **Weighted Vote Counting:** Vote counting mechanisms (`PluralityVoting.count_votes`, `BordaCount.count_votes`, etc.) have been updated to factor in `tokens_invested` as a multiplier for vote weight.
*   **`utils.py`**: A collection of general utility functions used across the bot (e.g., parsing durations, managing Discord channels, formatting text, creating embeds).
*   **`moderation.py`**: Implements moderation commands and logic (warnings, mutes, bans).

## Automated Channel Creation and Management
*   Standard governance channels (`proposals`, `voting-room`, `governance-results`, `audit-log`) are automatically created/verified on bot startup for each guild and when the bot joins a new guild.
*   A `server-guide` channel is also automatically created/verified. This channel receives a comprehensive, single-embed message detailing server channels, voting protocol explanations, and general bot usage. It is set to read-only for users, with the bot having send permissions. The guide embed is refreshed (old one purged, new one sent) on startup/join.

## Key Design Patterns

*   **Modular Design:** Functionality is separated into different Python modules/files (as listed above) to improve organization and maintainability.
*   **Asynchronous Programming:** Extensive use of `async` and `await` for non-blocking operations, essential for a responsive Discord bot handling multiple concurrent interactions and background tasks.
*   **View-Modal Interaction for Proposal/Scenario Creation:**
    1.  A `discord.ui.View` (`ProposalMechanismSelectionView`) presents buttons for choosing a voting mechanism.
    2.  Clicking a button triggers a callback that opens a specific `discord.ui.Modal`. These modals inherit from `BaseProposalModal` (providing 3 common fields: Title, Options, Deadline) and then add up to 2 mechanism-specific `TextInput` fields for hyperparameters. This structure ensures compliance with Discord's 5 `TextInput` component limit per modal. The proposal "Description" field was removed from this UI flow and is now defaulted.
*   **Sequential View/Modal Flow for Campaign Setup:**
    1.  `ProposalMechanismSelectionView` offers a "Create Weighted Campaign" button (if not already in a campaign flow).
    2.  This opens `CampaignSetupModal` (for campaign title, description, total tokens per voter, number of scenarios).
    3.  On submission, `db.create_campaign` is called. If successful, a campaign record is created (default status 'pending_approval'). Admins are notified via a message with `AdminCampaignApprovalView`.
    4.  **Admin Approval:** Admins use `AdminCampaignApprovalView` to approve or reject the campaign.
        *   **On Approval:** Campaign status becomes 'setup'. `_perform_approve_campaign_action` posts a `CampaignControlView` to the campaign management channel. Any existing 'Pending Approval' scenarios for this campaign are set to 'ApprovedScenario'.
        *   **On Rejection:** Campaign status becomes 'rejected'. Creator is notified.
    5.  **Scenario Definition (via `CampaignControlView` or initial DM after approval):
        *   The campaign creator (or admin) uses the "Define Next Scenario" button on `CampaignControlView` (or a `StartScenarioDefinitionView` sent via DM if that flow is re-enabled).
        *   This opens `DefineScenarioView` for the next scenario.
        *   `DefineScenarioView`'s button leads to `ProposalMechanismSelectionView` (passing `campaign_id`, `scenario_order`).
        *   User selects a mechanism, fills the specific proposal modal (e.g., `PluralityProposalModal`).
        *   On scenario submission (`_create_new_proposal_entry`):
            *   If the campaign status is 'setup' or 'active', the scenario is created with status 'ApprovedScenario'.
            *   `db.increment_defined_scenarios` is called.
            *   The `CampaignControlView` is updated to reflect the new scenario definition.
            *   The user is prompted to define the next scenario or informed if all are defined.
    6.  **Starting the Campaign/Next Scenario (via `CampaignControlView`):
        *   Creator/Admin uses the "Start Campaign / Start Scenario X" button.
        *   If campaign is 'setup' and Scenario 1 is 'ApprovedScenario', campaign status becomes 'active', and `voting_utils.initiate_voting_for_proposal` is called for Scenario 1.
        *   If campaign is 'active', no current scenario is 'Voting', and the next sequential scenario is 'ApprovedScenario', `voting_utils.initiate_voting_for_proposal` is called for it.
        *   The `CampaignControlView` and its embed are updated to reflect the new state.
*   **View/Modal Flow for Campaign Voting (DM):**
    1.  `send_voting_dm` (in `voting.py`) prepares a DM for a campaign scenario.
    2.  The DM includes a view derived from `BaseVoteView` (e.g., `PluralityVoteView`).
    3.  When the user makes their mechanism-specific selection, `BaseVoteView.submit_vote_callback` is triggered.
    4.  If it's a campaign scenario, `TokenInvestmentModal` is presented.
    5.  User inputs tokens to invest. On modal submission, `BaseVoteView.finalize_vote` is called with `tokens_invested_this_scenario`.
    6.  `finalize_vote` calls `process_vote`, which then calls `db.record_vote` (storing tokens) and updates user's campaign tokens via `db.update_user_remaining_tokens`.
*   **Database Abstraction:** Database logic is encapsulated within `db.py`, so other modules don't interact directly with SQL queries but call dedicated asynchronous functions.
*   **Configuration via Database:** Server-specific settings and constitutional variables are stored in and read from the database, allowing for per-server customization.
*   **Background Tasks:** `discord.py`'s `tasks` extension is used for periodic checks like proposal deadlines, pending result announcements, and expired moderations.

## Data Storage

*   **SQLite Database (`bot_database.db`):**
    *   `proposals` table: Stores proposal details, including title, description, proposer, voting mechanism, deadline, status, and a JSON field for `hyperparameters`. Has `campaign_id` (FK to `campaigns`) and `scenario_order` columns.
    *   `proposal_options` table: Stores the specific options for each proposal.
    *   `votes` table: Records individual votes. Has `tokens_invested` and `is_abstain` columns.
    *   `campaigns` table (NEW): Stores `campaign_id`, `guild_id`, `creator_id`, `title`, `description`, `total_tokens_per_voter`, `num_expected_scenarios`, `current_defined_scenarios`, `status` (e.g., 'pending_approval', 'setup', 'active', 'completed', 'rejected', 'archived'), `creation_timestamp`, `control_message_id`.
    *   `user_campaign_participation` table (NEW): Stores `participation_id`, `campaign_id`, `user_id`, `remaining_tokens`, `last_updated_timestamp`.
    *   `settings` table: Stores server-specific settings.
    *   `constitutional_variables` table: Stores server-specific governance rules.
    *   Other tables for users, warnings, moderation actions, etc.
*   **JSON for Hyperparameters:** Voting mechanism-specific hyperparameters are stored as a JSON string in the `proposals` table, allowing for flexibility as different mechanisms have different configuration needs.