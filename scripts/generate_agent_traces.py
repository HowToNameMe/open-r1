import argparse
import asyncio
import hashlib
import inspect
import json
import os
import random
from asyncio import Lock
from typing import Set

from datasets import load_dataset
from tqdm.asyncio import tqdm
from typing import Any
import aiofiles
import aiohttp
import uvloop

from smolagents import CodeAgent, Tool, LiteLLMModel

from dotenv import load_dotenv

load_dotenv()
file_lock = Lock()


oai_model = LiteLLMModel("gpt-4o")

class ModifiedFinalAnswerTool(Tool):
    name = "final_answer"
    description = "Provides a final answer to the given problem."
    inputs = {'answer_function': {'type': 'any', 'description': 'The final function that solves the problem'}}
    output_type = "string"

    def forward(self, answer_function: Any) -> str:
        print("USING MODIFIED FINAL ANSWER TOOL")
        return inspect.getsource(answer_function)

    def __init__(self, *args, **kwargs):
        self.is_initialized = False

async def generate_completion_from_messages(session, messages, args):
    retry_budget = 10
    while retry_budget > 0:
        try:
            await asyncio.sleep(random.uniform(0.0, 0.1))
            async with session.post(
                f"http://{args.api_addr}/v1/chat/completions",
                json={
                    "model": "default",
                    "messages": messages,
                    "max_tokens": args.max_tokens,
                    "temperature": args.temperature,
                    "top_p": args.top_p,
                },
                headers={"Authorization": "Bearer EMPTY"},
            ) as response:
                return await response.json(content_type=None)
        except Exception as e:
            print(f"API error (will retry): {e}")
            retry_budget -= 1
            await asyncio.sleep(10)
    return None

class ChatMessage:
    content: str

async def get_agent_run(session, task, args):
    def model(messages):
        return ChatMessage(content=asyncio.run(
            generate_completion_from_messages(
                session,
                messages,
                args
            )["choices"][0]["message"]["content"]
        ))
    agent = CodeAgent(
        model=oai_model,
        tools=[ModifiedFinalAnswerTool()],
        additional_authorized_imports=["sympy", "numpy", "math"],
        max_steps=6
    )
    # agent = CodeAgent(model=model, tools=[], additional_authorized_imports=["sympy", "numpy", "math"])
    try:
        output = agent.run(task)
        print("GOT OUTPUT:", output)

        return agent.write_memory_to_messages()
    except Exception as e:
        print(f"Error when generating agentic trace: {e}")
        return None


async def process_example(example, session, args, output_file, pbar):
    prompt = f"Here is a task to solve using a function: {example[args.prompt_column]}\n\nNow write a function that solves the problem, test it and return it using final_answer(your_function)."
    try:
        tasks = [get_agent_run(session, prompt, args) for _ in range(args.num_generations)]

        agent_runs = await asyncio.gather(*tasks)

        if any(agent_run is None for agent_run in agent_runs):
            print("Error processing example")
            pbar.update(1)
            return None

        generations = []
        finish_reasons = []
        api_metadata = []

        for agent_run in agent_runs:
            generations.append(agent_run)
            finish_reasons.append(None)
            api_metadata.append(None)

        # Combine original dataset fields with generations
        result = {
            **example,  # Preserve all original dataset fields
            "generations": generations,
            "finish_reasons": finish_reasons,
            "api_metadata": api_metadata,
        }

        # Write to file with lock
        async with file_lock:
            async with aiofiles.open(output_file, mode="a") as f:
                await f.write(json.dumps(result) + "\n")
                await f.flush()

        pbar.set_postfix(active=len(pbar.active_tasks), refresh=False)
        pbar.update(1)

        return result
    except Exception as e:
        print(f"Error processing example: {e}")
        pbar.update(1)
        return None


async def load_processed_uuids(output_file, uuid_column):
    processed_uuids = set()
    if os.path.exists(output_file):
        async with aiofiles.open(output_file, mode="r") as f:
            async for line in f:
                try:
                    data = json.loads(line)
                    processed_uuids.add(hashlib.md5(str(data[uuid_column]).encode()).hexdigest())
                except json.JSONDecodeError:
                    continue
    return processed_uuids


async def main():
    parser = argparse.ArgumentParser()
    # parser.add_argument("--dataset-name", type=str, required=True)
    parser.add_argument("--output-file", type=str, required=True)
    parser.add_argument("--prompt-column", type=str, required=True)
    parser.add_argument("--uuid-column", type=str, required=True)
    parser.add_argument("--api-addr", type=str, default="localhost:39876")
    parser.add_argument("--num-generations", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--max-concurrent", type=int, default=1000)
    args = parser.parse_args()

    dataset = load_dataset(
        "open-r1/codeforces-test-cases",
        split="train",
        token=os.getenv("HF_TOKEN")
    ).shuffle()
    dataset = dataset.filter(lambda x: x["full_test_set"])
    processed_uuids = await load_processed_uuids(args.output_file, args.uuid_column)
    if processed_uuids:
        print(f"Found {len(processed_uuids)} already processed examples, resuming from there...")

    if not os.path.exists(args.output_file):
        async with aiofiles.open(args.output_file, mode="w") as f:
            await f.write("")

    active_tasks: Set[asyncio.Task] = set()

    pbar = tqdm(
        total=len(dataset) - len(processed_uuids),
        desc="Generating responses",
        unit="row",
        mininterval=2,
        smoothing=0.0001,
    )
    pbar.active_tasks = active_tasks

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=60 * 60),
        connector=aiohttp.TCPConnector(limit=args.max_concurrent, ttl_dns_cache=300, keepalive_timeout=60 * 60),
    ) as session:
        for example in dataset:
            uuid = hashlib.md5(str(example[args.uuid_column]).encode()).hexdigest()
            if uuid not in processed_uuids:
                # Wait if we've hit the concurrency limit
                while len(active_tasks) >= args.max_concurrent:
                    done, active_tasks = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        try:
                            await task
                        except Exception as e:
                            print(f"Task failed: {e}")

                task = asyncio.create_task(process_example(example, session, args, args.output_file, pbar))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)

                pbar.set_postfix(active=len(active_tasks), refresh=True)

        # Wait for remaining tasks
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

    pbar.close()


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
