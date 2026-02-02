from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
import json
import asyncio
import os
import time
from typing import List, Dict, Any, Generator
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import ResponseStreamEventType

app = FastAPI()

# Thread pool for running sync SDK calls
executor = ThreadPoolExecutor(max_workers=10)

# Configuration - using environment variables with defaults from environments.yaml
AZURE_PROJECT_ENDPOINT = os.environ.get(
    "AZURE_PROJECT_ENDPOINT", 
    "https://sweeden-test.services.ai.azure.com/api/projects/swe-proj"
)

# Workflow to use
WORKFLOW_NAME = os.environ.get("WORKFLOW_NAME", "purple-workflow")
WORKFLOW_VERSION = os.environ.get("WORKFLOW_VERSION", "1")

# Guardrail agent name
GUARDRAIL_AGENT_NAME = os.environ.get("GUARDRAIL_AGENT_NAME", "blue-guardrail")

# Global state
conversation_history: List[Dict[str, str]] = []
system_instructions: str = "You are a helpful assistant."
# Store conversation ID for multi-turn conversations
current_conversation_id: str = None

# Store timing data for visualization
timing_logs: List[Dict[str, Any]] = []


@dataclass
class TimingData:
    """Stores timing information for a single request."""
    request_id: str
    request_start: float = 0
    request_end: float = 0
    
    # Guardrail timings
    guardrail_start: float = 0
    guardrail_end: float = 0
    guardrail_duration_ms: float = 0
    
    # Workflow timings
    workflow_start: float = 0
    workflow_first_chunk: float = 0
    workflow_end: float = 0
    workflow_time_to_first_chunk_ms: float = 0
    workflow_total_duration_ms: float = 0
    
    # Workflow chunk timings - list of (timestamp, chunk_size) tuples
    workflow_chunks: List[Dict[str, Any]] = field(default_factory=list)
    
    # Buffering timings
    buffer_start: float = 0
    buffer_release: float = 0
    buffer_duration_ms: float = 0
    events_buffered: int = 0
    
    # Overall
    total_duration_ms: float = 0
    guardrail_passed: bool = True
    guardrail_reason: str = ""
    
    def to_dict(self):
        return asdict(self)


def call_guardrail_sync(user_message: str, conversation_history: List[Dict[str, str]], timing: TimingData) -> Dict[str, Any]:
    """
    Call the blue-guardrail agent synchronously (non-streaming).
    Returns the guardrail response with guardrailPassed and reason.
    """
    timing.guardrail_start = time.time()
    
    try:
        credential = DefaultAzureCredential()
        project_client = AIProjectClient(
            endpoint=AZURE_PROJECT_ENDPOINT,
            credential=credential,
        )
        
        with project_client:
            # Get the guardrail agent
            agent = project_client.agents.get(agent_name=GUARDRAIL_AGENT_NAME)
            
            openai_client = project_client.get_openai_client()
            
            # Build input with conversation history context
            guardrail_input = f"User message: {user_message}\n\nConversation history: {json.dumps(conversation_history)}"
            
            # Call guardrail (non-streaming)
            response = openai_client.responses.create(
                input=[{"role": "user", "content": guardrail_input}],
                extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
            )
            
            # Parse the response - expecting JSON with guardrailPassed and reason
            response_text = response.output_text
            timing.guardrail_end = time.time()
            timing.guardrail_duration_ms = (timing.guardrail_end - timing.guardrail_start) * 1000
            
            try:
                result = json.loads(response_text)
                return result
            except json.JSONDecodeError:
                # If response isn't valid JSON, assume it passed
                return {"guardrailPassed": True, "reason": "", "raw_response": response_text}
                
    except Exception as e:
        timing.guardrail_end = time.time()
        timing.guardrail_duration_ms = (timing.guardrail_end - timing.guardrail_start) * 1000
        # On error, let the request through but log the error
        return {"guardrailPassed": True, "reason": f"Guardrail error: {str(e)}"}


def stream_workflow_response(user_message: str, queue: Queue, timing: TimingData):
    """
    Stream response from Azure AI Workflow.
    Puts events into a queue for async consumption.
    """
    timing.workflow_start = time.time()
    
    try:
        credential = DefaultAzureCredential()
        project_client = AIProjectClient(
            endpoint=AZURE_PROJECT_ENDPOINT,
            credential=credential,
        )
        
        with project_client:
            workflow = {
                "name": WORKFLOW_NAME,
                "version": WORKFLOW_VERSION,
            }
            
            openai_client = project_client.get_openai_client()
            
            # Create a new conversation for this chat
            conversation = openai_client.conversations.create()
            
            # Stream the response
            stream = openai_client.responses.create(
                conversation=conversation.id,
                extra_body={"agent": {"name": workflow["name"], "type": "agent_reference"}},
                input=user_message,
                stream=True,
                metadata={"x-ms-debug-mode-enabled": "1"},
            )
            
            full_response = ""
            started = False
            first_chunk_recorded = False
            chunk_index = 0
            
            for event in stream:
                event_time = time.time()
                
                # Get event type as string
                event_type_str = str(event.type) if hasattr(event, 'type') else 'unknown'
                
                # Record ALL events for timing visualization
                event_info = {
                    "index": chunk_index,
                    "timestamp": event_time,
                    "time_from_start_ms": (event_time - timing.workflow_start) * 1000,
                    "event_type": event_type_str,
                    "size": 0,
                    "content_preview": ""
                }
                
                if event.type == ResponseStreamEventType.RESPONSE_OUTPUT_TEXT_DELTA:
                    if not started:
                        queue.put({"type": "message", "start": True, "timestamp": event_time})
                        started = True
                    if not first_chunk_recorded:
                        timing.workflow_first_chunk = event_time
                        timing.workflow_time_to_first_chunk_ms = (event_time - timing.workflow_start) * 1000
                        first_chunk_recorded = True
                    
                    # Add text-specific info
                    event_info["size"] = len(event.delta)
                    event_info["content_preview"] = event.delta[:20] if len(event.delta) > 20 else event.delta
                    
                    full_response += event.delta
                    queue.put({"type": "message", "content": event.delta, "timestamp": event_time})
                elif event.type == ResponseStreamEventType.RESPONSE_OUTPUT_TEXT_DONE:
                    # Final text is available
                    pass
                elif event.type == ResponseStreamEventType.RESPONSE_OUTPUT_ITEM_ADDED:
                    if hasattr(event, 'item') and hasattr(event.item, 'type'):
                        event_info["item_type"] = event.item.type
                        if event.item.type == "workflow_action":
                            event_info["action_id"] = event.item.action_id if hasattr(event.item, 'action_id') else ""
                            queue.put({"type": "workflow", "action": "started", "action_id": event.item.action_id, "timestamp": event_time})
                elif event.type == ResponseStreamEventType.RESPONSE_OUTPUT_ITEM_DONE:
                    if hasattr(event, 'item') and hasattr(event.item, 'type'):
                        event_info["item_type"] = event.item.type
                        if event.item.type == "workflow_action":
                            event_info["action_id"] = event.item.action_id if hasattr(event.item, 'action_id') else ""
                            event_info["status"] = event.item.status if hasattr(event.item, 'status') else ""
                            queue.put({"type": "workflow", "action": "done", "action_id": event.item.action_id, "status": event.item.status, "timestamp": event_time})
                
                # Always record the event
                timing.workflow_chunks.append(event_info)
                chunk_index += 1
            
            timing.workflow_end = time.time()
            timing.workflow_total_duration_ms = (timing.workflow_end - timing.workflow_start) * 1000
            
            if not started:
                queue.put({"type": "message", "start": True, "timestamp": time.time()})
            
            queue.put({"type": "message", "end": True, "timestamp": time.time()})
            queue.put({"type": "done", "full_response": full_response, "timestamp": time.time()})
            
    except Exception as e:
        timing.workflow_end = time.time()
        timing.workflow_total_duration_ms = (timing.workflow_end - timing.workflow_start) * 1000
        
        queue.put({"type": "message", "start": True, "timestamp": time.time()})
        queue.put({"type": "message", "content": f"Error: {str(e)}", "timestamp": time.time()})
        queue.put({"type": "message", "end": True, "timestamp": time.time()})
        queue.put({"type": "done", "full_response": f"Error: {str(e)}", "timestamp": time.time()})
    finally:
        queue.put(None)  # Signal end of stream


async def chat_with_workflow_and_guardrail(messages: List[Dict[str, str]], history: List[Dict[str, str]], timing: TimingData):
    """
    Send messages to Azure AI Workflow while concurrently checking guardrail.
    Buffers workflow events until guardrail responds.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        history: Conversation history for guardrail context
        timing: TimingData object to record timings
    
    Yields:
        Response chunks (or guardrail hit message)
    """
    # Get the last user message
    user_message = messages[-1]["content"] if messages else ""
    
    # Create a queue for workflow events
    workflow_queue = Queue()
    
    # Buffer for holding events until guardrail responds
    event_buffer = []
    timing.buffer_start = time.time()
    
    # Start workflow streaming in a thread
    workflow_thread = threading.Thread(target=stream_workflow_response, args=(user_message, workflow_queue, timing))
    workflow_thread.start()
    
    # Start guardrail check concurrently
    loop = asyncio.get_event_loop()
    guardrail_future = loop.run_in_executor(
        executor, 
        call_guardrail_sync, 
        user_message,
        history,
        timing
    )
    
    # Wait for guardrail response while buffering workflow events
    guardrail_result = None
    workflow_done = False
    
    while guardrail_result is None:
        # Check if guardrail is done
        if guardrail_future.done():
            guardrail_result = await guardrail_future
            timing.buffer_release = time.time()
            timing.buffer_duration_ms = (timing.buffer_release - timing.buffer_start) * 1000
            timing.events_buffered = len(event_buffer)
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
    
    # Now we have guardrail result
    if not guardrail_result.get("guardrailPassed", True):
        # Guardrail failed - send guardrail hit message
        yield {"type": "message", "start": True}
        yield {"type": "message", "content": "Guardrail Hit :("}
        yield {"type": "message", "end": True}
        yield {"type": "done", "full_response": "Guardrail Hit :(", "guardrail": guardrail_result}
        
        # Clean up the workflow thread
        workflow_thread.join(timeout=1)
        return
    
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
    """Chat endpoint that streams responses from Azure AI Workflow with concurrent guardrail check."""
    global conversation_history, timing_logs
    
    # Create timing data for this request
    request_id = f"{datetime.now().isoformat()}_{len(timing_logs)}"
    timing = TimingData(request_id=request_id)
    timing.request_start = time.time()
    
    # Add user message to history
    conversation_history.append({"role": "user", "content": msg})
    
    # Build messages list with system instructions
    messages = [{"role": "system", "content": system_instructions}] + conversation_history
    
    async def event_stream():
        full_response = ""
        async for result in chat_with_workflow_and_guardrail(messages, conversation_history, timing):
            if result["type"] == "done":
                full_response = result["full_response"]
            else:
                yield f"data: {json.dumps(result)}\n\n"
        
        # Add assistant response to history
        conversation_history.append({"role": "assistant", "content": full_response})
        
        # Finalize timing
        timing.request_end = time.time()
        timing.total_duration_ms = (timing.request_end - timing.request_start) * 1000
        
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
    """Clear the conversation history."""
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
    Returns data suitable for a Gantt-style chart showing parallel execution.
    """
    chart_data = []
    
    for t in timing_logs:
        base_time = t.get("request_start", 0)
        
        entry = {
            "request_id": t.get("request_id", ""),
            "total_duration_ms": t.get("total_duration_ms", 0),
            "guardrail_passed": t.get("guardrail_passed", True),
            "events_buffered": t.get("events_buffered", 0),
            "components": [
                {
                    "name": "Guardrail (blue-guardrail)",
                    "start_ms": (t.get("guardrail_start", base_time) - base_time) * 1000,
                    "duration_ms": t.get("guardrail_duration_ms", 0),
                    "color": "#3b82f6"  # blue
                },
                {
                    "name": "Workflow (purple-workflow)",
                    "start_ms": (t.get("workflow_start", base_time) - base_time) * 1000,
                    "duration_ms": t.get("workflow_total_duration_ms", 0),
                    "time_to_first_chunk_ms": t.get("workflow_time_to_first_chunk_ms", 0),
                    "color": "#8b5cf6"  # purple
                },
                {
                    "name": "Buffer Wait",
                    "start_ms": (t.get("buffer_start", base_time) - base_time) * 1000,
                    "duration_ms": t.get("buffer_duration_ms", 0),
                    "color": "#f59e0b"  # amber
                }
            ]
        }
        chart_data.append(entry)
    
    return {"chart_data": chart_data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)