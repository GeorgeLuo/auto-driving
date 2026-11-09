"""Adapters for external engines that accept a text body."""

import json
import re
import subprocess
import sys

from qca.evaluation import finding
from qca.text_evaluators import offset


def retired_vocabulary(text: str) -> list[dict]:
    word = "snap" + "shot"
    return [finding("vocabulary.retired", {"start": match.start(), "end": match.end()},
                    "Is this retired vocabulary appropriate here?", "text", word,
                    evidence={"text": match.group()}, status="blocking")
            for match in re.finditer(word, text, re.IGNORECASE)]


def ruff_python(text: str) -> list[dict]:
    """Run Ruff on supplied Python text without reading a project or file."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--isolated", "--select", "F,E722,B",
         "--output-format", "json", "--stdin-filename", "input.py", "-"],
        input=text, capture_output=True, text=True, check=False,
    )
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr or "Ruff could not evaluate the text")
    records = []
    for item in json.loads(result.stdout):
        rule = item["code"] or "invalid-syntax"
        start, end = item["location"], item["end_location"]
        location = {"start": offset(text, start["row"], start["column"] - 1),
                    "end": offset(text, end["row"], end["column"] - 1)}
        records.append(finding("ruff." + rule, location, "Does this indicate a problem: " + item["message"] + "?",
                               "ruff", rule, evidence={"message": item["message"]}))
    return records
