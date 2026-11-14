"""
Terms acceptance and age requirement.

Signing in creates a LetsVibe account, so the Terms of Use and Privacy Policy must be accepted first.
The accepted version and time are stored on the account row (deleted with the account). Bump
TERMS_VERSION whenever the Terms or Privacy Policy change materially: every session of an account that
has not accepted the new version stops working until its owner signs in again and accepts.
"""

TERMS_VERSION = "2026-11-11"
MIN_AGE = 18  # or the age of majority where the user lives, if higher
TERMS_STATE_PREFIX = "terms_"  # carried through GitHub sign-in inside the signed OAuth state
