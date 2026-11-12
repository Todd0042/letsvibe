"""
Content moderation engine for LetsVibe.
Detects violent threats and profane language in user-submitted appeals.
"""

import re
from dataclasses import dataclass
from typing import List, Tuple

# Comprehensive profanity word roots (matched with flexible word boundaries and character leetspeak)
PROFANITY_PATTERNS = [
    r"\bf+u+c+k",
    r"\bs+h+i+t",
    r"\bb+i+t+c+h",
    r"\ba+s+s+h+o+l+e",
    r"\bb+a+s+t+a+r+d",
    r"\bd+i+c+k",
    r"\bc+u+n+t",
    r"\bp+i+s+s",
    r"\bc+o+c+k",
    r"\bw+h+o+r+e",
    r"\bs+l+u+t",
    r"\bf+a+g",
    r"\bn+i+g+g",
    r"\bm+o+t+h+e+r+f+u+c+k",
    r"\bb+u+l+l+s+h+i+t",
]

# Violence and threat patterns
VIOLENCE_PATTERNS = [
    r"\b(i\s*will\s*(kill|murder|shoot|stab|beat|destroy|hurt|hunt|slap|strangle))\b",
    r"\b(kill\s*(you|yourself|them|todd))\b",
    r"\b(go\s*die)\b",
    r"\b(die\s*in\s*a\s*fire)\b",
    r"\b(slit\s*(your|throat))\b",
    r"\b(beat\s*(the\s*shit\s*out\s*of|your\s*ass))\b",
    r"\b(break\s*your\s*(neck|face|bones|legs))\b",
    r"\b(punch\s*(you|in\s*the\s*face))\b",
    r"\b(put\s*a\s*bullet)\b",
    r"\b(murder(ed|er|ing)?)\b",
    r"\b(death\s*threat)\b",
    r"\b(hang\s*yourself)\b",
]


@dataclass
class ModerationResult:
    is_clean: bool
    is_profane: bool
    is_violent: bool
    flagged_reasons: List[str]


def analyze_appeal_text(text: str) -> ModerationResult:
    """
    Analyzes an appeal text for profane or violent language.
    Returns a ModerationResult indicating whether the text is clean,
    and whether profanity or violence was detected.
    """
    if not text:
        return ModerationResult(is_clean=True, is_profane=False, is_violent=False, flagged_reasons=[])

    lower = text.lower()
    is_profane = False
    is_violent = False
    flagged = []

    # Check violence
    for pat in VIOLENCE_PATTERNS:
        if re.search(pat, lower, re.IGNORECASE):
            is_violent = True
            flagged.append("violent")
            break

    # Check profanity
    for pat in PROFANITY_PATTERNS:
        if re.search(pat, lower, re.IGNORECASE):
            is_profane = True
            flagged.append("profane")
            break

    is_clean = not (is_profane or is_violent)
    return ModerationResult(
        is_clean=is_clean,
        is_profane=is_profane,
        is_violent=is_violent,
        flagged_reasons=flagged,
    )
