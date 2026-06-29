"""
Local test API for running Agent Framework declarative workflows.
Uses the purple-workflow.yaml as an example.

Requires env var: FOUNDRY_PROJECT_ENDPOINT (or set in .env file)
"""

import asyncio
import os
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_framework.declarative import WorkflowFactory
from agent_framework_declarative import AgentFactory
from agent_framework import WorkflowRunResult
from azure.identity import DefaultAzureCredential

app = FastAPI(title="Declarative Workflow Test")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
FOUNDRY_PROJECT_ENDPOINT = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://oai-resource-aueast.services.ai.azure.com/api/projects/oai-project-aueast",
)
WORKFLOW_DIR = Path(__file__).parent / "workflows"
AGENTS_DIR = Path(__file__).parent / "agents"  # Declarative-format agents
ENV_FILE = Path(__file__).parent.parent / ".env"


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: Optional[str] = None


def get_workflow_factory() -> WorkflowFactory:
    """
    Create a WorkflowFactory connected to the Foundry project.
    
    The AgentFactory uses the 'Foundry' provider by default, which creates 
    a FoundryChatClient using FOUNDRY_PROJECT_ENDPOINT env var.
    Agents referenced in workflow YAML are loaded from agents/ directory.
    """
    # Set env var for FoundryChatClient if not already set
    if "FOUNDRY_PROJECT_ENDPOINT" not in os.environ:
        os.environ["FOUNDRY_PROJECT_ENDPOINT"] = FOUNDRY_PROJECT_ENDPOINT

    env_file_path = str(ENV_FILE) if ENV_FILE.exists() else None

    # Create agent factory - uses Foundry provider by default
    # Pass credential so FoundryChatClient can authenticate
    credential = DefaultAzureCredential()
    agent_factory = AgentFactory(
        env_file_path=env_file_path,
        client_kwargs={"credential": credential},
    )

    # Pre-load agents from YAML files in the agents/ directory
    agents = {}
    if AGENTS_DIR.exists():
        for agent_yaml in AGENTS_DIR.glob("*.yaml"):
            try:
                agent = agent_factory.create_agent_from_yaml_path(str(agent_yaml))
                agents[agent.name] = agent
            except Exception as e:
                print(f"Warning: Could not load agent from {agent_yaml.name}: {e}")

    factory = WorkflowFactory(
        agent_factory=agent_factory,
        agents=agents,
        env_file=env_file_path,
    )
    return factory


def extract_output(result: WorkflowRunResult) -> str:
    """Extract the text output from workflow events."""
    outputs = []
    for event in result:
        if event.type == "output" and event.data:
            outputs.append(str(event.data))
    return "\n".join(outputs) if outputs else "No output generated"


@app.get("/")
async def index():
    """Serve the chat HTML page."""
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/test-results.html")
async def test_results_page():
    """Serve the test results comparison page."""
    return FileResponse(Path(__file__).parent / "test-results.html")


@app.get("/test-results.json")
async def test_results_json():
    """Serve the test results JSON data."""
    return FileResponse(Path(__file__).parent / "test-results.json")


@app.get("/api/workflows")
async def list_workflows():
    """List available workflow YAML files."""
    workflows = [f.stem for f in WORKFLOW_DIR.glob("*.yaml")]
    return {"workflows": workflows}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Run the purple-workflow declaratively using agent-framework-declarative.
    Sends user message through the workflow and returns the agent response.
    """
    workflow_path = WORKFLOW_DIR / "purple-workflow.yaml"
    if not workflow_path.exists():
        raise HTTPException(status_code=404, detail="purple-workflow.yaml not found")

    try:
        factory = get_workflow_factory()
        workflow = factory.create_workflow_from_yaml_path(str(workflow_path))

        # Pass user message as string - becomes System.LastMessage in the workflow
        result = await workflow.run(request.message)

        # Extract output from WorkflowRunResult (list of WorkflowEvent)
        response_text = extract_output(result)

        return ChatResponse(
            response=response_text,
            conversation_id=request.conversation_id,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {e}")


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Stream the workflow response using SSE (Server-Sent Events).
    """
    workflow_path = WORKFLOW_DIR / "purple-workflow.yaml"
    if not workflow_path.exists():
        raise HTTPException(status_code=404, detail="purple-workflow.yaml not found")

    async def event_generator():
        try:
            factory = get_workflow_factory()
            workflow = factory.create_workflow_from_yaml_path(str(workflow_path))

            # Send start event
            yield f"data: {json.dumps({'type': 'start'})}\n\n"

            # Run workflow
            result = await workflow.run(request.message)

            # Extract output
            response_text = extract_output(result)

            yield f"data: {json.dumps({'type': 'message', 'content': response_text})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/run-workflow/{workflow_name}")
async def run_workflow(workflow_name: str, request: ChatRequest):
    """
    Run any workflow by name from the workflows/ directory.
    """
    workflow_path = WORKFLOW_DIR / f"{workflow_name}.yaml"
    if not workflow_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_name}.yaml' not found"
        )
    try:
        factory = get_workflow_factory()
        workflow = factory.create_workflow_from_yaml_path(str(workflow_path))

        # Pass user message as string - becomes System.LastMessage in the workflow
        result = await workflow.run(request.message)

        response_text = extract_output(result)

        return ChatResponse(
            response=response_text,
            conversation_id=request.conversation_id,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {e}")


@app.post("/api/run-agent/{agent_name}")
async def run_agent(agent_name: str, request: ChatRequest):
    """
    Run an agent directly (without workflow).
    Useful for comparing direct agent calls vs workflow calls.
    """
    try:
        factory = get_workflow_factory()

        if agent_name not in factory.agents:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_name}' not found. Available: {list(factory.agents.keys())}",
            )

        agent = factory.agents[agent_name]
        response = await agent.run(request.message)

        return ChatResponse(
            response=str(response),
            conversation_id=request.conversation_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
