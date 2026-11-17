# Security Policy

LetsVibe holds very little personal data (public GitHub usernames, IDs and avatars; never email
addresses), but every report is taken seriously.

## Reporting a vulnerability

**Please report privately — don't open a public issue.**

* Preferred: GitHub's **[private vulnerability reporting](https://github.com/Todd0042/letsvibe/security/advisories/new)** for this repository.
* Or reach out privately via the in-app **Chat / Support** widget with "LetsVibe security" in the message.

Please include what you found, how to reproduce it, and what an attacker could do with it. You'll get a
reply as soon as possible (this is a one-person project), and credit in the fix if you'd like it.

## Scope

In scope: this repository's code and the live site at <https://letsvibe.todd0042.dev>, for example
sign-in and session handling, ownership checks (only owners may submit, re-grade or delete their
projects), the admin console, data export and deletion, and anything that could expose or alter
another user's data.

Out of scope: denial-of-service or load testing against the live site, social engineering, and
findings in third-party services (GitHub, Cloudflare, Ollama). Please don't access or change data
that isn't yours while testing; use your own account and repositories.

## Supported versions

Only the latest version on the `main` branch (which is what the live site runs) receives fixes.
