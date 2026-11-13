# clipboardhealth-crm-reconciliation
Python and Streamlit-based CRM reconciliation tool for facility matching, duplicate detection, and human-reviewed CRM updates.
CRM Facility Reconciliation Tool

Sales Operations Analyst Assessment | Clipboard Health

Project Overview

Developed a Python-based CRM reconciliation application using Streamlit to compare facility information from the Bellhaven Senior Living website against existing CRM records.

The project focuses on identifying data discrepancies, detecting duplicate accounts, reviewing potential missing facilities, and ensuring accurate CRM records through a human-reviewed approval workflow.

## Key Features

- **Facility Matching:** Compares website facility listings with CRM records using facility names, addresses, and location details.
- **Duplicate Detection:** Identifies multiple CRM accounts potentially representing the same facility.
- **Data Quality Checks:** Flags discrepancies in facility names, addresses, ZIP codes, and parent-company relationships.
- **Human Review Workflow:** Allows users to approve or reject proposed CRM corrections with documented explanations.
- **CRM API Integration:** Supports authorized account creation and updates through REST API requests.
- **Change of Ownership (CHOW) Safeguards:** Helps protect historical financial records when facility ownership changes.
- **Decision Tracking:** Records reconciliation decisions for review and auditability.

 Technologies Used

- Python
- Streamlit
- Pandas
- REST APIs
- JSON
- SQLite
- Visual Studio Code


## How AI Was Used

AI-assisted development was incorporated into the project to improve efficiency, support problem-solving, and accelerate implementation.

**1. Code Development**

AI was used to assist with developing Python scripts, structuring the Streamlit application, and implementing data reconciliation functionality.

**2. Data Matching and Reconciliation Logic**

AI supported the design and refinement of matching rules for comparing facility names, addresses, and CRM records. The application uses programmed matching logic rather than relying on AI to independently authorize CRM changes.

**3. Debugging and Troubleshooting**

AI assisted in identifying coding errors, troubleshooting API integration issues, and improving application functionality.

**4. Workflow Optimization**

AI helped structure the approval and rejection process, including duplicate detection, account updates, and change-of-ownership safeguards.

**5. Documentation**

AI assisted in organizing technical documentation and explaining the application's functionality.

### Human Oversight

AI was used as a development and problem-solving assistant, not as an autonomous decision-maker.

Final reconciliation decisions were based on available website evidence, CRM information, business rules, and human review. CRM modifications required explicit approval before execution.

## Project Workflow

1. Extract facility information from the website.
2. Retrieve existing CRM account information.
3. Compare facility names, addresses, and parent-company details.
4. Identify potential matches, duplicates, and missing accounts.
5. Present reconciliation proposals in the Streamlit dashboard.
6. Review supporting evidence and select an appropriate action.
7. Approve or reject proposed CRM changes.
8. Verify successful updates and maintain decision records.

## Running the Application

Install the required dependencies:

`pip install -r requirements.txt`

Configure the required CRM environment variables in a local `.env` file.

Launch the application:

`streamlit run app.py`

## Data Security

API tokens and credentials are stored in environment variables and should not be committed to the public repository.

Sensitive CRM data and local environment files are excluded from public sharing.

## Project Objective

To demonstrate how Python, data reconciliation techniques, API integration, AI-assisted development, and human decision-making can improve CRM data quality and support efficient sales operations.
