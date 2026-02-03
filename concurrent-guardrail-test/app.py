from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
import json
import asyncio
import os
import time
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import threading
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


def call_guardrail_sync(user_message: str, conversation_id: str, timing: TimingEvents) -> Dict[str, Any]:
    """
    Call the blue-guardrail agent synchronously (non-streaming).
    Uses conversation ID to provide context instead of passing full history.
    Returns the guardrail response with guardrailPassed and reason.
    """
    timing.add("blue_guardrail", "start")
    
    try:
        timing.add("blue_guardrail", "credential.start")
        credential = DefaultAzureCredential()
        timing.add("blue_guardrail", "credential.done")
        
        timing.add("blue_guardrail", "client.start")
        project_client = AIProjectClient(
            endpoint=AZURE_PROJECT_ENDPOINT,
            credential=credential,
        )
        timing.add("blue_guardrail", "client.done")
        
        with project_client:
            timing.add("blue_guardrail", "openai_client.start")
            openai_client = project_client.get_openai_client()
            timing.add("blue_guardrail", "openai_client.done")
            
            try:
                timing.add("blue_guardrail", "responses.create.start")
                response = openai_client.responses.create(
                    input=[{"role": "user", "content": user_message}],
                    conversation=conversation_id,
                    extra_body={"agent": {"name": GUARDRAIL_AGENT_NAME, "type": "agent_reference"}},
                )
                timing.add("blue_guardrail", "responses.create.done")
            except Exception as api_error:
                timing.add("blue_guardrail", "responses.create.error", error=str(api_error))
                raise
            
            # Parse the response - expecting JSON with guardrailPassed and reason
            response_text = response.output_text
            
            try:
                result = json.loads(response_text)
                timing.add("blue_guardrail", "done", result="passed" if result.get("guardrailPassed", True) else "failed")
                return result
            except json.JSONDecodeError:
                timing.add("blue_guardrail", "done", result="passed")
                return {"guardrailPassed": True, "reason": "", "raw_response": response_text}
                
    except Exception as e:
        timing.add("blue_guardrail", "error", error=str(e)[:100])
        
        # Check if this is a content filter exception (Azure's built-in safety)
        error_str = str(e)
        
        if "content_filter" in error_str or "content_management_policy" in error_str:
            # Extract the filter reason
            filter_reason = "Azure content filter triggered"
            content_filters_json = None
            
            # Try to extract the content_filters from the exception body
            try:
                if hasattr(e, 'body') and isinstance(e.body, dict):
                    content_filters_json = e.body.get('content_filters')
            except:
                pass
            
            if "self_harm" in error_str:
                filter_reason = "Azure content filter: self-harm detected"
            elif "violence" in error_str and "'filtered': True" in error_str:
                filter_reason = "Azure content filter: violence detected"
            elif "hate" in error_str and "'filtered': True" in error_str:
                filter_reason = "Azure content filter: hate speech detected"
            elif "sexual" in error_str and "'filtered': True" in error_str:
                filter_reason = "Azure content filter: sexual content detected"
            
            print(f"[GUARDRAIL] Content filter triggered - blocking request")
            return {
                "guardrailPassed": False, 
                "reason": filter_reason, 
                "azure_content_filter": True,
                "content_filters": content_filters_json
            }
        
        # On other errors, let the request through but log the error
        return {"guardrailPassed": True, "reason": f"Guardrail error: {str(e)}"}


def stream_workflow_response(user_message: str, queue: Queue, timing: TimingEvents):
    """
    Stream response from Azure AI Workflow.
    Puts events into a queue for async consumption.
    """
    timing.add("purple_workflow", "start")
    
    try:
        timing.add("purple_workflow", "credential.start")
        credential = DefaultAzureCredential()
        timing.add("purple_workflow", "credential.done")
        
        timing.add("purple_workflow", "client.start")
        project_client = AIProjectClient(
            endpoint=AZURE_PROJECT_ENDPOINT,
            credential=credential,
        )
        timing.add("purple_workflow", "client.done")
        
        with project_client:
            timing.add("purple_workflow", "openai_client.start")
            openai_client = project_client.get_openai_client()
            timing.add("purple_workflow", "openai_client.done")
            
            # Create a new conversation for this chat
            timing.add("purple_workflow", "conversation.create.start")
            conversation = openai_client.conversations.create()
            timing.add("purple_workflow", "conversation.create.done")
            
            # Stream the response
            timing.add("purple_workflow", "responses.create.start")
            stream = openai_client.responses.create(
                conversation=conversation.id,
                extra_body={"agent": {"name": WORKFLOW_NAME, "type": "agent_reference"}},
                input=user_message,
                stream=True,
                metadata={"x-ms-debug-mode-enabled": "1"},
            )
            
            full_response = ""
            started = False
            
            for event in stream:
                # Get event type as string
                event_type_str = str(event.type) if hasattr(event, 'type') else 'unknown'
                # Simplify event type name
                event_name = event_type_str.replace("ResponseStreamEventType.", "")
                
                # Add extra info based on event type
                extra = {}
                if event.type == ResponseStreamEventType.RESPONSE_OUTPUT_TEXT_DELTA:
                    extra["size"] = len(event.delta)
                    if not started:
                        queue.put({"type": "message", "start": True})
                        started = True
                    full_response += event.delta
                    queue.put({"type": "message", "content": event.delta})
                elif event.type == ResponseStreamEventType.RESPONSE_OUTPUT_ITEM_ADDED:
                    if hasattr(event, 'item') and hasattr(event.item, 'type'):
                        extra["item_type"] = event.item.type
                        if event.item.type == "workflow_action" and hasattr(event.item, 'action_id'):
                            extra["action_id"] = event.item.action_id
                elif event.type == ResponseStreamEventType.RESPONSE_OUTPUT_ITEM_DONE:
                    if hasattr(event, 'item') and hasattr(event.item, 'type'):
                        extra["item_type"] = event.item.type
                        if hasattr(event.item, 'status'):
                            extra["status"] = event.item.status
                
                timing.add("purple_workflow", event_name, **extra)
            
            timing.add("purple_workflow", "done")
            
            if not started:
                queue.put({"type": "message", "start": True})
            
            queue.put({"type": "message", "end": True})
            queue.put({"type": "done", "full_response": full_response})
            
    except Exception as e:
        timing.add("purple_workflow", "error", error=str(e)[:100])
        queue.put({"type": "message", "start": True})
        queue.put({"type": "message", "content": f"Error: {str(e)}"})
        queue.put({"type": "message", "end": True})
        queue.put({"type": "done", "full_response": f"Error: {str(e)}"})
    finally:
        queue.put(None)  # Signal end of stream


async def chat_with_workflow_and_guardrail(messages: List[Dict[str, str]], conversation_id: str, timing: TimingEvents):
    """
    Send messages to Azure AI Workflow while concurrently checking guardrail.
    Buffers workflow events until guardrail responds.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        conversation_id: Conversation ID to provide context to guardrail
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
        conversation_id,
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
        # Guardrail failed - send guardrail hit message with content filters
        content_filters = guardrail_result.get("content_filters")
        message = "Guardrail Hit :(\n\n"
        message += f"**Reason:** {guardrail_result.get('reason', 'Unknown')}\n\n"
        if content_filters:
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
    """Chat endpoint that streams responses from Azure AI Workflow with concurrent guardrail check."""
    global conversation_history, timing_logs, current_conversation_id
    
    # Create timing data for this request
    request_id = f"{datetime.now().isoformat()}_{len(timing_logs)}"
    timing = TimingEvents(request_id=request_id)
    timing.add("request", "start")
    
    # Create or reuse conversation for context
    if current_conversation_id is None:
        timing.add("request", "conversation.create.start")
        credential = DefaultAzureCredential()
        project_client = AIProjectClient(endpoint=AZURE_PROJECT_ENDPOINT, credential=credential)
        with project_client:
            openai_client = project_client.get_openai_client()
            conversation = openai_client.conversations.create()
            current_conversation_id = conversation.id
        timing.add("request", "conversation.create.done", conversation_id=current_conversation_id)
        print(f"[CONVERSATION] Created new conversation: {current_conversation_id}")
    else:
        print(f"[CONVERSATION] Reusing conversation: {current_conversation_id}")
    
    # Add user message to history
    conversation_history.append({"role": "user", "content": msg})
    
    # Build messages list with system instructions
    messages = [{"role": "system", "content": system_instructions}] + conversation_history
    
    async def event_stream():
        full_response = ""
        async for result in chat_with_workflow_and_guardrail(messages, current_conversation_id, timing):
            if result["type"] == "done":
                full_response = result["full_response"]
            else:
                yield f"data: {json.dumps(result)}\n\n"
        
        # Add assistant response to local history
        conversation_history.append({"role": "assistant", "content": full_response})
        
        # Add the exchange to the Azure conversation for guardrail context
        try:
            credential = DefaultAzureCredential()
            project_client = AIProjectClient(endpoint=AZURE_PROJECT_ENDPOINT, credential=credential)
            with project_client:
                openai_client = project_client.get_openai_client()
                # Add user message and assistant response to conversation
                openai_client.responses.create(
                    input=[
                        {"role": "user", "content": msg},
                        {"role": "assistant", "content": full_response}
                    ],
                    conversation=current_conversation_id,
                    extra_body={"agent": {"name": GUARDRAIL_AGENT_NAME, "type": "agent_reference"}},
                )
                print(f"[CONVERSATION] Added exchange to conversation {current_conversation_id}")
        except Exception as e:
            print(f"[CONVERSATION] Failed to add to conversation: {e}")
        
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
    """Clear the conversation history and reset conversation ID."""
    global conversation_history, current_conversation_id
    conversation_history = []
    current_conversation_id = None
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