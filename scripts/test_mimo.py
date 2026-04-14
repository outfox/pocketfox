"""Manual smoke test for MiMo V2 Pro via OpenRouter.

Requires OPENROUTER_API_KEY in env. Not part of the pytest suite — it hits the
real API and will spend tokens.

Usage:
    python scripts/test_mimo.py

Verifies end-to-end:
  1. `extra_body={"reasoning": {"enabled": True}}` reaches OpenRouter.
  2. `reasoning_details` is parsed off the response.
  3. A follow-up turn with the full `reasoning_details` round-tripped in
     conversation history is accepted by OpenRouter without complaint.
"""

import asyncio
import os
import sys

from pocketfox.agent.context import ContextBuilder  # noqa: F401  (unused direct, but mirrors prod code path)
from pocketfox.providers.litellm_provider import LiteLLMProvider


def _require_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("ERROR: set OPENROUTER_API_KEY before running this script", file=sys.stderr)
        sys.exit(1)
    return key


async def main() -> None:
    provider = LiteLLMProvider(
        api_key=_require_key(),
        default_model="openrouter/xiaomi/mimo-v2-pro",
        provider_name="openrouter",
    )

    # --- Turn 1 -------------------------------------------------------------
    messages = [{"role": "user", "content": "Think step by step: what's 17 * 23?"}]
    print("[turn 1] calling MiMo V2 Pro...")
    response1 = await provider.chat(messages=messages)

    print(f"[turn 1] content: {response1.content!r}")
    print(f"[turn 1] reasoning_content (first 500 chars): {(response1.reasoning_content or '')[:500]!r}")
    if response1.reasoning_details:
        print(f"[turn 1] reasoning_details[0]: {response1.reasoning_details[0]!r}")
        print(f"[turn 1] reasoning_details count: {len(response1.reasoning_details)}")
    else:
        print("[turn 1] reasoning_details: None  <-- likely a problem")

    if not response1.reasoning_details and not response1.reasoning_content:
        print(
            "[turn 1] WARNING: no reasoning returned. The extra_body path may not "
            "have reached OpenRouter. Try setting `litellm.set_verbose = True` "
            "in this script and re-running to see the outgoing HTTP body.",
            file=sys.stderr,
        )

    # --- Turn 2 -------------------------------------------------------------
    assistant_msg: dict = {"role": "assistant", "content": response1.content or ""}
    if response1.reasoning_content:
        assistant_msg["reasoning_content"] = response1.reasoning_content
    if response1.reasoning_details:
        assistant_msg["reasoning_details"] = response1.reasoning_details

    messages.append(assistant_msg)
    messages.append({"role": "user", "content": "Now double it."})

    print("\n[turn 2] calling MiMo V2 Pro with round-tripped reasoning_details...")
    response2 = await provider.chat(messages=messages)

    print(f"[turn 2] content: {response2.content!r}")
    print(f"[turn 2] finish_reason: {response2.finish_reason}")
    if response2.finish_reason == "error":
        print("[turn 2] FAILED — OpenRouter rejected the history", file=sys.stderr)
        sys.exit(2)

    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
