# Project Brief: Governance Automation Discord Bot

## Core Goal

To develop a Discord bot that automates governance processes within a Discord server. The primary focus is on enabling highly customizable voting proposals, allowing users to define various voting models and their specific parameters.

## Key Features

*   **Customizable Proposal Creation:** Allow users to create proposals with different voting mechanisms (e.g., Plurality, Borda Count, Approval Voting, Runoff).
*   **Weighted Campaigns:** Allow creation of multi-scenario campaigns where users invest tokens to weight their votes in each scenario.
*   **Hyperparameter Configuration:** Enable configuration of model-specific hyperparameters for each proposal (e.g., custom winning thresholds, allowance of abstain votes).
*   **Dynamic Form Generation:** Implement a user-friendly way to present form fields dynamically based on selected voting models, overcoming Discord API limitations.
*   **Database Persistence:** Store proposal details, votes, server settings, and constitutional variables in an SQLite database.
*   **Moderation Tools:** Include basic moderation commands (warn, mute, ban).
*   **Constitutional Variables:** Allow server administrators to define and manage core governance rules and settings.
*   **Automated Processes:** Handle proposal deadlines, vote counting, and result announcements automatically.