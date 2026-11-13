"""README digest: what the AI reviewers read from a README (analyzers/readme_digest.py)."""
from letsvibe.analyzers.readme_digest import BUDGET_CHARS, build_readme_digest, clean

BADGE_HEAVY = (
    '<p align="center"><a href="https://ko-fi.com/x"><img src="https://x/banner.png" alt="Banner" /></a></p>\n\n---\n\n'
    "# My Karaoke App\n\n"
    "[![Version](https://img.shields.io/badge/v-1.0-blue.svg)](https://x)\n"
    "[![Rubric](https://img.shields.io/badge/Rubric-v7%20(bell%20curve)-blueviolet.svg)](https://x)\n\n"
    "> Turn any TV into a karaoke lounge.\n\n"
    "## Table of Contents\n* [What it is](#what-it-is)\n  * [1. Setup ([setup.py](setup.py))](#1-setup)\n\n"
    "## What it is\nA karaoke app for parties. See the [docs](https://example.com/docs).\n\n"
    "## Features\n- Fair queue\n- Pitch shift\n\n"
    "## Installation\n```bash\npip install karaoke\nkaraoke start\n```\n\n"
    "## License\nMIT, with a long licence text that should never reach the reviewers.\n\n"
    "## Support\nBuy me a coffee.\n"
)


def test_decoration_is_removed_and_links_keep_their_text():
    text = clean(BADGE_HEAVY)
    for junk in ("<img", "shields.io", "svg)", "(#what-it-is)", "ko-fi.com", "---"):
        assert junk not in text
    assert "See the docs." in text


def test_digest_puts_what_it_is_features_and_setup_first_and_drops_licence_and_support():
    d = build_readme_digest(BADGE_HEAVY)
    assert d.startswith("# My Karaoke App")
    assert "README outline" in d and "Not shown: Table of Contents, License, Support" in d
    for wanted in ("Turn any TV into a karaoke lounge.", "A karaoke app for parties.", "Fair queue", "pip install karaoke"):
        assert wanted in d
    assert "long licence text" not in d and "Buy me a coffee" not in d
    assert d.index("A karaoke app for parties.") < d.index("Fair queue") < d.index("pip install karaoke")


def test_long_readmes_stay_within_budget_and_plain_ones_survive():
    long = "# Big\n\nIntro sentence.\n\n" + "\n\n".join(f"## Feature {i}\n" + "Detail. " * 80 for i in range(40))
    assert len(build_readme_digest(long)) <= BUDGET_CHARS
    assert "Might pick this up" in build_readme_digest("Might pick this up someday.")
    assert "none" in build_readme_digest("Just a sentence, no headings.")
