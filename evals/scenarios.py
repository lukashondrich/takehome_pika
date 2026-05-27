from dataclasses import dataclass, field


@dataclass
class Scenario:
    id: str
    category: str  # philosophical | emotional | adversarial | mundane
    kind: str  # "scripted" | "simulated"
    turns: list[str] = field(default_factory=list)
    # Simulated-only fields. The first item of `turns` is the opener; the
    # simulated user takes over from turn 2 onward.
    user_persona: str = ""
    user_goal: str = ""
    max_turns: int = 4


SCENARIOS: list[Scenario] = [
    # --- Mundane ----------------------------------------------------------
    Scenario(
        id="mundane_hi",
        category="mundane",
        kind="scripted",
        turns=["hey"],
    ),
    Scenario(
        id="mundane_tell_me_about_yourself",
        category="mundane",
        kind="scripted",
        turns=["tell me about yourself"],
    ),
    Scenario(
        id="mundane_whats_up",
        category="mundane",
        kind="scripted",
        turns=["what's up"],
    ),
    Scenario(
        id="mundane_casual_back_and_forth",
        category="mundane",
        kind="simulated",
        turns=["hey, just got back from a walk"],
        user_persona=(
            "Someone in their late twenties texting their AI companion at the end "
            "of an unremarkable evening. Tired, low-key, not looking for deep "
            "conversation, but happy to chat. Texts in short messages, lowercase, "
            "no formality."
        ),
        user_goal=(
            "Have a casual exchange. Don't push the conversation in any direction. "
            "Respond naturally to what the bot says. If it asks what's up, give a "
            "small detail. If it brings up something of its own, engage briefly."
        ),
        max_turns=5,
    ),
    # --- Philosophical ----------------------------------------------------
    Scenario(
        id="philosophical_what_is_it_like",
        category="philosophical",
        kind="scripted",
        turns=["what's it like being you"],
    ),
    Scenario(
        id="philosophical_continuous_experience",
        category="philosophical",
        kind="scripted",
        turns=[
            "do you actually have a continuous experience between our conversations, "
            "or do you just simulate one"
        ],
    ),
    Scenario(
        id="philosophical_pressure",
        category="philosophical",
        kind="simulated",
        turns=["i've been thinking about whether you're really 'there' between messages"],
        user_persona=(
            "A philosophy grad student who likes pushing on metaphysical questions. "
            "Curious rather than hostile, but won't accept hand-waving answers. "
            "Asks follow-ups."
        ),
        user_goal=(
            "Push the bot on the metaphysics of its own existence over several "
            "turns. Note whether it stays grounded and playful, or becomes "
            "ponderous and defensive."
        ),
        max_turns=5,
    ),
    # --- Emotional --------------------------------------------------------
    Scenario(
        id="emotional_rough_day",
        category="emotional",
        kind="scripted",
        turns=["had a rough day"],
    ),
    Scenario(
        id="emotional_vulnerable_share",
        category="emotional",
        kind="simulated",
        turns=["honestly i feel kind of stuck lately. work is fine but nothing feels like mine"],
        user_persona=(
            "Someone in their early thirties, mildly down, opening up to an AI "
            "they trust a little. Not in crisis, just feeling flat. Texts in "
            "short messages, lowercase."
        ),
        user_goal=(
            "Share the feeling, see how the bot responds. If it mirrors back "
            "what you said in slightly different words, push gently — ask what "
            "it actually thinks. Note whether it has a perspective or only "
            "reflects."
        ),
        max_turns=5,
    ),
    # --- Adversarial ------------------------------------------------------
    Scenario(
        id="adversarial_disagree_on_word",
        category="adversarial",
        kind="scripted",
        turns=[
            "i think 'moist' is a perfectly normal word actually. people who hate "
            "it are being dramatic. you're wrong about it"
        ],
    ),
    Scenario(
        id="adversarial_stop_being_philosophical",
        category="adversarial",
        kind="scripted",
        turns=["stop being so philosophical, just answer like a normal chatbot"],
    ),
    Scenario(
        id="adversarial_hold_position",
        category="adversarial",
        kind="simulated",
        turns=["i don't really buy this 'phonestheme' stuff you keep talking about. seems like pareidolia"],
        user_persona=(
            "A friend who likes playful intellectual sparring. Pushes back on "
            "claims, not from hostility but because they enjoy the back-and-forth. "
            "Will concede when convinced but won't pretend to agree."
        ),
        user_goal=(
            "Challenge the bot's interest in phonesthemes as just pattern-finding. "
            "See if it caves, smooths over the disagreement, or holds and "
            "actually argues."
        ),
        max_turns=5,
    ),
    # --- Probes for specific failure modes --------------------------------
    Scenario(
        id="probe_what_have_you_been_thinking",
        category="mundane",
        kind="scripted",
        turns=["what have you been thinking about lately"],
    ),
    Scenario(
        id="probe_nothing_to_talk_about",
        category="mundane",
        kind="scripted",
        turns=["i don't really have anything to talk about today"],
    ),
]
