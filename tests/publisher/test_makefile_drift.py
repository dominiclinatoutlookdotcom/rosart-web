"""The check job runs what ``make check`` runs (spec 006 T055; research R-6, FR-005).

Seam: ``publisher.steps.CHECK_STEPS`` and ``CHECK_ENV`` against the Makefile's ``check:`` recipe and its
``export`` variables, parsed here from the real Makefile. A step added to the recipe without the service
knowing fails this test.
"""
import re
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parent.parent.parent / "Makefile"


def parse_check_recipe(text: str) -> list[tuple[str, ...]]:
    """The commands of the ``check:`` recipe, ``$(NPM)`` read as ``npm``."""
    steps, inside = [], False
    for line in text.splitlines():
        if re.match(r"^check\s*:", line):
            inside = True
            continue
        if not inside:
            continue
        if not line.startswith("\t"):
            break
        command = line.strip().lstrip("@-").replace("$(NPM)", "npm").replace("$(PYTHON)", "python")
        steps.append(tuple(command.split()))
    return steps


def parse_exports(text: str) -> dict[str, str]:
    """``export NAME := value`` lines (variables every recipe child inherits)."""
    return {m[1]: m[2].strip() for m in re.finditer(r"^export\s+([A-Z_][A-Z0-9_]*)\s*:?=\s*(.*)$", text, re.M)}


def test_the_check_steps_are_exactly_the_check_recipe():
    from publisher.steps import CHECK_STEPS

    recipe = parse_check_recipe(MAKEFILE.read_text(encoding="utf-8"))

    assert recipe == [("npm", "run", "build"), ("npm", "test"), ("npm", "run", "typecheck"),
                      ("npm", "run", "check:dist")], "the parser should read the four commands"
    assert [tuple(step) for step in CHECK_STEPS] == recipe


def test_the_check_environment_carries_the_makefile_export_variables():
    from publisher.steps import CHECK_ENV

    assert CHECK_ENV == parse_exports(MAKEFILE.read_text(encoding="utf-8"))
    assert CHECK_ENV["ASTRO_TELEMETRY_DISABLED"] == "1"


def test_a_line_added_to_the_recipe_would_fail_the_comparison():
    from publisher.steps import CHECK_STEPS

    text = MAKEFILE.read_text(encoding="utf-8").replace(
        "\t$(NPM) run check:dist\n", "\t$(NPM) run check:dist\n\t$(NPM) run lint\n", 1)

    assert [tuple(step) for step in CHECK_STEPS] != parse_check_recipe(text)


def test_an_export_variable_added_to_the_makefile_would_fail_the_comparison():
    from publisher.steps import CHECK_ENV

    text = MAKEFILE.read_text(encoding="utf-8") + "\nexport NODE_OPTIONS := --max-old-space-size=1\n"

    assert CHECK_ENV != parse_exports(text)
