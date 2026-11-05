"""`ghti` with no command: the first thing a person types after cloning.

It used to be an argparse error ("the following arguments are required: command")
that said nothing about where to start, and nothing about `demo` being the only
command that works on a bare clone. These tests keep the start page honest: the
commands it names must exist, and the README anchors it links must exist too.
"""

from __future__ import annotations

import re
from argparse import _SubParsersAction
from pathlib import Path

from ghti.cli import ORDER, build_parser, main, start_here
from ghti.github import ERRORS_ANCHOR, SETUP_ANCHOR

README = Path(__file__).resolve().parent.parent / "README.md"


def test_bare_ghti_says_where_to_start(capsys):
    """No command: exit code 2, as before, but now with the way in."""
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "1. ghti demo" in out
    assert "Needs nothing: no Google account, no key, no network." in out
    assert "Steps 2 to 4 need the Google Cloud setup" in out
    assert SETUP_ANCHOR in out  # the setup is one click away
    assert ERRORS_ANCHOR in out  # and so is the free list of errors
    assert "ghti --help" in out


def test_the_start_page_only_names_commands_that_exist():
    """A renamed command must not leave the start page pointing at nothing."""
    sub = next(a for a in build_parser()._actions if isinstance(a, _SubParsersAction))
    assert set(ORDER) <= set(sub.choices)
    # only the indented list, so the first line ("ghti copies ...") stays out
    named = set(re.findall(r"^ {2,}(?:\d\. )?ghti ([a-z][a-z-]+)", start_here(), re.MULTILINE))
    assert named == set(ORDER)
    assert named <= set(sub.choices)


def test_demo_comes_first_because_it_is_the_only_one_that_needs_nothing():
    page = start_here()
    assert page.index("ghti demo") < page.index("ghti check") < page.index("ghti sync")
    assert ORDER[0] == "demo"


def test_help_sends_the_reader_to_the_same_order(capsys):
    assert "start here: ghti demo (needs nothing)" in build_parser().format_help()


def _slugs(markdown: str) -> set[str]:
    """The ids GitHub gives to headings: lower case, punctuation dropped, spaces to hyphens."""
    out = set()
    for line in markdown.splitlines():
        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if match:
            title = match.group(1).strip().lower()
            title = re.sub(r"[^\w\s-]", "", title)
            out.add(re.sub(r"\s+", "-", title))
    return out


def test_the_anchors_the_code_links_exist_in_the_readme():
    """Every pointer we print (start page and error footer) must land on a heading.

    Only README.md: the links in the code all point at the English page.
    """
    slugs = _slugs(README.read_text(encoding="utf-8"))
    for anchor in (SETUP_ANCHOR, ERRORS_ANCHOR):
        assert anchor.lstrip("#") in slugs, f"{anchor} has no heading in README.md"
