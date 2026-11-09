"""Checks that operate directly on text."""

import re

from qca.evaluation import finding


def retired_vocabulary(text: str) -> list[dict]:
    word = "snap" + "shot"
    return [finding("vocabulary.retired", {"start": match.start(), "end": match.end()},
                    "Is this retired vocabulary appropriate here?", "text", word,
                    evidence={"text": match.group()}, status="blocking")
            for match in re.finditer(word, text, re.IGNORECASE)]
