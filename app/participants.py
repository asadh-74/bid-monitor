"""Role-labelled companies stored in the existing Description column."""
import json
import re

PREFIX = "Project participants: "
ROLES = {"plan holders", "planholder", "planholders", "bidder", "bid result",
         "award", "general contractor", "contractor", "cm", "supplemental vendor"}


def read_participants(record):
    result = []
    for line in record.get("description", "").splitlines():
        if line.startswith(PREFIX):
            try:
                values = json.loads(line[len(PREFIX):])
            except (ValueError, TypeError):
                continue
            if isinstance(values, list):
                result.extend(v for v in values if isinstance(v, dict) and v.get("name")
                              and v.get("role", "").lower() in ROLES)
            continue
        match = re.fullmatch(r"(.+?): (.+?); phone: (.*?); email: (.*?); amount: (.*)", line)
        if match and match.group(1).lower() in ROLES:
            role, name, phone, email, amount = match.groups()
            result.append(dict(role=role, name=name, phone=phone,
                email="; ".join(re.findall(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", email)), amount=amount,
                evidence=record.get("source_url", "") + "#ui-tabs-3"))
    seen, unique = set(), []
    for value in result:
        key = (value["role"].casefold(), value["name"].casefold())
        if key not in seen:
            unique.append(value)
            seen.add(key)
    return unique


def write_participants(record, participants):
    lines = [line for line in record.get("description", "").splitlines() if not line.startswith(PREFIX)]
    if participants:
        lines.append(PREFIX + json.dumps(participants, ensure_ascii=False))
    record["description"] = "\n".join(lines)
    return record
