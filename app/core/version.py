"""Which build produced a job.

Prompt and pipeline changes only take effect on deploy, and a summary carries
no sign of which build wrote it. That cost real time: a file was re-run twice to
check whether two merged fixes had worked, when neither was deployed yet, and
nothing in the output could have said so.

Every job is stamped, so "is my fix live?" is answered by looking at a job
instead of guessing from its content.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache

# Bumped when the pipeline changes in a way that alters output. Deliberately
# separate from the git SHA: the SHA says which commit, this says whether a
# reviewer should expect different results.
PIPELINE_VERSION = "2.1"


@lru_cache(maxsize=1)
def build_revision() -> str:
    """Short identifier of the running build.

    Prefers an explicit build-time stamp, since a deployed container usually
    carries no git metadata. Falls back to the working tree's HEAD in
    development, and to "unknown" rather than failing - a missing version must
    never stop a job from running.
    """
    for name in ("GIT_SHA", "SOURCE_COMMIT", "RAILWAY_GIT_COMMIT_SHA", "VERCEL_GIT_COMMIT_SHA"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value[:12]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        revision = result.stdout.strip()
        if revision:
            return revision
    except Exception:
        pass
    return "unknown"


def pipeline_build() -> str:
    """The stamp written onto a job, e.g. "2.1+ab373ec8f012"."""
    return f"{PIPELINE_VERSION}+{build_revision()}"
