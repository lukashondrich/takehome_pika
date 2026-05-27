import hashlib
import json
from datetime import datetime, timezone

try:
    from src import memory
except ModuleNotFoundError:
    import memory  # type: ignore


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_event(event: dict) -> None:
    memory.logs_dir().mkdir(parents=True, exist_ok=True)
    with memory.agent_log_file().open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def print_block(prefix: str, label: str, body: str, indent: str = "    ") -> None:
    print(f"{prefix} {label}:")
    for line in body.splitlines() or [""]:
        print(f"{prefix}{indent}{line}")


def _serializable_seed(seed: list[dict]) -> list[dict]:
    out = []
    for m in seed:
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        else:
            out.append({"role": m["role"], "content": "<non-string content>"})
    return out


def log_agent_start(context: str, system_prompt: str, seed: list[dict]) -> None:
    ts = now()
    sp_sha256 = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    write_event(
        {
            "ts": ts,
            "kind": "agent_start",
            "context": context,
            "system_prompt": system_prompt,
            "system_prompt_sha256": sp_sha256,
            "seed_messages": _serializable_seed(seed),
        }
    )
    prefix = f"[{ts}] [{context}]"
    print(f"{prefix} === agent run start ===")
    print_block(
        prefix,
        f"system_prompt ({len(system_prompt)} chars, sha256={sp_sha256[:12]})",
        system_prompt,
    )
    for m in seed:
        if isinstance(m.get("content"), str):
            print_block(prefix, m["role"], m["content"])


def log_round(context: str, round_num: int, response) -> dict:
    ts = now()
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                {"id": block.id, "name": block.name, "input": dict(block.input)}
            )
    text = "\n".join(text_parts)
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    write_event(
        {
            "ts": ts,
            "kind": "round",
            "context": context,
            "round_num": round_num,
            "stop_reason": response.stop_reason,
            "text": text,
            "tool_calls": tool_calls,
            "usage": usage,
        }
    )
    prefix = f"[{ts}] [{context}]"
    print(
        f"{prefix} round {round_num}: stop={response.stop_reason} "
        f"tokens=in:{usage['input_tokens']}/out:{usage['output_tokens']}"
    )
    if text:
        print_block(prefix, "  assistant_text", text, indent="      ")
    for tc in tool_calls:
        print(f"{prefix}   tool_use: {tc['name']}  (id={tc['id']})")
        for k, v in tc["input"].items():
            print_block(prefix, f"    {k}", str(v), indent="      ")
    return usage


def log_tool_result(context: str, tool_use_id: str, tool_name: str, result: str) -> None:
    ts = now()
    write_event(
        {
            "ts": ts,
            "kind": "tool_result",
            "context": context,
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "result": result,
        }
    )
    prefix = f"[{ts}] [{context}]"
    print_block(prefix, f"  tool_result {tool_name}", result, indent="      ")


def log_agent_end(context: str, final_text: str, rounds: int, total_usage: dict) -> None:
    ts = now()
    write_event(
        {
            "ts": ts,
            "kind": "agent_end",
            "context": context,
            "final_text": final_text,
            "rounds": rounds,
            "total_usage": total_usage,
        }
    )
    prefix = f"[{ts}] [{context}]"
    print(
        f"{prefix} === agent run end === rounds={rounds} "
        f"total_tokens=in:{total_usage['input_tokens']}/out:{total_usage['output_tokens']}"
    )
    if final_text:
        print_block(prefix, "  final_text", final_text, indent="    ")
