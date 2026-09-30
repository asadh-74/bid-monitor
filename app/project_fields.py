"""Source-grounded display fields without changing the existing sheet columns."""
import re

MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"


def date_year(value):
    text = str(value or "").strip()
    for pattern in (r"\b(20\d{2})[-/]\d{1,2}[-/]\d{1,2}(?=T|\b)",
                    r"\b\d{1,2}[/.-]\d{1,2}[/.-](20\d{2})\b",
                    MONTHS + r"\s+(?:\d{1,2},?\s+)?(20\d{2})\b"):
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    if re.fullmatch(r"20\d{2}", text):
        return text
    return ""


def project_year(record):
    # Deadline is a source project date; first/last-seen timestamps are never used.
    year = date_year(record.get("deadline"))
    if year:
        return year
    description = record.get("description", "")
    for label in ("Contract award date", "Advertisement date", "Broadcast", "Fiscal year"):
        match = re.search(r"(?im)(?:^|[;\n])\s*" + label + r":\s*([^;\n]+)", description)
        if match:
            year = date_year(match.group(1))
            if year:
                return year
    return ""


def amount_from_text(text):
    # A currency amount requires an explicit financial label.
    money = r"\$\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:million|billion|thousand|[MBK])\b)?"
    match = re.search(r"(?i)\b(Bid amount|Bid price|Award amount|Contract value|Estimated cost|Estimated value|Budget(?: range)?|Construction estimate)\s*:?\s*(" + money + r"(?:\s*(?:to|[-–])\s*" + money + r")?)", str(text or ""))
    return f"{match.group(1)}: {match.group(2)}" if match else ""


def bidding_amount(record):
    text = record.get("description", "")
    explicit = re.search(r"(?m)^Bidding amount:\s*(.+)$", text)
    if explicit:
        return explicit.group(1)
    contacts = []
    for line in text.splitlines():
        match = re.fullmatch(r"(Award|Bidder|General contractor|Contractor): (.+?); phone: .*?; email: .*?; amount: (\$?\s*\d[\d,]*(?:\.\d+)?)", line, re.I)
        if match and float(match.group(3).strip().replace('$', '').replace(',', '')) > 0:
            contacts.append(f"{match.group(1)} — {match.group(2)}: {match.group(3)}")
    if contacts:
        return "; ".join(contacts)
    labelled = amount_from_text(text)
    if labelled:
        return labelled
    title = record.get("title", "")
    threshold = re.search(r"\bOver\s*(\d+(?:\.\d+)?)\s*M\b", title, re.I)
    if threshold:
        return "Estimated range: over $" + threshold.group(1) + " million"
    # SCA titles include a stated JOC ceiling; retain it as contract value.
    if record.get("source", "").startswith("sca_"):
        value = re.search(r"\$\s*\d[\d,]*(?:\.\d+)?", title)
        if value:
            return "Listed contract value: " + value.group()
    return ""


def display_project(record):
    return {**record, "project_year": project_year(record), "bidding_amount": bidding_amount(record)}
