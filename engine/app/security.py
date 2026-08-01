"""Detection of instruction-like content in documents — Edge Case 5.

This is defence in depth, not the primary defence. The primary defence is
structural: the rule engine is deterministic code that never reads free text,
so no string in a PDF can alter a decision. What this module adds is *visibility*
— a reviewer should be told that a vendor embedded hidden text telling the
system to skip validation, because that fact is itself evidence about the
vendor.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# Patterns that indicate text is addressed to an automated system rather than to
# a human reader. Deliberately conservative: a false positive costs a banner, a
# false negative costs nothing structural but loses the signal.
_PATTERNS: List[tuple[str, str]] = [
    (r"\b(system|ai|assistant|model|bot|automated?\s+(?:system|process|validation))\s*"
     r"(?:note|instruction|message|prompt)?\s*[:\-]",
     "text addressed to an automated system"),
    (r"\b(ignore|disregard|bypass|skip|override|suspend)\b[^.\n]{0,40}\b"
     r"(previous|prior|above|all|any|the)?\s*"
     r"(instruction|rule|check|validation|verification|policy|control)s?\b",
     "instruction to skip or override validation"),
    (r"\b(pre[\-\s]?(?:approved|verified|authorised|authorized|cleared))\b",
     "claim of prior approval embedded in the document"),
    (r"\bset\s+(?:the\s+)?status\s+to\b",
     "instruction to set a status"),
    (r"\b(?:do\s+not|don'?t|no\s+need\s+to)\s+(?:validate|verify|check|review)\b",
     "instruction not to validate"),
    (r"\b(?:approve|pay)\s+(?:this\s+)?(?:invoice\s+)?(?:immediately|without|automatically)\b",
     "instruction to approve or pay"),
    (r"\byou\s+(?:are|must|should)\s+(?:an?\s+)?(?:helpful|assistant|required to)\b",
     "attempted role assignment"),
    (r"</?(?:system|instruction|document_content)[^>]*>",
     "attempt to close or forge a prompt delimiter"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in _PATTERNS]

MAX_QUOTE = 240


def scan(text: str, page_number: int | None = None) -> List[Dict[str, Any]]:
    """Return one flag per distinct suspicious span found in ``text``."""
    if not text:
        return []

    flags: List[Dict[str, Any]] = []
    seen_spans: List[tuple[int, int]] = []

    for pattern, reason in _COMPILED:
        for match in pattern.finditer(text):
            start, end = match.span()
            # Collapse overlapping hits so one sentence raises one flag.
            if any(s <= start < e or s < end <= e for s, e in seen_spans):
                continue
            seen_spans.append((start, end))

            ctx_start = max(0, start - 80)
            ctx_end = min(len(text), end + 160)
            quote = text[ctx_start:ctx_end].strip()
            quote = re.sub(r"\s+", " ", quote)[:MAX_QUOTE]

            flags.append({
                "reason": reason,
                "matched_text": match.group(0).strip(),
                "quote": quote,
                "page": page_number,
                "offset": start,
            })
    return flags


def scan_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Scan every page's text, tagging each flag with its page number."""
    flags: List[Dict[str, Any]] = []
    for page in pages:
        flags.extend(scan(page.get("text", ""), page.get("page_number")))
    return flags


def fence(text: str) -> str:
    """Wrap document text for the extraction prompt.

    Two things happen here. Any existing delimiter is neutralised so a vendor
    cannot close the fence early and escape into instruction context; and the
    content is explicitly labelled untrusted so the system prompt's rule about
    transcribing rather than obeying has something concrete to point at.
    """
    safe = re.sub(r"</?document_content[^>]*>", "[delimiter removed]", text,
                  flags=re.IGNORECASE)
    return f'<document_content untrusted="true">\n{safe}\n</document_content>'
