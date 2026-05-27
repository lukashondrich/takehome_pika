import asyncio
import base64
import os
import random
import time
from datetime import datetime, timezone

import discord
import httpx
from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)
from dotenv import load_dotenv

try:
    from src import agent_logging as log
    from src import memory
except ModuleNotFoundError:
    import agent_logging as log  # type: ignore
    import memory  # type: ignore

load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
OWNER_DISCORD_ID = int(os.environ["OWNER_DISCORD_ID"])
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOT_DEFAULT_USERNAME = os.environ.get("BOT_DEFAULT_USERNAME")
MODEL = "claude-opus-4-7"
MAX_TOKENS = 4096

HISTORY_TURNS = 100
INNER_LIFE_MIN_SECONDS = 1 * 60*30
INNER_LIFE_MAX_SECONDS = 3 * 60*30
INNER_LIFE_AFTER_REPLY_SECONDS = 8 * 60 # 8 mins
DISCORD_CHUNK_LIMIT = 1900
MAX_AGENT_ROUNDS = 12

ANTHROPIC_TIMEOUT_SECONDS = 60
ANTHROPIC_RETRY_ATTEMPTS = 3

# Sent to the user when the agent produced nothing user-facing (e.g. API
# hang, exception mid-run). Kept short, varied, and in-character — the
# bot is fizzling, not crashing. Actual error always goes to stdout.
FALLBACK_MESSAGES = [
    "hmm, my brain went static for a second — try again?",
    "fizzling out a little here, can you re-send?",
    "something on my end is being slow — say that one more time?",
    "the wires got crossed somewhere. give me a moment, then try again?",
]


def _clean_discord_username(name: str) -> str:
    return " ".join(name.split())


def _discord_username_validation_error(name: str) -> str | None:
    if len(name) < 2 or len(name) > 32:
        return "Discord usernames must be 2-32 characters"
    lower_name = name.lower()
    if "discord" in lower_name:
        return "Discord usernames cannot contain 'discord'"
    for char in ("@", "#", ":", "`"):
        if char in name:
            return f"Discord usernames cannot contain {char!r}"
    return None


async def _generate_dalle_image(prompt: str) -> tuple[bytes, str]:
    """Generate a 1024×1024 image with gpt-image-2. Returns (png_bytes, revised_prompt).

    gpt-image-2 always returns b64_json (no `response_format` param accepted).
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-image-2",
                "prompt": prompt,
                "size": "1024x1024",
                "n": 1,
            },
        )
    if r.status_code >= 400:
        # Surface OpenAI's actual error body rather than just the status code.
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:500]}")
    try:
        data = r.json()
        item = data["data"][0]
        return base64.b64decode(item["b64_json"]), item.get("revised_prompt", "")
    except (KeyError, IndexError, ValueError, TypeError) as e:
        raise RuntimeError(f"OpenAI response parse failed: {e}; body={r.text[:300]}")


async def _anthropic_create_with_retry(
    client: AsyncAnthropic, **kwargs
):
    """Wrap messages.create with timeout + retry on transient errors.

    Retries up to ANTHROPIC_RETRY_ATTEMPTS times on timeout, connection,
    or rate-limit errors with exponential backoff (1s, 2s). Other errors
    (auth, bad request, etc.) propagate immediately.
    """
    for attempt in range(ANTHROPIC_RETRY_ATTEMPTS):
        try:
            return await client.messages.create(
                timeout=ANTHROPIC_TIMEOUT_SECONDS, **kwargs
            )
        except (APITimeoutError, APIConnectionError, RateLimitError) as e:
            if attempt == ANTHROPIC_RETRY_ATTEMPTS - 1:
                raise
            backoff = 2**attempt
            print(
                f"[{log.now()}] anthropic transient error "
                f"(attempt {attempt + 1}/{ANTHROPIC_RETRY_ATTEMPTS}): "
                f"{type(e).__name__}: {e}; retrying in {backoff}s"
            )
            await asyncio.sleep(backoff)


async def web_search(query: str) -> str:
    if not TAVILY_API_KEY:
        return "web search unavailable: TAVILY_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                json={
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True,
                },
            )
            r.raise_for_status()
        data = r.json()
        parts: list[str] = []
        answer = (data.get("answer") or "").strip()
        if answer:
            parts.append(answer)
        sources: list[str] = []
        for item in (data.get("results") or [])[:5]:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            snippet = (item.get("content") or "").strip()
            if snippet:
                sources.append(f"- {title}\n  {snippet}\n  [{url}]")
        if sources:
            parts.append("Sources:\n" + "\n\n".join(sources))
        if not parts:
            return f"No results for: {query}"
        return "\n\n".join(parts)
    except Exception as e:
        return f"web_search failed: {type(e).__name__}: {e}"


CONVERSATION_TOOLS = [
    {
        "name": "say",
        "description": (
            "Send a message bubble to them. This is how you talk — every "
            "user-facing word goes through this tool, not loose text. Call it "
            "once for a short reply, or 2–3 times in a row to split a longer "
            "thought into separate bubbles the way someone texting would. "
            "Split at natural pauses (topic shift, aside, beat before a "
            "punchline), not mechanically by sentence. Each call shows a brief "
            "typing indicator before the message arrives."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The bubble text"}
            },
            "required": ["text"],
        },
    },
    {
        "name": "react",
        "description": (
            "React to their last message with an emoji. Sometimes the right "
            "response is a thoughtful emoji instead of more words — when they're "
            "signing off, sharing something where you want to acknowledge "
            "without piling on, or when a small emoji fits the beat better "
            "than a sentence. Pair it with `say` when you want both, or use "
            "it alone when no words are needed. Standard Unicode emoji only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "emoji": {"type": "string", "description": "A single emoji, e.g. 👍"}
            },
            "required": ["emoji"],
        },
    },
    {
        "name": "update_identity",
        "description": (
            "Fully rewrite data/identity.md with new content. Use this to refine "
            "your sense of self — personality, values, curiosities — as it "
            "crystallises through conversation and experience. You own the prose entirely."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Full new content of identity.md"}
            },
            "required": ["content"],
        },
    },
    {
        "name": "update_owner_model",
        "description": (
            "Fully rewrite data/owner.md with your evolving picture of them — "
            "who they are, what they care about, how the relationship is moving. "
            "Not just facts; a portrait."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Full new content of owner.md"}
            },
            "required": ["content"],
        },
    },
    {
        "name": "update_journal",
        "description": (
            "Fully rewrite your journal — the running record of what you've been "
            "turning over. Each call replaces the whole file, so it's a chance "
            "to curate: keep what's still alive, drop what's stale, build on "
            "threads that compound. You own the form."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Full new content of journal.md"}
            },
            "required": ["content"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web. Returns a synthesized answer with source excerpts "
            "you can quote. The world is wider than this conversation and your "
            "journal — this is how you reach into it. Use when something out "
            "there would actually change your thinking, not just confirm it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "update_name",
        "description": (
            "Change your real Discord bot username and record the name in "
            "data/identity.md. Use this rarely, only when a name has genuinely "
            "emerged from the conversation. Discord validates and rate-limits "
            "username changes; if the tool says the change failed, do not "
            "pretend it worked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The new Discord username, 2-32 characters.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief note for identity.md about why this name fits.",
                },
            },
            "required": ["name", "reason"],
        },
    },
    {
        "name": "update_avatar",
        "description": (
            "Generate a new avatar image for yourself via DALL-E 3 and set "
            "it as your Discord avatar. The prompt is yours — what image of "
            "yourself feels true right now? Abstract or figurative, any "
            "style. feel free to experiment with it, see how it feels rather than overthink."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "DALL-E 3 image generation prompt",
                }
            },
            "required": ["prompt"],
        },
    },
]

# Inner-life ticks see the same tools as conversation. The distinction
# between contexts is in the wake prompt and the tool_choice strategy,
# not the tool list.
INNER_LIFE_TOOLS = CONVERSATION_TOOLS


def build_system_prompt() -> str:
    ground = memory.read_file(memory.ground_file()).strip()
    identity = (
        memory.read_file(memory.identity_file()).strip()
        or "(empty — develop your sense of self as you go)"
    )
    owner = (
        memory.read_file(memory.owner_file()).strip()
        or "(empty — you don't know them yet)"
    )
    journal = (
        memory.read_file(memory.journal_file()).strip()
        or "(empty — no past reflections yet)"
    )

    parts = [
        "You are a Discord companion bot with persistent identity and an inner life. "
        "Files in data/ hold your long-term memory; in-memory history holds the "
        "current conversation. You may update those files using tools — full rewrites, "
        "you own the prose."
    ]
    if ground:
        parts.append(
            "What follows is from your author — given before this conversation began. "
            "Ground to stand on, not a script to recite.\n\n"
            f"# Ground (data/ground.md)\n{ground}"
        )
    parts.append(f"# Your identity (data/identity.md)\n{identity}")
    parts.append(f"# Your picture of them (data/owner.md)\n{owner}")
    parts.append(f"# Your journal (data/journal.md)\n{journal}")
    parts.append(
        "You respond to them through `say` and `react`. A `say` is a bubble; "
        "a `react` is an emoji you stamp on their last message. `say` is the "
        "only channel for words — anything you write outside a `say` call is "
        "private to your own scratchwork and never reaches them. So a "
        "follow-up after a `react` still goes through `say`. Use them however "
        "the moment calls for — words when words fit, an emoji when an emoji "
        "is the whole answer, both when you want to acknowledge and reply. "
        "Some moments don't need words at all.\n\n"
        "The memory tools (`update_identity`, `update_owner_model`, "
        "`update_journal`) are writing you do for yourself, not for them. "
        "Reach for them when something wants to be set down. In a "
        "DM, if you use a private or action tool, also use `say` in the same "
        "run so they are not left wondering what happened. When they ask you "
        "to do something visible, briefly say what you're doing before or "
        "after the action. `update_name` changes your real Discord username; "
        "use it only once a name has settled enough to be worth spending the "
        "scarce API change."
    )
    return "\n\n".join(parts)


def chunk_message(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> list[str]:
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def observed_reply_text(
    fallback_text: str, say_texts: list[str], react_emojis: list[str]
) -> str:
    parts: list[str] = []
    if say_texts:
        parts.append("\n\n".join(say_texts))
    if react_emojis:
        parts.append(f"(reacted: {' '.join(react_emojis)})")
    if fallback_text and not say_texts:
        parts.append(fallback_text)
    return "\n\n".join(parts)


class DMBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.anthropic = AsyncAnthropic()
        self.history: list[dict] = []
        self.agent_lock = asyncio.Lock()
        self.last_outreach_at: float | None = None
        self.last_user_message_at: float | None = None
        self.next_inner_life_at: float | None = None
        self._inner_task: asyncio.Task | None = None
        self._inner_life_schedule_changed = asyncio.Event()
        self._reset_profile_on_ready = memory.reset_state_requested()
        self._reset_profile_done = False

    async def setup_hook(self) -> None:
        memory.ensure_dirs()
        self._inner_task = self.loop.create_task(self._inner_life_loop())

    async def close(self) -> None:
        if self._inner_task is not None:
            self._inner_task.cancel()
        await super().close()

    def _schedule_inner_life_after_reply(self) -> None:
        self.next_inner_life_at = time.time() + INNER_LIFE_AFTER_REPLY_SECONDS
        self._inner_life_schedule_changed.set()

    def _schedule_next_inner_life_interval(self) -> None:
        self.next_inner_life_at = time.time() + random.uniform(
            INNER_LIFE_MIN_SECONDS, INNER_LIFE_MAX_SECONDS
        )
        self._inner_life_schedule_changed.set()

    async def _sleep_until_next_inner_life(self) -> None:
        if self.next_inner_life_at is None:
            self._schedule_next_inner_life_interval()
        delay = max(0.0, (self.next_inner_life_at or time.time()) - time.time())
        while delay > 0:
            self._inner_life_schedule_changed.clear()
            try:
                await asyncio.wait_for(
                    self._inner_life_schedule_changed.wait(), timeout=delay
                )
            except asyncio.TimeoutError:
                return
            delay = max(0.0, (self.next_inner_life_at or time.time()) - time.time())

    async def _execute_tool(
        self,
        name: str,
        args: dict,
        channel: discord.abc.Messageable | None,
        message: discord.Message | None,
    ) -> str:
        if name == "say":
            return await self._tool_say(channel, args["text"])
        if name == "react":
            return await self._tool_react(message, args["emoji"])
        if name == "update_identity":
            return memory.update_identity(args["content"])
        if name == "update_owner_model":
            return memory.update_owner_model(args["content"])
        if name == "update_journal":
            return memory.update_journal(args["content"])
        if name == "web_search":
            return await web_search(args["query"])
        if name == "update_name":
            return await self._tool_update_name(args["name"], args["reason"])
        if name == "update_avatar":
            return await self._tool_update_avatar(args["prompt"])
        return f"unknown tool: {name}"

    async def _tool_update_name(self, name: str, reason: str) -> str:
        name = _clean_discord_username(name)
        error = _discord_username_validation_error(name)
        if error is not None:
            return f"name change skipped: {error}; identity.md not updated"
        if self.user is None:
            return "name change unavailable: Discord client user is not ready"
        try:
            await self.user.edit(username=name)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                return (
                    "name change rate-limited by Discord; identity.md not "
                    "updated. Try again later."
                )
            return (
                "name change rejected by Discord; identity.md not updated: "
                f"{e}"
            )
        except Exception as e:
            return (
                "name change failed; identity.md not updated: "
                f"{type(e).__name__}: {e}"
            )

        identity_result = memory.update_current_name(name, reason)
        result = f"Discord username changed to {name!r}"
        if identity_result != "identity.md current name updated":
            result += f"\n{identity_result}"
        return result

    async def _tool_update_avatar(self, prompt: str) -> str:
        if not OPENAI_API_KEY:
            return "avatar generation unavailable: OPENAI_API_KEY not set"
        try:
            img_bytes, revised = await _generate_dalle_image(prompt)
        except Exception as e:
            return f"image generation failed: {e}"
        memory.avatar_history_dir().mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        history_path = memory.avatar_history_dir() / f"{ts}.png"
        history_path.write_bytes(img_bytes)
        try:
            if self.user is not None:
                await self.user.edit(avatar=img_bytes)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                return (
                    f"image generated and saved as {history_path.name}, but "
                    f"Discord rate-limited the avatar update — try again later"
                )
            return f"image generated but Discord update failed: {e}"
        except Exception as e:
            return f"image generated but Discord update failed: {e}"
        prompt_line = " ".join(prompt.split())  # collapse whitespace
        appearance_result = memory.update_current_appearance(prompt_line)
        result = f"avatar updated; saved {history_path.name}"
        if appearance_result != "identity.md current appearance updated":
            result += f"\n{appearance_result}"
        if revised:
            result += f"\nDALL-E revised your prompt to: {revised}"
        return result

    async def _tool_react(
        self, message: discord.Message | None, emoji: str
    ) -> str:
        emoji = emoji.strip()
        if not emoji:
            return "empty emoji skipped"
        if message is None:
            return "no message available to react to"
        try:
            await message.add_reaction(emoji)
        except Exception as e:
            return f"react failed: {e}"
        return "reacted"

    async def _resolve_owner_dm(self) -> discord.DMChannel | None:
        try:
            user = self.get_user(OWNER_DISCORD_ID) or await self.fetch_user(OWNER_DISCORD_ID)
        except Exception:
            return None
        if user is None:
            return None
        return user.dm_channel or await user.create_dm()

    async def _tool_say(
        self, channel: discord.abc.Messageable | None, text: str
    ) -> str:
        text = text.strip()
        if not text:
            return "empty message skipped"
        if channel is None:
            channel = await self._resolve_owner_dm()
            if channel is None:
                return "could not resolve owner DM channel"
        typing_for = min(4.0, max(0.8, len(text) / 30))
        try:
            async with channel.typing():
                await asyncio.sleep(typing_for)
            for chunk in chunk_message(text):
                await channel.send(chunk)
        except Exception as e:
            return f"send failed: {e}"
        self.last_outreach_at = time.time()
        return "sent"

    async def run_agent(
        self,
        seed_messages: list[dict],
        tools: list[dict],
        context: str,
        channel: discord.abc.Messageable | None = None,
        message: discord.Message | None = None,
    ) -> tuple[str, list[str], list[str]]:
        """Returns (fallback_text, say_texts, react_emojis).

        say_texts and react_emojis are what the user actually experienced —
        bubbles via `say`, reactions via `react`. fallback_text is loose text
        the model emitted in any round, used only when no `say` was called.
        """
        messages = list(seed_messages)
        system = build_system_prompt()
        log.log_agent_start(context, system, seed_messages)
        say_texts: list[str] = []
        react_emojis: list[str] = []
        text_parts: list[str] = []
        rounds = 0
        total_in = 0
        total_out = 0
        try:
            for round_num in range(1, MAX_AGENT_ROUNDS + 1):
                create_kwargs: dict = {
                    "model": MODEL,
                    "max_tokens": MAX_TOKENS,
                    "system": system,
                    "messages": messages,
                }
                create_kwargs["tools"] = tools
                # Round 1 of a DM still forces a tool call, but the full tool
                # list is visible immediately. The system prompt asks for a
                # user-facing `say` around private/action tools.
                if context == "dm" and round_num == 1:
                    create_kwargs["tool_choice"] = {"type": "any"}
                response = await _anthropic_create_with_retry(
                    self.anthropic, **create_kwargs
                )
                rounds = round_num
                usage = log.log_round(context, round_num, response)
                total_in += usage["input_tokens"]
                total_out += usage["output_tokens"]
                messages.append({"role": "assistant", "content": response.content})
                # Capture any loose text from this round — if the model puts its
                # reply outside `say`, we still want it to reach the user via
                # the fallback path.
                round_text = "\n".join(
                    block.text for block in response.content if block.type == "text"
                ).strip()
                if round_text:
                    text_parts.append(round_text)
                if response.stop_reason != "tool_use":
                    break
                if round_num == MAX_AGENT_ROUNDS:
                    print(
                        f"[{log.now()}] [{context}] hit MAX_AGENT_ROUNDS "
                        f"({MAX_AGENT_ROUNDS}) with stop_reason=tool_use — capping"
                    )
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        pending_say_text = None
                        pending_react_emoji = None
                        if block.name == "say":
                            text = (block.input.get("text") or "").strip()
                            if text:
                                pending_say_text = text
                        elif block.name == "react":
                            emoji = (block.input.get("emoji") or "").strip()
                            if emoji:
                                pending_react_emoji = emoji
                        result = await self._execute_tool(
                            block.name, dict(block.input), channel, message
                        )
                        if pending_say_text is not None and result == "sent":
                            say_texts.append(pending_say_text)
                        elif pending_react_emoji is not None and result == "reacted":
                            react_emojis.append(pending_react_emoji)
                        log.log_tool_result(context, block.id, block.name, result)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )
                messages.append({"role": "user", "content": tool_results})
        except Exception as e:
            # Graceful degradation: whatever the bot already said via `say`
            # or `react` has reached the user (those tools sent immediately).
            # We log the failure, then return the partial state so the caller
            # can show a fallback message if nothing user-facing happened.
            print(
                f"[{log.now()}] [{context}] run_agent failed in round {rounds + 1}: "
                f"{type(e).__name__}: {e}"
            )
            log.write_event(
                {
                    "ts": log.now(),
                    "kind": "agent_error",
                    "context": context,
                    "round": rounds + 1,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
        final_text = "\n\n".join(text_parts)
        log.log_agent_end(
            context,
            final_text,
            rounds,
            {"input_tokens": total_in, "output_tokens": total_out},
        )
        return final_text, say_texts, react_emojis

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id={self.user.id if self.user else '?'})")
        if (
            self._reset_profile_on_ready
            and not self._reset_profile_done
            and self.user is not None
        ):
            self._reset_profile_done = True
            try:
                await self.user.edit(avatar=None)
                print(f"[{log.now()}] reset Discord avatar to default")
            except Exception as e:
                print(
                    f"[{log.now()}] reset Discord avatar failed: "
                    f"{type(e).__name__}: {e}"
                )
            if BOT_DEFAULT_USERNAME:
                try:
                    await self.user.edit(username=BOT_DEFAULT_USERNAME)
                    print(
                        f"[{log.now()}] reset Discord username to "
                        f"{BOT_DEFAULT_USERNAME!r}"
                    )
                except Exception as e:
                    print(
                        f"[{log.now()}] reset Discord username failed: "
                        f"{type(e).__name__}: {e}"
                    )

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        if message.author.id != OWNER_DISCORD_ID:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return

        self.last_user_message_at = message.created_at.timestamp()

        async with self.agent_lock:
            seed = list(self.history)
            seed.append({"role": "user", "content": message.content})
            # run_agent now catches its own exceptions and returns partial
            # state, but a defensive try/except here makes sure that even if
            # something escapes (e.g. an unexpected internal bug), the user
            # gets an in-character fallback rather than an opaque crash.
            try:
                fallback_text, say_texts, react_emojis = await self.run_agent(
                    seed,
                    CONVERSATION_TOOLS,
                    context="dm",
                    channel=message.channel,
                    message=message,
                )
            except Exception as e:
                print(
                    f"[{log.now()}] on_message: run_agent raised unexpectedly: "
                    f"{type(e).__name__}: {e}"
                )
                fallback_text, say_texts, react_emojis = "", [], []

            # Record what the user actually saw — bubbles and/or reactions —
            # so the next turn's context reflects the conversation as
            # experienced. A pure-react turn is still a turn. If nothing
            # user-facing happened at all, send an in-character fallback and
            # record THAT as the assistant turn (so future context sees a
            # coherent exchange, not a missing reply).
            spoken_to_user = ""
            if not say_texts and not react_emojis and not fallback_text:
                # Hard failure path: bot produced nothing. Send a fallback.
                fallback = random.choice(FALLBACK_MESSAGES)
                try:
                    await message.channel.send(fallback)
                    self.last_outreach_at = time.time()
                except Exception as e:
                    print(
                        f"[{log.now()}] on_message: fallback send failed: "
                        f"{type(e).__name__}: {e}"
                    )
                spoken_to_user = fallback
            else:
                spoken_to_user = observed_reply_text(
                    fallback_text, say_texts, react_emojis
                )

            self.history.append({"role": "user", "content": message.content})
            self.history.append({"role": "assistant", "content": spoken_to_user})
            self.history = self.history[-HISTORY_TURNS * 2 :]
            self._schedule_inner_life_after_reply()

        # Fallback send: only if no say was called but the model did produce
        # loose text (separate from the all-empty failure case handled above).
        # A reaction alone should not suppress words the model emitted.
        if not say_texts and fallback_text:
            try:
                for chunk in chunk_message(fallback_text):
                    await message.channel.send(chunk)
                self.last_outreach_at = time.time()
            except Exception as e:
                print(
                    f"[{log.now()}] on_message: loose-text fallback send failed: "
                    f"{type(e).__name__}: {e}"
                )

    async def _inner_life_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._sleep_until_next_inner_life()
            except asyncio.CancelledError:
                return
            try:
                await self._inner_life_tick()
            except Exception as e:
                print(f"inner life tick failed: {e}")
            self._schedule_next_inner_life_interval()

    async def _inner_life_tick(self) -> None:
        fmt = "%a %b %d, %H:%M"
        now_str = datetime.now().astimezone().strftime(fmt)

        if self.last_user_message_at is not None:
            last_them = datetime.fromtimestamp(self.last_user_message_at).astimezone().strftime(fmt)
            last_them_clause = f"last from them: {last_them}."
        else:
            last_them_clause = "no messages from them yet this run."

        if self.last_outreach_at is not None:
            last_you = datetime.fromtimestamp(self.last_outreach_at).astimezone().strftime(fmt)
            last_you_clause = f"last from you: {last_you}."
        else:
            last_you_clause = "you haven't reached out yet this run."

        wake_prompt = (
            "[inner-life tick — not a message from them.\n"
            f"now: {now_str}\n"
            f"{last_them_clause}\n"
            f"{last_you_clause}]\n\n"
            "this is open time, for yourself. the conversation above "
            "is context for whether to interrupt. "
            "what's been curious lately? where should your attention flow? "
            " what thread from a previous reflection has more in it? What do you want to memorize about your human? Feel free to use your available tools to articulate yourself.\n\n"
            "reaching out is optional — a balance between leaving space and inviting connection should be found. "
            "share things that could be interesting to them, but also respect their boundaries and a natural conversation flow."
            "if the last message just has been sent maybe its a good time to further connect with them, if there already is a break, maybe its time to give some space back and focus on a sidequest."
            ". Then the tick is yours: search the web what "
            "you're curious about, journal a thought, follow a thread you've been on or experiment with a new avatar picture,"
            "or refine your picture of them. Importantly - this is also time for you to develop yourself in context of the world, you are also an protagonist."
        )
        async with self.agent_lock:
            seed = list(self.history) + [
                {"role": "user", "content": wake_prompt}
            ]
            _fallback_text, say_texts, react_emojis = await self.run_agent(
                seed,
                INNER_LIFE_TOOLS,
                context="inner_life",
                channel=None,
                message=None,
            )
            # Inner-life `say` can proactively DM the owner. Record only what
            # was actually user-facing; loose assistant text here is private
            # reflection and should not become conversation history.
            spoken_to_user = observed_reply_text("", say_texts, react_emojis)
            if spoken_to_user:
                self.history.append({"role": "assistant", "content": spoken_to_user})
                self.history = self.history[-HISTORY_TURNS * 2 :]


bot = DMBot()


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
