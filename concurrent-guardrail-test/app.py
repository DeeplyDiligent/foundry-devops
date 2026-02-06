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
    "https://aisvcgu5v.services.ai.azure.com/api/projects/projectgu5v"
)

# Workflow to use
WORKFLOW_NAME = os.environ.get("WORKFLOW_NAME", "purple-workflow")
WORKFLOW_VERSION = os.environ.get("WORKFLOW_VERSION", "1")

# Guardrail agent name
GUARDRAIL_AGENT_NAME = os.environ.get("GUARDRAIL_AGENT_NAME", "guardrail-agent")

# Global state
conversation_history: List[Dict[str, str]] = []
system_instructions: str = "You are a helpful assistant."
# Store conversation IDs for multi-turn conversations (separate for guardrail and workflow)
guardrail_conversation_id: str = None
workflow_conversation_id: str = None

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
            # Extract the filter reason
            filter_reason = "Azure content filter triggered"
            content_filters_json = None
            
            # Try to extract the content_filters from the exception body
            try:
                if hasattr(e, 'body') and isinstance(e.body, dict):
                    content_filters_json = e.body.get('content_filters')
                    
                    # Parse the actual filter results to determine reason
                    if content_filters_json:
                        for cf in content_filters_json:
                            results = cf.get('content_filter_results', {})
                            for filter_name, filter_data in results.items():
                                if isinstance(filter_data, dict) and filter_data.get('filtered'):
                                    severity = filter_data.get('severity', 'unknown')
                                    filter_reason = f"Azure content filter: {filter_name} detected (severity: {severity})"
                                    break
            except:
                pass
            
            print(f"[GUARDRAIL] Content filter triggered - blocking request")
            return {
                "guardrailPassed": False, 
                "reason": filter_reason, 
                "azure_content_filter": True,
                "content_filters": content_filters_json
            }
        
        # On other errors, let the request through but log the error
        return {"guardrailPassed": True, "reason": f"Guardrail error: {str(e)}"}


def stream_workflow_response(user_message: str, conversation_id: str, queue: Queue, timing: TimingEvents):
    """
    Stream response from Azure AI Workflow.
    Puts events into a queue for async consumption.
    Uses persistent conversation ID for multi-turn context.
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
            
            # Stream the response using persistent conversation
            print(f"[WORKFLOW] Using conversation: {conversation_id}")
            timing.add("purple_workflow", "responses.create.start")
            stream = openai_client.responses.create(
                conversation=conversation_id,
                extra_body={"agent": {"name": WORKFLOW_NAME, "type": "agent_reference"}},
                input=user_message,
                stream=True,
                metadata={"x-ms-debug-mode-enabled": "1"},
            )
            
            full_response = ""
            started = False
            message_ids = []  # Track message IDs from the response
            
            for event in stream:
                # Get event type as string
                event_type_str = str(event.type) if hasattr(event, 'type') else 'unknown'
                # Simplify event type name
                event_name = event_type_str.replace("ResponseStreamEventType.", "")
                
                # Debug: Print full event structure
                print(f"[WORKFLOW] Event: {event_name}, has item: {hasattr(event, 'item')}, has id: {hasattr(event, 'id')}")
                if hasattr(event, 'item'):
                    print(f"[WORKFLOW]   Item type: {getattr(event.item, 'type', 'N/A')}, Item id: {getattr(event.item, 'id', 'N/A')}")
                    if hasattr(event.item, 'id') and event.item.id:
                        message_ids.append(event.item.id)
                        print(f"[WORKFLOW]   Captured message ID: {event.item.id}")
                
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
            
            print(f"[WORKFLOW] Stream complete. Collected message IDs: {message_ids}")
            
            # Deduplicate message IDs while preserving order
            unique_message_ids = list(dict.fromkeys(message_ids))
            print(f"[WORKFLOW] Unique message IDs: {unique_message_ids}")
            
            if not started:
                queue.put({"type": "message", "start": True})
            
            queue.put({"type": "message", "end": True})
            queue.put({"type": "done", "full_response": full_response, "message_ids": unique_message_ids})
            
    except Exception as e:
        timing.add("purple_workflow", "error", error=str(e)[:100])
        queue.put({"type": "message", "start": True})
        queue.put({"type": "message", "content": f"Error: {str(e)}"})
        queue.put({"type": "message", "end": True})
        queue.put({"type": "done", "full_response": f"Error: {str(e)}"})
    finally:
        queue.put(None)  # Signal end of stream


async def chat_with_workflow_and_guardrail(messages: List[Dict[str, str]], guardrail_conv_id: str, workflow_conv_id: str, timing: TimingEvents):
    """
    Send messages to Azure AI Workflow while concurrently checking guardrail.
    Buffers workflow events until guardrail responds.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        guardrail_conv_id: Conversation ID for guardrail context
        workflow_conv_id: Conversation ID for workflow context
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
    workflow_thread = threading.Thread(target=stream_workflow_response, args=(user_message, workflow_conv_id, workflow_queue, timing))
    workflow_thread.start()
    
    # Start guardrail check concurrently
    loop = asyncio.get_event_loop()
    guardrail_future = loop.run_in_executor(
        executor, 
        call_guardrail_sync, 
        user_message,
        guardrail_conv_id,
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
        
        # Wait for workflow to complete and collect all remaining events (including message IDs)
        print(f"[GUARDRAIL] Blocked - waiting for workflow to complete so we can clean up")
        workflow_message_ids = []
        if not workflow_done:
            while True:
                event = await loop.run_in_executor(None, workflow_queue.get)
                if event is None:
                    break
                if event["type"] == "done" and "message_ids" in event:
                    workflow_message_ids = event["message_ids"]
                    print(f"[GUARDRAIL] Workflow completed with message IDs: {workflow_message_ids}")
        
        # Delete the workflow messages from the conversation
        if workflow_message_ids:
            timing.add("request", "cleanup.start", message_count=len(workflow_message_ids))
            print(f"[GUARDRAIL] Deleting {len(workflow_message_ids)} messages from workflow conversation {workflow_conv_id}")
            try:
                credential = DefaultAzureCredential()
                project_client = AIProjectClient(endpoint=AZURE_PROJECT_ENDPOINT, credential=credential)
                with project_client:
                    openai_client = project_client.get_openai_client()
                    for msg_id in workflow_message_ids:
                        try:
                            print(f"[GUARDRAIL] Deleting message: {msg_id}")
                            timing.add("request", "cleanup.delete", message_id=msg_id)
                            # item_id is positional, conversation_id is keyword-only
                            result = openai_client.conversations.items.delete(
                                msg_id,
                                conversation_id=workflow_conv_id
                            )
                            print(f"[GUARDRAIL] Deleted message {msg_id} successfully")
                            timing.add("request", "cleanup.delete.success", message_id=msg_id)
                        except Exception as delete_error:
                            print(f"[GUARDRAIL] Failed to delete message {msg_id}: {delete_error}")
                            timing.add("request", "cleanup.delete.error", message_id=msg_id, error=str(delete_error)[:100])
                timing.add("request", "cleanup.done")
            except Exception as cleanup_error:
                print(f"[GUARDRAIL] Cleanup error: {cleanup_error}")
                timing.add("request", "cleanup.error", error=str(cleanup_error)[:100])
        
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
    global conversation_history, timing_logs, guardrail_conversation_id, workflow_conversation_id
    
    # Create timing data for this request
    request_id = f"{datetime.now().isoformat()}_{len(timing_logs)}"
    timing = TimingEvents(request_id=request_id)
    timing.add("request", "start")
    
    # Create or reuse conversations for guardrail and workflow (in parallel if both need creation)
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=AZURE_PROJECT_ENDPOINT, credential=credential)
    
    with project_client:
        openai_client = project_client.get_openai_client()
        
        needs_guardrail = guardrail_conversation_id is None
        needs_workflow = workflow_conversation_id is None
        
        if needs_guardrail or needs_workflow:
            loop = asyncio.get_event_loop()
            
            async def create_conversations():
                global guardrail_conversation_id, workflow_conversation_id
                futures = []
                
                if needs_guardrail:
                    timing.add("request", "guardrail_conversation.create.start")
                    futures.append(("guardrail", loop.run_in_executor(executor, openai_client.conversations.create)))
                
                if needs_workflow:
                    timing.add("request", "workflow_conversation.create.start")
                    futures.append(("workflow", loop.run_in_executor(executor, openai_client.conversations.create)))
                
                for name, future in futures:
                    conversation = await future
                    if name == "guardrail":
                        guardrail_conversation_id = conversation.id
                        timing.add("request", "guardrail_conversation.create.done", conversation_id=guardrail_conversation_id)
                        print(f"[CONVERSATION] Created guardrail conversation: {guardrail_conversation_id}")
                    else:
                        workflow_conversation_id = conversation.id
                        timing.add("request", "workflow_conversation.create.done", conversation_id=workflow_conversation_id)
                        print(f"[CONVERSATION] Created workflow conversation: {workflow_conversation_id}")
            
            await create_conversations()
        else:
            print(f"[CONVERSATION] Reusing guardrail conversation: {guardrail_conversation_id}")
            print(f"[CONVERSATION] Reusing workflow conversation: {workflow_conversation_id}")
    
    # Add user message to history
    conversation_history.append({"role": "user", "content": msg})
    
    # Build messages list with system instructions
    messages = [{"role": "system", "content": system_instructions}] + conversation_history
    
    async def event_stream():
        full_response = ""
        async for result in chat_with_workflow_and_guardrail(messages, guardrail_conversation_id, workflow_conversation_id, timing):
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
        
        # Add the assistant response to the guardrail conversation for context (after yielding done)
        try:
            credential = DefaultAzureCredential()
            project_client = AIProjectClient(endpoint=AZURE_PROJECT_ENDPOINT, credential=credential)
            with project_client:
                openai_client = project_client.get_openai_client()
                # Add assistant response to guardrail conversation (user message already there from guardrail call)
                openai_client.conversations.items.create(
                    guardrail_conversation_id,
                    items=[
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": full_response,
                        }
                    ],
                )
                print(f"[CONVERSATION] Added assistant response to guardrail conversation {guardrail_conversation_id}")
        except Exception as e:
            print(f"[CONVERSATION] Failed to add to guardrail conversation: {e}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/history")
def history_endpoint():
    """Return the conversation history."""
    return {"history": conversation_history, "instructions": system_instructions}


@app.get("/clear")
def clear_endpoint():
    """Clear the conversation history and reset both conversation IDs."""
    global conversation_history, guardrail_conversation_id, workflow_conversation_id
    conversation_history = []
    guardrail_conversation_id = None
    workflow_conversation_id = None
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