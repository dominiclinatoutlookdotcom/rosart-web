"""Tests of the publisher service, and its fakes.

The service package `publisher/` (repository root) and this folder share a name, and pytest imports
this one first. Extending the search path makes `publisher.aws` resolve to `publisher/aws.py` and
`publisher.fake_aws` to the fake here, in one test session.
"""
from pathlib import Path

__path__.append(str(Path(__file__).resolve().parent.parent.parent / "publisher"))
