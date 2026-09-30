"""Deterministic, local memory matching. No inferred facts or cloud requests."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata


def normalized(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def inspect_matches(content, candidates, project_id=None, workspace_id=None, subject_key=None, exclude_id=None):
    value = normalized(content)
    tokens = set(value.split())
    duplicates, conflicts, revisions = [], [], []
    for item in candidates:
        if item["id"] == exclude_id or item["expired"]:
            continue
        if (item.get("project_id") or None, item.get("workspace_id") or None) != (project_id or None, workspace_id or None):
            continue
        other = normalized(item["content"])
        other_tokens = set(other.split())
        similarity = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
        if value == other or (len(tokens) >= 5 and similarity >= .85):
            duplicates.append({"id": item["id"], "match": "exact" if value == other else "similar",
                               "similarity": round(similarity, 3)})
            revisions.append(item)
        elif subject_key and normalized(item.get("subject_key") or "") == normalized(subject_key):
            conflicts.append({"id": item["id"], "subject_key": subject_key,
                              "reason": "Different values for the same explicit subject in this scope"})
            revisions.append(item)
    # A review token detects changes since preview; it does not authorize a write.
    fingerprint = json.dumps([content, project_id, workspace_id, subject_key, exclude_id, revisions],
                             sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {"duplicates": duplicates, "conflicts": conflicts, "requires_review": bool(revisions),
            "review_token": hashlib.sha256(fingerprint).hexdigest(),
            "conflict_detection": "Explicit subject keys only; no semantic contradiction inference",
            "automatic_cloud_sharing": False}


def relevance(item, query, project_id=None, workspace_id=None):
    """Return a transparent lexical score, without claiming semantic understanding."""
    terms = set(normalized(query).split())
    searchable = set(normalized(" ".join((item["content"], item.get("category", ""),
                                          item.get("subject_key", "")))).split())
    matched = sorted(terms & searchable)
    score = .55 * len(matched) / max(1, len(terms))
    reasons = ["Matched query terms: " + ", ".join(matched)] if matched else []
    if project_id and item.get("project_id") == project_id:
        score += .2
        reasons.append("Same project")
    if workspace_id and item.get("workspace_id") == workspace_id:
        score += .15
        reasons.append("Same workspace")
    importance = max(1, min(5, int(item.get("importance", 3))))
    score += .1 * importance / 5
    reasons.append(f"Explicit importance: {importance}/5")
    return round(score, 4), reasons, matched
