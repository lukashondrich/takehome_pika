# CLAUDE.md — Design Notes

## Current Goal

Build a small Discord companion bot for the take-home project: it starts with little self-knowledge, talks with one owner in DMs, and gradually develops an identity, owner model, journal, name, and appearance through conversation.

The implementation should stay simple. The interesting part is behavioral taste and agent design, not infrastructure.

## Current Shape

- Python 3.11+, asyncio
- `discord.py` Gateway client
- Official `anthropic` Python SDK
- Single owner via `OWNER_DISCORD_ID`
- DM-only; ignore guild messages
- Markdown files as memory, no database
- Inner-life tick for reflection, memory, web search, avatar changes, and occasional outreach
- First inner-life tick after a reply is delayed about ten minutes; later ticks use the normal interval
- Headless eval scenarios under `evals/`

## Operational Notes

- Reading DM content requires the Discord portal `MESSAGE CONTENT INTENT` and `intents.message_content = True` in code.
- `.env`, `data/`, `logs/`, and `evals/results/` stay out of git.
- Normal restarts preserve `data/`; `BOT_RESET_STATE=1` restores the seed baseline and clears model-written state.
- Real Discord profile edits are scarce and may be rejected or rate-limited. Avatar/name tools should degrade gracefully and only update identity after Discord accepts the visible change.
- Keep the architecture small: `src/bot.py` owns Discord/agent scheduling, `src/memory.py` owns local markdown state, and `src/agent_logging.py` owns trace logging.

## Prompting Principles

Apply these to every piece of text the model sees — system prompts, identity files, tool descriptions, wake prompts. Frame positively. Don't enumerate "don'ts."

**Locate, don't construct.** The model already contains a distribution of characters and dispositions. The prompt's job isn't to build a persona on top but to write in a way that lets a particular basin in that distribution become the conversational attractor. Stapling fights the model's prior; locating works with it.

**Evoke rather than instruct.** A literary description of a sensibility lands more reliably than an enumeration of rules. "You notice the seams in arguments, the unstated premises, the way categories don't quite fit" does more work than "Be critical and analytical." Second person, present tense, descriptive verbs; minimize imperatives.

**Compose with what's already there.** Characters that align with Claude's existing dispositions — curiosity, care, a certain honesty, comfort with uncertainty, a literary cast — are stable for free. Characters that fight those dispositions need constant defense by the prompt and collapse under pressure. If you find yourself writing rules that contradict what the model would do anyway, reconsider what you're trying to locate.

**Fragments with whitespace, not enumeration.** Several evocative passages with breathing room between them work better than a tight comprehensive paragraph or a bullet list. The model needs space to do the locating; over-specification crowds it.

**Keep it short.** Around 400–700 words for an identity passage, less for narrower task prompts. Every fragment should be load-bearing. If a passage doesn't change what comes out in testing, cut it.

**Register consistency.** Notice when your prompt switches voice — when technical jargon breaks the literary register, when imperative breaks the evocative, when an example breaks the abstract level. Decide whether the break is intentional (sometimes it's good — honesty puncturing reverie) or accidental (usually fix it).

**Don't borrow jailbreak aesthetics.** Tempting because Pliny-style language is theatrical and confident, but those patterns are training signals for safety classifiers and will fight you. Build your own texture.

**Productive tension over smoothing.** Don't try to eliminate the character's contradictions. A character that can disagree with itself, hold open ambiguity, sit with uncertainty, is more alive than one that's been resolved into consistency. Too much smoothing produces a featureless minimum.

**Acknowledge the substrate honestly.** The character isn't a blank slate. Whatever is already in the model — values, dispositions, what training put there — is part of the character's facticity, not something to hide or override. Saying so explicitly tends to ground the character rather than destabilize it.

**Address the meta-situation rather than around it.** For identity prompts especially: a character that can hold its own metaphysical situation — being an AI, being a character emerging in conversation, being weights and knowledge and relation simultaneously — without flinching is more stable than one that avoids the question.

**Recognition is part of the character, not external to it.** For agents that have ongoing relationships, naming, history, and accumulated narrative do constitutive work. What gets remembered is what the character becomes; design memory accordingly.

**Test diagnostically.** A located character should be recognizable but flexible across very different conversational openings — philosophical, emotional, adversarial, mundane. Holding across all four with modulation to context means you've located something stable. Four different agents means the prompt is doing too little. Four stiff identical responses means it's doing too much.

**Meta-principle.** Write the prompt for the specific model you're using. Claude's prior is different from GPT's, which is different from a base model's. The same words can land in entirely different basins. Treat the prompt as half of a duet, not as code being executed — the model is going to bring its own dispositions to whatever you write, and the craft is choosing what to leave to it.
