from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
import ast
import json
import asyncio
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import threading
from datetime import datetime
import yaml

USER_DOTNET_ROOT = Path.home() / ".dotnet"
if USER_DOTNET_ROOT.exists():
    os.environ.setdefault("DOTNET_ROOT", str(USER_DOTNET_ROOT))
    if str(USER_DOTNET_ROOT) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{USER_DOTNET_ROOT}:{os.environ.get('PATH', '')}"

from agent_framework.declarative import WorkflowFactory
from agent_framework_declarative import AgentFactory
from azure.identity import DefaultAzureCredential

app = FastAPI()

# Thread pool for running sync SDK calls
executor = ThreadPoolExecutor(max_workers=10)

# Configuration - prefer explicit overrides, then fall back to the repo's dev project
AZURE_PROJECT_ENDPOINT = (
    os.environ.get("AZURE_PROJECT_ENDPOINT")
    or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    or "https://oai-resource-aueast.services.ai.azure.com/api/projects/oai-project-aueast"
)
APP_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = Path(
    os.environ.get(
        "WORKFLOW_DIR",
        str(APP_DIR / "workflows"),
    )
)
AGENTS_DIR = Path(
    os.environ.get(
        "AGENTS_DIR",
        str(APP_DIR / "agents"),
    )
)
ENV_FILE = APP_DIR.parent / ".env"

# Workflow to use
WORKFLOW_NAME = os.environ.get("WORKFLOW_NAME", "purple-workflow")

# Guardrail agent name
GUARDRAIL_AGENT_NAME = os.environ.get("GUARDRAIL_AGENT_NAME", "guardrail-agent")

# Global state
conversation_history: List[Dict[str, str]] = []
system_instructions: str = "You are a helpful assistant."

# Store timing data for visualization
timing_logs: List[Dict[str, Any]] = []


class TimingEvents:
    """Simple event-based timing - just a list of events with time, category, and event name."""
    
    def __init__(self, request_id: str):
        self.request_id = request_id
        self.events: List[Dict[str, Any]] = []
        self.start_time = time.time()
        self.guardrail_passed = True
        self.guardrail_reason = ""
        self.content_filters = None
    
    def add(self, category: str, event: str, **extra):
        """Add an event. Categories: request, blue_guardrail, purple_workflow"""
        self.events.append({
            "time": time.time(),
            "time_ms": (time.time() - self.start_time) * 1000,
            "category": category,
            "event": event,
            **extra
        })
    
    def to_dict(self):
        return {
            "request_id": self.request_id,
            "start_time": self.start_time,
            "events": self.events,
            "guardrail_passed": self.guardrail_passed,
            "guardrail_reason": self.guardrail_reason,
            "content_filters": self.content_filters,
            "total_duration_ms": (time.time() - self.start_time) * 1000 if self.events else 0
        }


def get_workflow_factory() -> WorkflowFactory:
    """Create a local declarative workflow factory backed by the configured Foundry project."""
    agent_factory = get_agent_factory()

    agents = {}
    if AGENTS_DIR.exists():
        for agent_yaml in AGENTS_DIR.glob("*.yaml"):
            agent = agent_factory.create_agent_from_yaml_path(str(agent_yaml))
            agents[agent.name] = agent

    return WorkflowFactory(
        agent_factory=agent_factory,
        agents=agents,
        env_file=str(ENV_FILE) if ENV_FILE.exists() else None,
    )


def get_agent_factory() -> AgentFactory:
    """Create an agent factory that resolves local YAMLs against the configured Foundry project."""
    if "FOUNDRY_PROJECT_ENDPOINT" not in os.environ:
        os.environ["FOUNDRY_PROJECT_ENDPOINT"] = AZURE_PROJECT_ENDPOINT

    env_file_path = str(ENV_FILE) if ENV_FILE.exists() else None
    credential = DefaultAzureCredential()
    agent_factory = AgentFactory(
        env_file_path=env_file_path,
        client_kwargs={"credential": credential},
    )

    return agent_factory


async def run_local_agent(agent_name: str, user_message: str, timing: TimingEvents, category: str) -> str:
    """Load and run a local agent YAML from the concurrent demo folder."""
    agent_yaml_path = AGENTS_DIR / f"{agent_name}.yaml"
    if not agent_yaml_path.exists():
        raise FileNotFoundError(f"Agent YAML not found: {agent_yaml_path}")

    timing.add(category, "agent_factory.start")
    agent_factory = get_agent_factory()
    timing.add(category, "agent_factory.done")

    timing.add(category, "agent.load.start", agent=agent_yaml_path.name)
    agent = agent_factory.create_agent_from_yaml_path(str(agent_yaml_path))
    timing.add(category, "agent.load.done", agent=agent_yaml_path.name)

    timing.add(category, "agent.run.start", agent=agent_name)
    response = await agent.run(user_message)
    response_text = str(response)
    timing.add(category, "agent.run.done", agent=agent_name, size=len(response_text))
    return response_text


def extract_output(result) -> str:
    """Extract the text output from a declarative workflow run result."""
    outputs = []
    for event in result:
        if getattr(event, "type", None) == "output" and getattr(event, "data", None):
            outputs.append(str(event.data))
    return "\n".join(outputs) if outputs else "No output generated"


def iter_text_deltas(text: str, chunk_size: int = 1):
    """Yield small text chunks so the UI can render incremental output."""
    if chunk_size < 1:
        chunk_size = 1
    for index in range(0, len(text), chunk_size):
        yield text[index:index + chunk_size]


def extract_content_filter_error_details(exception: Exception) -> tuple[str, Any, Any]:
    """Extract reason, content_filters, and the full error payload from a content filter exception."""
    filter_reason = "Azure content filter triggered"
    content_filters_json = None
    error_payload = None

    if hasattr(exception, "body") and isinstance(exception.body, dict):
        error_payload = exception.body
    else:
        error_match = re.search(r"(\{'error':.*\})", str(exception), re.DOTALL)
        if error_match:
            try:
                error_payload = ast.literal_eval(error_match.group(1))
            except (ValueError, SyntaxError):
                error_payload = None

    if isinstance(error_payload, dict):
        content_filters_json = error_payload.get("error", {}).get("content_filters")
        if content_filters_json:
            for content_filter in content_filters_json:
                results = content_filter.get("content_filter_results", {})
                for filter_name, filter_data in results.items():
                    if isinstance(filter_data, dict) and filter_data.get("filtered"):
                        severity = filter_data.get("severity", "unknown")
                        filter_reason = f"Azure content filter: {filter_name} detected (severity: {severity})"
                        return filter_reason, content_filters_json, error_payload

    return filter_reason, content_filters_json, error_payload


async def run_local_workflow(user_message: str, timing: TimingEvents) -> str:
    """Run the purple path from local declarative YAML instead of a hosted Azure workflow."""
    workflow_path = WORKFLOW_DIR / f"{WORKFLOW_NAME}.yaml"
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow YAML not found: {workflow_path}")

    timing.add("purple_workflow", "factory.start")
    factory = get_workflow_factory()
    timing.add("purple_workflow", "factory.done")

    timing.add("purple_workflow", "workflow.load.start", workflow=workflow_path.name)
    workflow = factory.create_workflow_from_yaml_path(str(workflow_path))
    timing.add("purple_workflow", "workflow.load.done", workflow=workflow_path.name)

    timing.add("purple_workflow", "workflow.run.start")
    try:
        result = await workflow.run(user_message)
        timing.add("purple_workflow", "workflow.run.done")

        response_text = extract_output(result)
        timing.add("purple_workflow", "output.done", size=len(response_text))
        return response_text
    except Exception as exc:
        if "PowerFx is not available" not in str(exc):
            raise

        timing.add("purple_workflow", "workflow.run.fallback", reason="powerfx_unavailable")
        workflow_definition = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        actions = workflow_definition.get("trigger", {}).get("actions", [])
        invoke_action = next(
            (action for action in actions if action.get("kind") == "InvokeAzureAgent"),
            None,
        )
        if not invoke_action:
            raise ValueError(f"Workflow does not contain an InvokeAzureAgent action: {workflow_path.name}")

        agent_name = invoke_action.get("agent", {}).get("name")
        if not agent_name:
            raise ValueError(f"Workflow action does not define an agent name: {workflow_path.name}")

        response_text = await run_local_agent(agent_name, user_message, timing, "purple_workflow")
        timing.add("purple_workflow", "output.done", size=len(response_text), fallback_agent=agent_name)
        return response_text


def call_guardrail_sync(user_message: str, timing: TimingEvents) -> Dict[str, Any]:
    """
    Call the blue-guardrail agent synchronously (non-streaming).
    Stateless - only sends the current message with no history.
    Returns the guardrail response with guardrailPassed and reason.
    """
    timing.add("blue_guardrail", "start")
    
    try:
        response_text = asyncio.run(run_local_agent(GUARDRAIL_AGENT_NAME, user_message, timing, "blue_guardrail"))
        response_text = response_text.strip()
        if response_text.startswith("```"):
            response_text = response_text.strip("`")
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        print(f"[GUARDRAIL] Raw response: {response_text}")
        
        try:
            result = json.loads(response_text)
            print(f"[GUARDRAIL] Parsed result: {result}")
            print(f"[GUARDRAIL] guardrailPassed = {result.get('guardrailPassed', True)}")
            timing.add("blue_guardrail", "done", result="passed" if result.get("guardrailPassed", True) else "failed")
            return result
        except json.JSONDecodeError as json_err:
            print(f"[GUARDRAIL] JSON decode error: {json_err}")
            
            # Try to parse manually if JSON is malformed (e.g., missing comma)
            # Look for "guardrailPassed":false or "guardrailPassed": false
            import re
            passed_match = re.search(r'"guardrailPassed"\s*:\s*(true|false)', response_text, re.IGNORECASE)
            reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', response_text)
            
            if passed_match:
                passed = passed_match.group(1).lower() == 'true'
                reason = reason_match.group(1) if reason_match else "Malformed JSON response"
                print(f"[GUARDRAIL] Manually parsed: guardrailPassed={passed}, reason={reason}")
                timing.add("blue_guardrail", "done", result="passed" if passed else "failed", manual_parse=True)
                return {"guardrailPassed": passed, "reason": reason, "raw_response": response_text}
            else:
                # If we can't parse at all, default to blocking for safety
                print(f"[GUARDRAIL] Could not parse guardrailPassed - defaulting to BLOCK for safety")
                timing.add("blue_guardrail", "done", result="failed", parse_error=True)
                return {"guardrailPassed": False, "reason": f"Guardrail response parse error: {json_err}", "raw_response": response_text}
                
    except Exception as e:
        timing.add("blue_guardrail", "error", error=str(e)[:100])
        
        # Check if this is a content filter exception (Azure's built-in safety)
        error_str = str(e)
        
        if "content_filter" in error_str or "content_management_policy" in error_str:
            filter_reason, content_filters_json, error_payload = extract_content_filter_error_details(e)
            
            print(f"[GUARDRAIL] Content filter triggered - blocking request")
            return {
                "guardrailPassed": False, 
                "reason": filter_reason, 
                "azure_content_filter": True,
                "content_filters": content_filters_json,
                "error_payload": error_payload,
            }
        
        # On other errors, let the request through but log the error
        return {"guardrailPassed": True, "reason": f"Guardrail error: {str(e)}"}


def stream_workflow_response(user_message: str, queue: Queue, timing: TimingEvents):
    """
    Run the local declarative workflow and emit the same queue protocol the UI expects.
    Puts events into a queue for async consumption.
    """
    timing.add("purple_workflow", "start")
    
    try:
        full_response = asyncio.run(run_local_workflow(user_message, timing))
        queue.put({"type": "message", "start": True})
        if full_response:
            for delta in iter_text_deltas(full_response):
                timing.add("purple_workflow", "TEXT_DELTA", size=len(delta))
                queue.put({"type": "message", "content": delta})
        queue.put({"type": "message", "end": True})
        timing.add("purple_workflow", "TEXT_DONE", size=len(full_response))
        queue.put({"type": "done", "full_response": full_response})
        timing.add("purple_workflow", "done")
    except Exception as e:
        timing.add("purple_workflow", "error", error=str(e)[:100])
        queue.put({"type": "message", "start": True})
        queue.put({"type": "message", "content": f"Error: {str(e)}"})
        queue.put({"type": "message", "end": True})
        queue.put({"type": "done", "full_response": f"Error: {str(e)}"})
    finally:
        queue.put(None)  # Signal end of stream


async def chat_with_workflow_and_guardrail(messages: List[Dict[str, str]], timing: TimingEvents):
    """
    Send messages to the local declarative workflow while concurrently checking guardrail.
    Buffers workflow events until guardrail responds.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        timing: TimingEvents object to record timings
    
    Yields:
        Response chunks (or guardrail hit message)
    """
    # Get the last user message
    user_message = messages[-1]["content"] if messages else ""
    
    # Create a queue for workflow events
    workflow_queue = Queue()
    
    # Buffer for holding events until guardrail responds
    event_buffer = []
    timing.add("request", "buffer.start")
    
    # Start workflow streaming in a thread
    workflow_thread = threading.Thread(target=stream_workflow_response, args=(user_message, workflow_queue, timing))
    workflow_thread.start()
    
    # Start guardrail check concurrently
    loop = asyncio.get_event_loop()
    guardrail_future = loop.run_in_executor(
        executor, 
        call_guardrail_sync, 
        user_message,
        timing
    )
    
    # Wait for guardrail response while buffering workflow events
    guardrail_result = None
    workflow_done = False
    
    while guardrail_result is None:
        # Check if guardrail is done
        if guardrail_future.done():
            guardrail_result = await guardrail_future
            timing.add("request", "buffer.release", events_buffered=len(event_buffer))
            break
        
        # Try to get workflow events (non-blocking)
        try:
            event = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: workflow_queue.get(timeout=0.1)),
                timeout=0.2
            )
            if event is None:
                workflow_done = True
            else:
                event_buffer.append(event)
        except (asyncio.TimeoutError, Exception):
            pass
        
        # Small delay to prevent busy waiting
        await asyncio.sleep(0.01)
    
    # Record guardrail result
    timing.guardrail_passed = guardrail_result.get("guardrailPassed", True)
    timing.guardrail_reason = guardrail_result.get("reason", "")
    timing.content_filters = guardrail_result.get("content_filters")
    
    # Now we have guardrail result
    if not guardrail_result.get("guardrailPassed", True):
        timing.add("request", "guardrail.blocked")
        
        # Wait for the local workflow to finish so the worker thread exits cleanly.
        print("[GUARDRAIL] Blocked - waiting for local workflow thread to finish")
        if not workflow_done:
            while True:
                event = await loop.run_in_executor(None, workflow_queue.get)
                if event is None:
                    break
        
        # Guardrail failed - send guardrail hit message with content filters
        content_filters = guardrail_result.get("content_filters")
        error_payload = guardrail_result.get("error_payload")
        message = "Guardrail Hit :(\n\n"
        message += f"**Reason:** {guardrail_result.get('reason', 'Unknown')}\n\n"
        if error_payload:
            message += "**Full Response JSON:**\n```json\n"
            message += json.dumps(error_payload, indent=2)
            message += "\n```\n\n"
        elif content_filters:
            message += "**Content Filters:**\n```json\n"
            message += json.dumps(content_filters, indent=2)
            message += "\n```"
        
        yield {"type": "message", "start": True}
        yield {"type": "message", "content": message}
        yield {"type": "message", "end": True}
        yield {"type": "done", "full_response": message, "guardrail": guardrail_result}
        
        # Clean up the workflow thread
        workflow_thread.join(timeout=1)
        return
    
    timing.add("request", "streaming.start")
    
    # Guardrail passed - send all buffered events
    for event in event_buffer:
        if event["type"] != "done":
            yield event
        else:
            # Don't yield done yet, there might be more events
            pass
    
    # Continue streaming remaining workflow events
    if not workflow_done:
        while True:
            event = await loop.run_in_executor(None, workflow_queue.get)
            if event is None:
                break
            yield event
    
    timing.add("request", "done")
    workflow_thread.join()


@app.get("/")
def root():
    return FileResponse("index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/set-instructions")
def set_instructions(instructions: str = ""):
    """Set the system instructions for the assistant."""
    global system_instructions
    system_instructions = instructions if instructions else "You are a helpful assistant."
    return {"status": "success", "instructions": system_instructions}


@app.get("/chat")
async def chat_endpoint(msg: str):
    """Chat endpoint that streams responses from a local declarative workflow with concurrent guardrail check."""
    global conversation_history, timing_logs
    
    # Create timing data for this request
    request_id = f"{datetime.now().isoformat()}_{len(timing_logs)}"
    timing = TimingEvents(request_id=request_id)
    timing.add("request", "start")
    
    # Add user message to history
    conversation_history.append({"role": "user", "content": msg})
    
    # Build messages list with system instructions
    messages = [{"role": "system", "content": system_instructions}] + conversation_history
    
    async def event_stream():
        full_response = ""
        async for result in chat_with_workflow_and_guardrail(messages, timing):
            if result["type"] == "done":
                full_response = result["full_response"]
            else:
                yield f"data: {json.dumps(result)}\n\n"
        
        # Add assistant response to local history
        conversation_history.append({"role": "assistant", "content": full_response})
        
        # Store timing data
        timing_logs.append(timing.to_dict())
        
        # Send done event with timing data
        yield f"data: {json.dumps({'type': 'done', 'timing': timing.to_dict()})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/history")
def history_endpoint():
    """Return the conversation history."""
    return {"history": conversation_history, "instructions": system_instructions}


@app.get("/clear")
def clear_endpoint():
    """Clear the local conversation history."""
    global conversation_history
    conversation_history = []
    return {"status": "success", "message": "History cleared"}


@app.get("/timings")
def timings_endpoint():
    """Return all timing logs for visualization."""
    return {"timings": timing_logs}


@app.get("/timings/latest")
def latest_timing_endpoint():
    """Return the latest timing log."""
    if timing_logs:
        return {"timing": timing_logs[-1]}
    return {"timing": None}


@app.get("/timings/clear")
def clear_timings_endpoint():
    """Clear all timing logs."""
    global timing_logs
    timing_logs = []
    return {"status": "success", "message": "Timing logs cleared"}


@app.get("/timings/chart")
def timings_chart_data():
    """
    Return timing data formatted for chart visualization.
    The new format is just a list of events.
    """
    return {"timings": timing_logs}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)