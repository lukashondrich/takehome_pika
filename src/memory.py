import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = PROJECT_ROOT / "seed"

# Files seeded from seed/. By default existing data/ files are preserved across
# restarts; set BOT_RESET_STATE=1 to start a fresh demo from the seed baseline.
SEEDED_FILES = ("identity.md", "ground.md")


def railway_volume_dir() -> Path | None:
    mount_path = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    return Path(mount_path) if mount_path else None


def data_dir() -> Path:
    if custom := os.environ.get("BOT_DATA_DIR"):
        return Path(custom)
    if volume := railway_volume_dir():
        return volume / "data"
    return PROJECT_ROOT / "data"


def logs_dir() -> Path:
    if custom := os.environ.get("BOT_LOGS_DIR"):
        return Path(custom)
    if volume := railway_volume_dir():
        return volume / "logs"
    return PROJECT_ROOT / "logs"


def ground_file() -> Path: return data_dir() / "ground.md"
def identity_file() -> Path: return data_dir() / "identity.md"
def owner_file() -> Path: return data_dir() / "owner.md"
def journal_file() -> Path: return data_dir() / "journal.md"
def avatars_file() -> Path: return data_dir() / "avatars.md"
def agent_log_file() -> Path: return logs_dir() / "agent.jsonl"
def avatar_history_dir() -> Path: return logs_dir() / "avatar_history"


def reset_state_requested() -> bool:
    return os.environ.get("BOT_RESET_STATE", "").lower() in {"1", "true", "yes", "on"}


def ensure_dirs() -> None:
    """Create data/log dirs and seed missing files.

    Normal restarts preserve the bot's written memory. For a clean evaluation
    run, set BOT_RESET_STATE=1; that restores seeded files and clears
    model-written state.
    """
    data_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    reset = reset_state_requested()
    for name in SEEDED_FILES:
        src = SEED_DIR / name
        dst = data_dir() / name
        if reset or not dst.exists():
            dst.write_text(src.read_text() if src.exists() else "")
    for path in (owner_file(), journal_file()):
        if reset or not path.exists():
            path.write_text("")
    if reset:
        if avatars_file().exists():
            avatars_file().unlink()
        if avatar_history_dir().exists():
            shutil.rmtree(avatar_history_dir())


def read_file(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def update_identity(content: str) -> str:
    try:
        identity_file().write_text(content.rstrip() + "\n")
    except Exception as e:
        return f"identity.md update failed: {e}"
    return "identity.md updated"


def update_owner_model(content: str) -> str:
    try:
        owner_file().write_text(content.rstrip() + "\n")
    except Exception as e:
        return f"owner.md update failed: {e}"
    return "owner.md updated"


def update_journal(content: str) -> str:
    try:
        journal_file().write_text(content.rstrip() + "\n")
    except Exception as e:
        return f"journal.md update failed: {e}"
    return "journal.md updated"


IDENTITY_SECTION_HEADINGS = ("Current name:", "Current appearance:")


def _strip_identity_section(identity: str, heading: str) -> str:
    out: list[str] = []
    skipping = False
    for line in identity.rstrip().splitlines():
        if line == heading:
            skipping = True
            while out and out[-1] == "":
                out.pop()
            continue
        if skipping and line in IDENTITY_SECTION_HEADINGS:
            if out and out[-1] != "":
                out.append("")
            out.append(line)
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out).strip()


def _without_no_name_placeholder(identity: str) -> str:
    lines = [
        line
        for line in identity.splitlines()
        if not line.startswith("I do not have a name yet.")
    ]
    return "\n".join(lines).strip()


def _identity_with_current_name(identity: str, name: str, reason: str) -> str:
    identity = _without_no_name_placeholder(
        _strip_identity_section(identity, "Current name:")
    )
    name_block = f"Current name:\n{name.strip()}"
    reason = reason.strip()
    if reason:
        name_block += f"\n\nWhy this name fits:\n{reason}"
    return f"{identity}\n\n{name_block}\n" if identity else f"{name_block}\n"


def update_current_name(name: str, reason: str) -> str:
    try:
        identity = read_file(identity_file())
        identity_file().write_text(_identity_with_current_name(identity, name, reason))
    except Exception as e:
        return f"identity.md name update failed: {e}"
    return "identity.md current name updated"


def _identity_with_current_appearance(identity: str, description: str) -> str:
    identity = _strip_identity_section(identity, "Current appearance:")
    appearance = f"Current appearance:\n{description.strip()}"
    return f"{identity}\n\n{appearance}\n" if identity else f"{appearance}\n"


def update_current_appearance(description: str) -> str:
    try:
        identity = read_file(identity_file())
        identity_file().write_text(
            _identity_with_current_appearance(identity, description)
        )
    except Exception as e:
        return f"identity.md appearance update failed: {e}"
    return "identity.md current appearance updated"
