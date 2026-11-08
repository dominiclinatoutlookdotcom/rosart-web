"""The service package `publisher/` must not be shadowed by this test folder."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_publisher_resolves_to_the_service_package_not_the_test_folder():
    import publisher
    import publisher.aws

    assert Path(publisher.__file__).resolve() == REPO_ROOT / "publisher" / "__init__.py"
    assert Path(publisher.aws.__file__).resolve() == REPO_ROOT / "publisher" / "aws.py"
