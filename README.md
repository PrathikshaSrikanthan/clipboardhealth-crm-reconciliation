## Project Overview

A Python-based CRM reconciliation system developed to identify and review inconsistencies between Bellhaven Senior Living's facility website and a CRM sandbox.

The project combines facility data extraction, record matching, duplicate detection, and a Streamlit review application to support accurate CRM maintenance.

The application requires human approval before making CRM changes through the REST API.

## Key Features

### 1. Facility Data Extraction
- Extracts Bellhaven facility information, including names, addresses, cities, states, ZIP codes, and care offerings.
- Stores facility information in structured CSV files for analysis.

### 2. CRM Matching and Reconciliation
- Compares website facilities against CRM accounts.
- Uses normalized names, addresses, geographic information, and weighted similarity scores.
- Identifies potential matches, missing accounts, duplicate candidates, and CRM-only accounts.
- Flags inconsistencies requiring manual review.

### 3. Human-in-the-Loop Review
- Interactive review dashboard built using Streamlit.
- Displays website evidence alongside CRM account information.
- Allows reviewers to approve or reject proposed changes.
- Requires reviewer explanations and confirmation before approved API writes.
- Records decisions in a SQLite database.

### 4. CRM API Integration
Supports approved changes through the CRM REST API, including:

- Updating existing account information using PATCH.
- Creating missing accounts using POST.
- Marking duplicate accounts inactive.
- Updating parent-company relationships when permitted.

All API write operations require explicit reviewer approval.

### 5. Decision Persistence and Safe Reruns
- Review decisions are stored in `review_decisions.sqlite3`.
- Previously approved and rejected proposals are excluded from the pending review queue.
- Proposal identifiers help prevent repeated processing of previously reviewed items.
- A matching-engine rerun was tested successfully, retaining 34 saved decisions with zero pending proposals.

The current implementation uses CSV snapshots. Additional safeguards would be required for fully automated synchronization with refreshed CRM data and recovery from interrupted API operations.

### 6. Change of Ownership (CHOW) Safeguards
The application checks `lifetime_revenue` and `outstanding_ar` before changing an account's parent.

- If either value is zero, direct reparenting is permitted.
- If both values are greater than zero, direct reparenting is blocked to preserve billing history.

The complete CHOW create-and-link workflow remains a planned improvement. The application currently prevents unsafe direct parent changes.

### 7. Daily Scheduling Configuration
A `daily_schedule.cron` configuration specifies how the matching engine can be scheduled to run daily.

The schedule is provided as configuration only and is not deployed. The current matching engine processes locally available CSV snapshots.

## Technology Stack

- Python
- Streamlit
- Pandas / CSV processing
- REST API
- SQLite
- JSON
- Requests
- Python-dotenv
- GitHub
- Visual Studio Code

## Project Workflow

1. Extract facility information from the Bellhaven website.
2. Retrieve CRM account data.
3. Normalize facility and account information.
4. Calculate weighted matching scores.
5. Identify inconsistencies and potential duplicates.
6. Generate reconciliation proposals.
7. Review proposals in Streamlit.
8. Approve or reject changes based on supporting evidence.
9. Submit approved changes through the CRM API.
10. Save reviewer decisions and suppress previously reviewed proposals.

## Running the Application

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure the required environment variables in a local `.env` file:

```env
CRM_API_TOKEN=your_token_here
CRM_WRITE_ENABLED=0
```

Start the matching engine:

```bash
python matching_engine.py
```

Launch the review dashboard:

```bash
streamlit run app.py
```

Set `CRM_WRITE_ENABLED=1` only when authorized CRM modifications are intended.

**Security:** The `.env` file and API credentials must not be committed to the public repository.

## Review Results

During the assessment:

- 34 review decisions were recorded.
- Approved and rejected decisions were persisted in SQLite.
- CRM API write functionality was exercised.
- The review queue showed zero pending proposals.
- Rerunning the matching engine preserved the 34 decisions and zero pending proposals.

These results demonstrate decision persistence and suppression for the tested dataset.

## How AI Tools Were Used

AI tools, including ChatGPT, supported development by assisting with:

- Python and Streamlit application structure.
- Facility matching and normalization logic.
- REST API integration and debugging.
- SQL database inspection.
- Error handling and workflow improvements.
- Technical documentation and testing guidance.

AI-generated suggestions were reviewed before use. Facility-level decisions and CRM changes remained subject to human review and approval.

## Future Improvements

- Implement the complete CHOW create-and-link workflow.
- Automatically refresh website and CRM data before each reconciliation.
- Improve duplicate detection and address standardization.
- Add stronger idempotency safeguards for API operations.
- Introduce automated tests and monitoring.
- Add audit reporting and change-history dashboards.

## Project Objective

To demonstrate a practical, human-reviewed CRM reconciliation workflow that improves facility data quality, reduces duplicate records, and supports accurate parent-company relationships while protecting historical billing information.
