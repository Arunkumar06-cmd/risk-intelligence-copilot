"""Demo mode: fake sample data, no Snowflake, no AI call. Simple English.

Used by the public demo link (GitHub Pages, app runs in the browser via stlite) and by
anyone who sets DEMO_MODE=TRUE. Everything here is made up for the demo:
- Transactions and accounts are invented. No real people or banks.
- Policy clauses are SHORT SAMPLE TEXT written for this demo, not quotes of real law.
- The SAR draft is filled from a fixed template (no AI model is called).
- The audit log lives in memory only (lost when the page reloads).
The real app path (Snowflake + Cortex AI) is not changed by this file.
"""
import re

DEMO_MODEL = "demo-template"
DEMO_MODEL_VERSION = "demo-template-v1"

# Invented accounts. RISK_TIER RESTRICTED rows are hidden from non-officers,
# same rule as the real app (sql/01 TRANSACTIONS_ROLE_FILTER + app-side filter).
DEMO_ACCOUNTS = {
    "ACC-1001": {"customer_name": "Asha Traders (sample)", "risk_tier": "HIGH"},
    "ACC-1002": {"customer_name": "Blue Kite Exports (sample)", "risk_tier": "MEDIUM"},
    "ACC-1003": {"customer_name": "Coral Bay Holdings (sample)", "risk_tier": "RESTRICTED"},
    "ACC-1004": {"customer_name": "Delta Fresh Foods (sample)", "risk_tier": "LOW"},
}

DEMO_TRANSACTIONS = [
    {"tx_id": "TX-9001", "account_id": "ACC-1001", "amount": 48500.00, "currency": "USD", "tx_date": "2026-09-28", "merchant": "Wire out - high-risk country A (sample)"},
    {"tx_id": "TX-9002", "account_id": "ACC-1001", "amount": 49200.00, "currency": "USD", "tx_date": "2026-09-28", "merchant": "Wire out - high-risk country A (sample)"},
    {"tx_id": "TX-9003", "account_id": "ACC-1001", "amount": 47900.00, "currency": "USD", "tx_date": "2026-09-27", "merchant": "Wire out - high-risk country B (sample)"},
    {"tx_id": "TX-9004", "account_id": "ACC-1003", "amount": 120000.00, "currency": "USD", "tx_date": "2026-09-26", "merchant": "Shell company transfer (sample)"},
    {"tx_id": "TX-9005", "account_id": "ACC-1002", "amount": 9800.00, "currency": "USD", "tx_date": "2026-09-25", "merchant": "Cash deposit - branch 12 (sample)"},
    {"tx_id": "TX-9006", "account_id": "ACC-1002", "amount": 9750.00, "currency": "USD", "tx_date": "2026-09-25", "merchant": "Cash deposit - branch 14 (sample)"},
    {"tx_id": "TX-9007", "account_id": "ACC-1004", "amount": 1250.00, "currency": "USD", "tx_date": "2026-09-24", "merchant": "Grocery supplier (sample)"},
    {"tx_id": "TX-9008", "account_id": "ACC-1004", "amount": 890.00, "currency": "USD", "tx_date": "2026-09-23", "merchant": "Utility bill (sample)"},
]

# Sample policy clauses. Written for this demo only. NOT real regulation text.
DEMO_POLICIES = [
    {"policy_id": "DEMO-POL-01", "title": "Suspicious activity reporting (sample)",
     "clause_text": "Sample clause: when a pattern of transactions has no clear business reason, the analyst drafts a suspicious activity report and an officer reviews it before filing."},
    {"policy_id": "DEMO-POL-02", "title": "High-risk country wires (sample)",
     "clause_text": "Sample clause: repeated wire transfers to high-risk countries, close to reporting limits or in short time windows, need enhanced review."},
    {"policy_id": "DEMO-POL-03", "title": "Cash structuring (sample)",
     "clause_text": "Sample clause: several cash deposits just under a reporting limit, at different branches, may show structuring (splitting money to avoid reports)."},
    {"policy_id": "DEMO-POL-04", "title": "Record keeping (sample)",
     "clause_text": "Sample clause: keep the evidence IDs, policy references and the approver name with every filed report."},
]

# In-memory audit log for the demo (one list per Python process / browser tab).
DEMO_AUDIT_LOG = []

_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set:
    return set(_WORD_RE.findall(str(text or "").lower()))


def search(question: str, k: int) -> list:
    """Rank sample clauses by shared words. Score is word overlap (0-1), not cosine."""
    q = _words(question)
    scored = []
    for p in DEMO_POLICIES:
        w = _words(p["title"] + " " + p["clause_text"])
        score = (len(q & w) / len(q)) if q else 0.0
        row = dict(p)
        row["sim"] = round(score, 3)
        scored.append(row)
    # Stable order: higher score first, then policy id.
    scored.sort(key=lambda r: (-r["sim"], r["policy_id"]))
    return scored[: max(1, int(k))]


def transactions(account_filter: str, limit: int, offset: int, is_officer: bool) -> list:
    """Filter + page the sample rows. Non-officers never see RESTRICTED accounts."""
    rows = []
    filt = str(account_filter or "").strip().lower()
    for t in sorted(DEMO_TRANSACTIONS, key=lambda r: (r["tx_date"], r["tx_id"]), reverse=True):
        acct = DEMO_ACCOUNTS.get(t["account_id"], {})
        if not is_officer and acct.get("risk_tier") == "RESTRICTED":
            continue
        if filt and filt not in t["account_id"].lower():
            continue
        rows.append(dict(t))
    return rows[int(offset): int(offset) + int(limit)]


def complete(prompt: str) -> str:
    """Fill a fixed SAR template from the prompt's evidence + policy lines. No AI call."""
    ev, pol = [], []
    section = ""
    for line in str(prompt or "").splitlines():
        s = line.strip()
        if s.startswith("Evidence:"):
            section = "ev"
            continue
        if s.startswith("Policies:"):
            section = "pol"
            continue
        if s.startswith("Rules:"):
            section = ""
            continue
        if s.startswith("- ") and section == "ev":
            ev.append(s[2:].split(" ")[0])
        elif s.startswith("- ") and section == "pol":
            pol.append(s[2:].split(" ")[0])
    ev_txt = ", ".join(ev) if ev else "none found"
    pol_txt = ", ".join(pol) if pol else "none found"
    return (
        "DEMO DRAFT (template, no AI model was called)\n"
        "Summary: the listed transactions show a pattern that needs officer review.\n"
        f"Evidence IDs: {ev_txt}\n"
        f"Policy references: {pol_txt}\n"
        "Next step: officer reviews the evidence and approves or rejects this draft."
    )
