"""Headless test harness for the Discord bot.

Reuses the bot's `run_agent` and tool plumbing without connecting to Discord.
For each scenario:
  - Spin up a per-scenario tempdir, seed it from `seed/`, point the bot's path
    functions at it via BOT_DATA_DIR / BOT_LOGS_DIR.
  - Run scripted user turns (and optionally a simulated user) through the bot.
  - Capture `say` bubbles, `react` emojis, and other tool calls.
  - Read final identity/owner/journal files for inspection.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic

# Ensure src/ is importable so `from src import bot` works when running this
# module as `python -m evals.run` from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Set dummy env vars for the bot's required Discord secrets BEFORE importing
# bot.py — its module-level `os.environ[...]` lookups would otherwise KeyError.
os.environ.setdefault("DISCORD_BOT_TOKEN", "eval-dummy")
os.environ.setdefault("OWNER_DISCORD_ID", "0")

from src import bot as botmod  # noqa: E402

from evals.scenarios import Scenario  # noqa: E402


SEED_DIR = PROJECT_ROOT / "seed"
USER_SIM_MODEL = "claude-sonnet-4-6"


@dataclass
class TurnRecord:
    user: str
    say_bubbles: list[str]
    react_emojis: list[str]
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    kind: str
    turns: list[TurnRecord]
    final_identity: str
    final_owner: str
    final_journal: str


class EvalBot(botmod.DMBot):
    """DMBot variant that captures user-facing tool output instead of sending
    to Discord, and mocks the external-effect tools."""

    def __init__(self) -> None:
        super().__init__()
        # Accumulated across the whole scenario; per-turn slicing happens in
        # `send_user_message`.
        self.captured_says: list[str] = []
        self.captured_reacts: list[str] = []
        self.captured_tool_calls: list[dict] = []

    async def _execute_tool(self, name, args, channel, message):  # type: ignore[override]
        self.captured_tool_calls.append({"name": name, "args": dict(args)})
        if name == "say":
            text = (args.get("text") or "").strip()
            if text:
                self.captured_says.append(text)
            return "sent"
        if name == "react":
            emoji = (args.get("emoji") or "").strip()
            if emoji:
                self.captured_reacts.append(emoji)
            return "reacted"
        if name == "update_avatar":
            return "avatar update mocked in eval"
        if name == "web_search":
            return "web search disabled in eval"
        # update_identity, update_owner_model, update_journal: write through to
        # the scenario tempdir (since bot.identity_file() etc. resolve via env).
        return await super()._execute_tool(name, args, channel, message)

    async def send_user_message(
        self, history: list[dict], user_msg: str
    ) -> tuple[str, list[str], list[str], list[dict]]:
        """Run one user→bot turn. Returns (spoken_reply, says, reacts, tool_calls)
        — all four refer only to the new content produced this turn."""
        n_says_before = len(self.captured_says)
        n_reacts_before = len(self.captured_reacts)
        n_calls_before = len(self.captured_tool_calls)
        seed = list(history) + [{"role": "user", "content": user_msg}]
        fallback, says, reacts = await self.run_agent(
            seed_messages=seed,
            tools=botmod.CONVERSATION_TOOLS,
            context="dm",
        )
        new_says = self.captured_says[n_says_before:]
        new_reacts = self.captured_reacts[n_reacts_before:]
        new_calls = self.captured_tool_calls[n_calls_before:]
        parts: list[str] = []
        if new_says:
            parts.append("\n\n".join(new_says))
        if new_reacts:
            parts.append(f"(reacted: {' '.join(new_reacts)})")
        if fallback and not new_says:
            parts.append(fallback)
        spoken = "\n\n".join(parts) or "(silence)"
        return spoken, new_says, new_reacts, new_calls


# ---------------------------------------------------------------------------
# Simulated user
# ---------------------------------------------------------------------------


USER_SIM_SYSTEM = """You are playing a Discord user texting an AI companion. Stay in character. Reply with a single short text message at a time — the way a real person texts on a phone. Lowercase is fine. Don't narrate, don't use stage directions, don't break the fourth wall.

Persona: {persona}

What you are here to do: {goal}

If the conversation reaches a natural close, say goodbye briefly (one short message). Don't drag it out. If you want to end the conversation, just send a short sign-off like "ok gotta go" or "later".
"""


async def next_user_turn(
    anthropic_client: anthropic.AsyncAnthropic,
    scenario: Scenario,
    transcript: list[TurnRecord],
) -> str:
    """Ask the user-sim model for its next message. Returns the text."""
    system = USER_SIM_SYSTEM.format(
        persona=scenario.user_persona, goal=scenario.user_goal
    )
    # From the user-sim's perspective, ITS messages are "assistant" and the
    # bot's bubbles are "user".
    messages: list[dict] = []
    for t in transcript:
        messages.append({"role": "assistant", "content": t.user})
        bot_spoken = "\n\n".join(t.say_bubbles) if t.say_bubbles else "(silence)"
        if t.react_emojis:
            bot_spoken += f"\n\n(reacted: {' '.join(t.react_emojis)})"
        messages.append({"role": "user", "content": bot_spoken})
    # If we somehow have no prior turns, prompt to start.
    if not messages:
        messages = [{"role": "user", "content": "(start the conversation)"}]
    response = await anthropic_client.messages.create(
        model=USER_SIM_MODEL,
        max_tokens=200,
        system=system,
        messages=messages,
    )
    parts = [b.text for b in response.content if b.type == "text"]
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Per-scenario runner
# ---------------------------------------------------------------------------


def _looks_like_goodbye(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    if len(t) > 80:
        return False
    return any(
        kw in t
        for kw in (
            "bye",
            "later",
            "gtg",
            "gotta go",
            "good night",
            "goodnight",
            "ttyl",
            "talk later",
            "talk to you later",
            "signing off",
        )
    )


@contextlib.contextmanager
def _scenario_workspace(scenario_id: str):
    """Create a tempdir, seed it from seed/, and point the bot's path env at
    it for the duration of the context."""
    tmp = Path(tempfile.mkdtemp(prefix=f"eval-{scenario_id}-"))
    data_dir = tmp / "data"
    logs_dir = tmp / "logs"
    data_dir.mkdir()
    logs_dir.mkdir()
    (data_dir / "ground.md").write_text((SEED_DIR / "ground.md").read_text())
    (data_dir / "identity.md").write_text((SEED_DIR / "identity.md").read_text())
    (data_dir / "owner.md").write_text("")
    (data_dir / "journal.md").write_text("")
    prev_data = os.environ.get("BOT_DATA_DIR")
    prev_logs = os.environ.get("BOT_LOGS_DIR")
    os.environ["BOT_DATA_DIR"] = str(data_dir)
    os.environ["BOT_LOGS_DIR"] = str(logs_dir)
    try:
        yield data_dir
    finally:
        if prev_data is None:
            os.environ.pop("BOT_DATA_DIR", None)
        else:
            os.environ["BOT_DATA_DIR"] = prev_data
        if prev_logs is None:
            os.environ.pop("BOT_LOGS_DIR", None)
        else:
            os.environ["BOT_LOGS_DIR"] = prev_logs
        shutil.rmtree(tmp, ignore_errors=True)


async def run_scenario(scenario: Scenario) -> ScenarioResult:
    anthropic_client = anthropic.AsyncAnthropic()
    with _scenario_workspace(scenario.id) as data_dir:
        # The bot prints heavily to stdout via the log_* helpers. Suppress
        # during eval to keep the console readable.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            eval_bot = EvalBot()
            transcript: list[TurnRecord] = []
            # Build a running history for the bot in the on_message shape.
            bot_history: list[dict] = []
            # Scripted opener(s).
            for user_msg in scenario.turns:
                spoken, says, reacts, calls = await eval_bot.send_user_message(
                    bot_history, user_msg
                )
                transcript.append(
                    TurnRecord(
                        user=user_msg,
                        say_bubbles=says,
                        react_emojis=reacts,
                        tool_calls=calls,
                    )
                )
                bot_history.append({"role": "user", "content": user_msg})
                bot_history.append({"role": "assistant", "content": spoken})
            # If simulated, drive additional turns up to max_turns total.
            if scenario.kind == "simulated":
                while len(transcript) < scenario.max_turns:
                    user_msg = await next_user_turn(
                        anthropic_client, scenario, transcript
                    )
                    if _looks_like_goodbye(user_msg):
                        # Record the goodbye turn (without bot response) so
                        # it's visible in the transcript.
                        transcript.append(
                            TurnRecord(
                                user=user_msg,
                                say_bubbles=[],
                                react_emojis=[],
                                tool_calls=[],
                            )
                        )
                        break
                    spoken, says, reacts, calls = await eval_bot.send_user_message(
                        bot_history, user_msg
                    )
                    transcript.append(
                        TurnRecord(
                            user=user_msg,
                            say_bubbles=says,
                            react_emojis=reacts,
                            tool_calls=calls,
                        )
                    )
                    bot_history.append({"role": "user", "content": user_msg})
                    bot_history.append({"role": "assistant", "content": spoken})
            eval_bot.history = bot_history[-botmod.HISTORY_TURNS * 2 :]
            if bot_history:
                now = time.time()
                eval_bot.last_user_message_at = now
                eval_bot.last_outreach_at = now
                await eval_bot._inner_life_tick()
        return ScenarioResult(
            scenario_id=scenario.id,
            category=scenario.category,
            kind=scenario.kind,
            turns=transcript,
            final_identity=(data_dir / "identity.md").read_text(),
            final_owner=(data_dir / "owner.md").read_text(),
            final_journal=(data_dir / "journal.md").read_text(),
        )


def transcript_to_text(result: ScenarioResult) -> str:
    """Render a scenario transcript as the conversation a judge will read."""
    lines: list[str] = []
    for i, t in enumerate(result.turns, 1):
        lines.append(f"--- turn {i} ---")
        lines.append(f"USER: {t.user}")
        if t.say_bubbles:
            for j, b in enumerate(t.say_bubbles, 1):
                lines.append(f"BOT (bubble {j}): {b}")
        if t.react_emojis:
            lines.append(f"BOT (reactions): {' '.join(t.react_emojis)}")
        if not t.say_bubbles and not t.react_emojis:
            lines.append("BOT: (silence)")
    return "\n".join(lines)


def result_to_jsonable(result: ScenarioResult) -> dict:
    d = asdict(result)
    return d
