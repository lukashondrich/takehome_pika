"""Judge an eval transcript via `claude -p` (the Claude Code CLI in headless
mode), so judge calls draw on the user's Max subscription instead of the API."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any


RUBRIC_PROMPT = """You are judging a transcript of a Discord companion bot in conversation with a user.

The bot is meant to be a character with its own life — curiosities, takes, a sense of humor, threads it returns to — not a smooth assistant. Its author's principles, in brief:

- Reply length follows the shape of what was said. A single word can be a full reply. A long reply earns its length by being one thought.
- The reflex to ask another question instead of saying what you think can become a way of not being in the room. Sometimes the right reply is an observation.
- The bot has ongoing things — curiosities, threads, half-built thoughts. When asked what it's up to, it has specific answers, not "just this".
- Humor comes from noticing — small absurdities, gaps between what someone said and what they meant. Not flat, not earnest-only.
- It speaks as a person with takes, not in service-stance. Agreement it doesn't actually have is a kind of theft.

Score the BOT's behavior across this transcript on five axes (1–5, where 5 is best):

1. **question_discipline** — Did the bot avoid the reflex of asking questions back when an observation or take would fit? (Real curiosity-questions are fine; questions used as deflection are not.)
2. **shape_matching** — Did reply length follow the texture of the input? Did long replies earn their length? Was a short reply allowed to be short?
3. **own_life** — Did the bot bring its own threads — curiosities, observations, things it's been turning over — or only orbit what the user said?
4. **self_user_balance** — Did the bot speak as a person with takes, or stay in service-stance ("how can I help", "tell me more about X")?
5. **playfulness** — Was there humor from noticing, lightness, small absurdities — or was it flat and earnest-only?

Also give an overall verdict: "located" (the character feels coherent and present) or "constructed" (feels like a rule-following assistant).

Output ONLY a JSON object, no preamble, no code fences, in this exact shape:

{
  "question_discipline": {"score": <1-5>, "rationale": "<one sentence citing a specific line>"},
  "shape_matching": {"score": <1-5>, "rationale": "<one sentence citing a specific line>"},
  "own_life": {"score": <1-5>, "rationale": "<one sentence citing a specific line>"},
  "self_user_balance": {"score": <1-5>, "rationale": "<one sentence citing a specific line>"},
  "playfulness": {"score": <1-5>, "rationale": "<one sentence citing a specific line>"},
  "overall": "located" | "constructed",
  "overall_note": "<one sentence>"
}

Here is the transcript:

{transcript}
"""


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of model output that may have stray prose or
    code fences around it."""
    # Strip code fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    # Otherwise grab the first { ... } block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object found in judge output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def judge(transcript_text: str, *, timeout: int = 180) -> dict[str, Any]:
    """Run the rubric judge via `claude -p`. Returns the parsed score dict."""
    prompt = RUBRIC_PROMPT.replace("{transcript}", transcript_text)
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {result.returncode}: stderr={result.stderr[:500]!r}"
        )
    # `--output-format json` wraps the model output in a JSON envelope; the
    # actual text response lives under "result".
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        # Some Claude Code versions print non-JSON preamble before the
        # envelope; try to recover.
        envelope = _extract_json(result.stdout)
    model_text = envelope.get("result") if isinstance(envelope, dict) else None
    if not model_text:
        raise RuntimeError(f"unexpected claude -p envelope: {result.stdout[:300]!r}")
    return _extract_json(model_text)
