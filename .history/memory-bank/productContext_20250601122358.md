# Product Context: Governance Bot UX and Purpose

## Problem Solved

Discord's native slash command forms (modal inputs and drop-downs) lack support for dynamic behavior, such as showing or hiding form fields based on previous user selections within the same form. This limitation hinders the creation of highly customizable voting proposals where different voting models require different sets of hyperparameters.

## How It Should Work (User Experience)

To overcome Discord's limitations and provide a flexible proposal creation experience, a two-step interaction model has been implemented:

1.  **Initial Command & Mechanism Selection:**
    *   User invokes a command (e.g., `!propose` or a slash command `/propose`).
    *   The bot responds with a message containing several Discord buttons, each representing a distinct voting model (e.g., "Plurality", "Borda Count", "Approval Voting").

2.  **Model-Specific Modal Form:**
    *   When the user clicks one of the mechanism buttons, the bot presents a Discord Modal form.
    *   This modal is specific to the selected voting model and includes fields for common proposal details (title, description, options, deadline) as well as input fields for only the relevant tunable hyperparameters for that particular model.

This two-step process allows for dynamic control over the form fields presented to the user, ensuring they only see and configure parameters applicable to their chosen voting mechanism.

This is further extended with **Weighted Campaigns**, where a campaign creator defines a series of voting scenarios. Voters receive campaign tokens to invest in these scenarios, adding a strategic layer to participation.

## User Experience Goals

*   **Intuitive Proposal Setup:** Make it easy and clear for users to define complex proposals without being overwhelmed by irrelevant options.
*   **Flexibility:** Support a variety of common and advanced voting models.
*   **Clarity:** Provide clear feedback to the user throughout the proposal creation, voting, and results announcement process.
*   **Efficiency:** Automate as much of the governance lifecycle as possible (deadline tracking, result calculation, announcements).