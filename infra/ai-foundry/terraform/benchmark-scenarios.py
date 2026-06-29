#!/usr/bin/env python3
"""
Benchmark all 4 AI Foundry deployment scenarios.

Measures time for:
  1. Chat completion (all scenarios)
  2. Agent create → thread create → message send → run → response (capability host scenarios)

Usage:
  python benchmark-scenarios.py
"""
import time
import asyncio
import json
from dataclasses import dataclass, field

from azure.identity.aio import DefaultAzureCredential
from azure.ai.agents.aio import AgentsClient
from openai import AsyncAzureOpenAI


SCENARIOS = [
    {
        "name": "public-no-cap-host",
        "endpoint": "https://aifoundry23775.services.ai.azure.com",
        "project": "project23775",
        "networking": "public",
        "capability_host": False,
    },
    {
        "name": "public-cap-host",
        "endpoint": "https://aifoundry60085.services.ai.azure.com",
        "project": "project60085",
        "networking": "public",
        "capability_host": True,
    },
    # Private scenarios require VNet access — cannot be tested from public internet
    # {
    #     "name": "private-no-cap-host",
    #     "endpoint": "https://aifoundry17542.services.ai.azure.com",
    #     "project": "project17542",
    #     "networking": "private",
    #     "capability_host": False,
    # },
    # {
    #     "name": "private-cap-host",
    #     "endpoint": "https://aifoundry38591.services.ai.azure.com",
    #     "project": "project38591",
    #     "networking": "private",
    #     "capability_host": True,
    # },
]

MODEL = "gpt-4o"
TEST_MESSAGE = "What is 2+2? Reply with just the number."


@dataclass
class TimingResult:
    scenario: str
    networking: str
    capability_host: bool
    chat_completion_ms: float = 0.0
    agent_create_ms: float = 0.0
    thread_create_ms: float = 0.0
    message_send_ms: float = 0.0
    run_complete_ms: float = 0.0
    total_agent_ms: float = 0.0
    response_text: str = ""
    agent_response_text: str = ""
    error: str = ""


async def benchmark_chat_completion(oai_client: AsyncAzureOpenAI, result: TimingResult):
    """Benchmark a simple chat completion via the OpenAI client."""
    t0 = time.perf_counter()
    response = await oai_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": TEST_MESSAGE}],
        max_tokens=10,
    )
    elapsed = (time.perf_counter() - t0) * 1000
    result.chat_completion_ms = elapsed
    result.response_text = response.choices[0].message.content.strip()


async def benchmark_agent_conversation(client: AgentsClient, result: TimingResult):
    """Benchmark full agent flow: create agent → thread → message → run → response."""
    total_start = time.perf_counter()

    # Create agent
    t0 = time.perf_counter()
    agent = await client.create_agent(
        model=MODEL,
        name="benchmark-agent",
        instructions="You are a helpful math assistant. Be extremely brief.",
    )
    result.agent_create_ms = (time.perf_counter() - t0) * 1000

    try:
        # Create thread
        t0 = time.perf_counter()
        thread = await client.threads.create()
        result.thread_create_ms = (time.perf_counter() - t0) * 1000

        # Send message
        t0 = time.perf_counter()
        await client.messages.create(
            thread_id=thread.id,
            role="user",
            content=TEST_MESSAGE,
        )
        result.message_send_ms = (time.perf_counter() - t0) * 1000

        # Create run and wait for completion
        t0 = time.perf_counter()
        run = await client.runs.create_and_process(
            thread_id=thread.id,
            agent_id=agent.id,
        )
        result.run_complete_ms = (time.perf_counter() - t0) * 1000

        # Get response
        messages = await client.messages.list(thread_id=thread.id)
        for msg in messages.data:
            if msg.role == "assistant":
                result.agent_response_text = msg.content[0].text.value.strip()
                break
    finally:
        # Cleanup
        await client.delete_agent(agent.id)

    result.total_agent_ms = (time.perf_counter() - total_start) * 1000


async def run_benchmark(scenario: dict, credential) -> TimingResult:
    """Run all benchmarks for a single scenario."""
    result = TimingResult(
        scenario=scenario["name"],
        networking=scenario["networking"],
        capability_host=scenario["capability_host"],
    )

    endpoint = f"{scenario['endpoint']}/api/projects/{scenario['project']}"
    # OpenAI endpoint uses the cognitiveservices domain
    oai_endpoint = scenario["endpoint"].replace(".services.ai.azure.com", ".openai.azure.com")

    try:
        from azure.identity.aio import get_bearer_token_provider
        token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

        oai_client = AsyncAzureOpenAI(
            azure_endpoint=oai_endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-12-01-preview",
        )

        # 1. Chat completion (all scenarios)
        print(f"    Chat completion...", end=" ", flush=True)
        await benchmark_chat_completion(oai_client, result)
        print(f"{result.chat_completion_ms:.0f}ms -> \"{result.response_text}\"")

        await oai_client.close()

        # 2. Agent conversation (capability host only)
        if scenario["capability_host"]:
            async with AgentsClient(
                endpoint=endpoint,
                credential=credential,
            ) as agents_client:
                print(f"    Agent conversation...", end=" ", flush=True)
                await benchmark_agent_conversation(agents_client, result)
                print(f"{result.total_agent_ms:.0f}ms -> \"{result.agent_response_text}\"")
        else:
            print(f"    Agent conversation... SKIPPED (no capability host)")

    except Exception as e:
        result.error = str(e)[:200]
        print(f"    ERROR: {result.error}")

    return result


def print_summary(results: list[TimingResult]):
    """Print a comparison table."""
    print("\n" + "=" * 100)
    print("  BENCHMARK RESULTS — Chat Completion (ms)")
    print("=" * 100)
    print(f"  {'Scenario':<30} {'Network':<10} {'Cap Host':<10} {'Chat (ms)':<12} {'Response':<20}")
    print(f"  {'─' * 30} {'─' * 10} {'─' * 10} {'─' * 12} {'─' * 20}")
    for r in results:
        if r.error:
            print(f"  {r.scenario:<30} {r.networking:<10} {str(r.capability_host):<10} {'ERROR':<12} {r.error[:20]}")
        else:
            print(f"  {r.scenario:<30} {r.networking:<10} {str(r.capability_host):<10} {r.chat_completion_ms:<12.0f} {r.response_text[:20]}")

    cap_results = [r for r in results if r.capability_host and not r.error]
    if cap_results:
        print(f"\n{'=' * 100}")
        print("  BENCHMARK RESULTS — Agent Conversation Breakdown (ms)")
        print("=" * 100)
        print(f"  {'Scenario':<30} {'Agent Create':<14} {'Thread Create':<15} {'Msg Send':<12} {'Run+Response':<14} {'Total':<10}")
        print(f"  {'─' * 30} {'─' * 14} {'─' * 15} {'─' * 12} {'─' * 14} {'─' * 10}")
        for r in cap_results:
            print(
                f"  {r.scenario:<30} "
                f"{r.agent_create_ms:<14.0f} "
                f"{r.thread_create_ms:<15.0f} "
                f"{r.message_send_ms:<12.0f} "
                f"{r.run_complete_ms:<14.0f} "
                f"{r.total_agent_ms:<10.0f}"
            )

    print()


async def main():
    print("=" * 100)
    print("  AI Foundry Scenario Benchmark")
    print("  Testing: chat completion + agent conversation across 4 scenarios")
    print("=" * 100)
    print()

    credential = DefaultAzureCredential()

    results = []
    for scenario in SCENARIOS:
        print(f"  [{scenario['name']}] ({scenario['networking']} / cap_host={scenario['capability_host']})")
        result = await run_benchmark(scenario, credential)
        results.append(result)
        print()

    await credential.close()

    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
