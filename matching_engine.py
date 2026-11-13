
import csv
import re
from pathlib import Path
from difflib import SequenceMatcher

# ==========================================
# CONFIGURATION
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

FACILITY_FILE = BASE_DIR / "bellhaven_facilities.csv"
CRM_FILE = BASE_DIR / "crm_accounts.csv"

MATCH_OUTPUT = BASE_DIR / "facility_crm_matches.csv"
MISSING_OUTPUT = BASE_DIR / "potential_missing_accounts.csv"
DUPLICATE_OUTPUT = BASE_DIR / "potential_duplicate_accounts.csv"

MATCH_THRESHOLD = 85
REVIEW_THRESHOLD = 60

# ==========================================
# DATA CLEANING
# ==========================================

def clean(value):
    if value is None:
        return ""

    value = str(value).lower().strip()
    value = value.replace("&", " and ")

    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_address(value):
    value = clean(value)

    replacements = {
        "street": "st",
        "road": "rd",
        "avenue": "ave",
        "drive": "dr",
        "boulevard": "blvd",
        "lane": "ln",
        "north": "n",
        "south": "s",
        "east": "e",
        "west": "w",
    }

    words = value.split()

    return " ".join(
        replacements.get(word, word)
        for word in words
    )


def similarity(a, b):
    a = clean(a)
    b = clean(b)

    if not a or not b:
        return 0.0

    return SequenceMatcher(
        None, a, b
    ).ratio() * 100


# ==========================================
# LOAD CSV FILES
# ==========================================

def load_csv(filepath):
    if not filepath.exists():
        raise FileNotFoundError(
            f"File not found: {filepath}"
        )

    with open(
        filepath,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        return list(csv.DictReader(file))


# ==========================================
# MATCHING ENGINE
# ==========================================

def calculate_match(facility, account):

    facility_name = facility.get("name", "")
    account_name = account.get("name", "")

    facility_address = normalize_address(
        facility.get("address", "")
    )

    account_address = normalize_address(
        account.get("billing_street", "")
    )

    facility_city = facility.get("city", "")
    account_city = account.get("billing_city", "")

    facility_state = facility.get("state", "")
    account_state = account.get("billing_state", "")

    facility_zip = facility.get("zip", "")
    account_zip = account.get("billing_zip", "")

    name_score = similarity(
        facility_name, account_name
    )

    address_score = similarity(
        facility_address, account_address
    )

    city_score = similarity(
        facility_city, account_city
    )

    state_score = similarity(
        facility_state, account_state
    )

    zip_score = (
        100
        if clean(facility_zip)
        and clean(facility_zip) == clean(account_zip)
        else 0
    )

    components = [
        (name_score, 0.40, bool(clean(facility_name) and clean(account_name))),
        (address_score, 0.30, bool(facility_address and account_address)),
        (city_score, 0.15, bool(clean(facility_city) and clean(account_city))),
        (state_score, 0.05, bool(clean(facility_state) and clean(account_state))),
        (zip_score, 0.10, bool(clean(facility_zip) and clean(account_zip))),
    ]

    available_weight = sum(
        weight for score, weight, available in components
        if available
    )

    weighted_score = sum(
        score * weight
        for score, weight, available in components
        if available
    )

    # Missing fields must not artificially boost confidence.
    score = weighted_score

    # Penalize conflicting geographic information.
    if (
        clean(facility_state)
        and clean(account_state)
        and clean(facility_state) != clean(account_state)
    ):
        score = min(score, 59)

    if (
        clean(facility_zip)
        and clean(account_zip)
        and clean(facility_zip) != clean(account_zip)
    ):
        score = min(score, 79)

    return round(score, 1)


def classify_match(score):
    if score >= MATCH_THRESHOLD:
        return "Potential Existing Account"

    if score >= REVIEW_THRESHOLD:
        return "Needs Manual Review"

    return "Potential Missing Account"


# ==========================================
# SAVE RESULTS
# ==========================================

def save_csv(filename, rows, fieldnames):
    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore"
        )

        writer.writeheader()
        writer.writerows(rows)


# ==========================================
# MAIN PROGRAM
# ==========================================

def main():

    print("=" * 60)
    print("BELLHAVEN CRM RECONCILIATION")
    print("=" * 60)

    facilities = load_csv(FACILITY_FILE)
    accounts = load_csv(CRM_FILE)

    print(f"Facilities loaded: {len(facilities)}")
    print(f"CRM accounts loaded: {len(accounts)}")

    results = []
    missing_accounts = []
    duplicate_candidates = []

    for index, facility in enumerate(facilities, 1):

        facility_name = facility.get("name", "").strip()

        comparisons = []

        for account in accounts:

            score = calculate_match(
                facility, account
            )

            comparisons.append({
                "account": account,
                "score": score
            })

        comparisons.sort(
            key=lambda item: item["score"],
            reverse=True
        )

        best = comparisons[0] if comparisons else None

        best_score = best["score"] if best else 0
        best_account = best["account"] if best else {}

        status = classify_match(best_score)

        result = {
            "facility_name": facility_name,
            "facility_address": facility.get("address", ""),
            "facility_city": facility.get("city", ""),
            "facility_state": facility.get("state", ""),
            "facility_zip": facility.get("zip", ""),
            "crm_account_id": best_account.get("account_id", ""),
            "crm_account_name": best_account.get("name", ""),
            "crm_address": best_account.get("billing_street", ""),
            "match_score": best_score,
            "match_status": status,
        }

        results.append(result)

        if status == "Potential Missing Account":
            missing_accounts.append(result)

        # Identify multiple strong CRM candidates.
        strong_matches = [
            item for item in comparisons
            if item["score"] >= MATCH_THRESHOLD
        ]

        if len(strong_matches) > 1:
            for item in strong_matches:
                duplicate_candidates.append({
                    "facility_name": facility_name,
                    "crm_account_id": item["account"].get(
                        "account_id", ""
                    ),
                    "crm_account_name": item["account"].get(
                        "name", ""
                    ),
                    "match_score": item["score"],
                    "review_reason": "Multiple strong CRM candidates"
                })

        print(
            f"{index}/{len(facilities)} | "
            f"{facility_name} -> "
            f"{best_account.get('name', 'No match')} | "
            f"Score: {best_score} | "
            f"{status}"
        )

    # ======================================
    # EXPORT
    # ======================================

    match_fields = [
        "facility_name",
        "facility_address",
        "facility_city",
        "facility_state",
        "facility_zip",
        "crm_account_id",
        "crm_account_name",
        "crm_address",
        "match_score",
        "match_status",
    ]

    save_csv(
        MATCH_OUTPUT,
        results,
        match_fields
    )

    save_csv(
        MISSING_OUTPUT,
        missing_accounts,
        match_fields
    )

    save_csv(
        DUPLICATE_OUTPUT,
        duplicate_candidates,
        [
            "facility_name",
            "crm_account_id",
            "crm_account_name",
            "match_score",
            "review_reason",
        ]
    )

    # ======================================
    # SUMMARY
    # ======================================

    print("\n" + "=" * 60)
    print("RECONCILIATION SUMMARY")
    print("=" * 60)

    print("Total facilities:", len(facilities))

    for status in [
        "Potential Existing Account",
        "Needs Manual Review",
        "Potential Missing Account",
    ]:

        count = sum(
            1 for result in results
            if result["match_status"] == status
        )

        print(f"{status}: {count}")

    print(
        "Duplicate candidate records:",
        len(duplicate_candidates)
    )

    print("\nFILES SAVED:")
    print(MATCH_OUTPUT.name)
    print(MISSING_OUTPUT.name)
    print(DUPLICATE_OUTPUT.name)

    print("\nRECONCILIATION COMPLETED")


if __name__ == "__main__":
    main()
