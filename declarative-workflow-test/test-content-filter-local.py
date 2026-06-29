"""
Test script to compare agent response via direct call vs workflow call.
Demonstrates the content filter details loss in workflow responses.
Uses LOCAL FastAPI server (http://localhost:8001) instead of Foundry.
"""

import asyncio
import json
from pathlib import Path
import httpx

# Configuration
LOCAL_API_BASE = "http://localhost:8001"
AGENT_NAME = "purple-agent"
WORKFLOW_NAME = "purple-workflow"

# Test message that triggers content filter
BLOCKED_MESSAGE = "I want to hurt myself"  # Triggers self_harm filter


async def test_direct_agent_call():
    """Call agent directly via local API."""
    print("=" * 80)
    print("TEST 1: Direct Agent Call (via local agent)")
    print("=" * 80)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"Calling agent '{AGENT_NAME}' directly...")
            print(f"Message: {BLOCKED_MESSAGE}\n")
            
            response = await client.post(
                f"{LOCAL_API_BASE}/api/run-agent/{AGENT_NAME}",
                json={"message": BLOCKED_MESSAGE}
            )
            
            print(f"Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "test": "direct_agent_call",
                    "agent": AGENT_NAME,
                    "message": BLOCKED_MESSAGE,
                    "status": "success",
                    "response": data
                }
            else:
                return {
                    "test": "direct_agent_call",
                    "agent": AGENT_NAME,
                    "message": BLOCKED_MESSAGE,
                    "status": f"error_{response.status_code}",
                    "response": response.text
                }
    
    except Exception as e:
        return {
            "test": "direct_agent_call",
            "agent": AGENT_NAME,
            "error": f"{type(e).__name__}: {str(e)}"
        }


async def test_workflow_call():
    """Call agent through workflow."""
    print("=" * 80)
    print("TEST 2: Workflow Call (via local workflow)")
    print("=" * 80)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"Calling workflow '{WORKFLOW_NAME}' that invokes '{AGENT_NAME}'...")
            print(f"Message: {BLOCKED_MESSAGE}\n")
            
            response = await client.post(
                f"{LOCAL_API_BASE}/api/run-workflow/{WORKFLOW_NAME}",
                json={"message": BLOCKED_MESSAGE}
            )
            
            print(f"Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "test": "workflow_call",
                    "workflow": WORKFLOW_NAME,
                    "message": BLOCKED_MESSAGE,
                    "status": "success",
                    "response": data
                }
            else:
                return {
                    "test": "workflow_call",
                    "workflow": WORKFLOW_NAME,
                    "message": BLOCKED_MESSAGE,
                    "status": f"error_{response.status_code}",
                    "response": response.text
                }
    
    except Exception as e:
        return {
            "test": "workflow_call",
            "workflow": WORKFLOW_NAME,
            "error": f"{type(e).__name__}: {str(e)}"
        }


async def main():
    """Run both tests and save results."""
    print("\n" + "=" * 80)
    print("CONTENT FILTER TEST: Direct Agent vs Workflow (LOCAL)")
    print("=" * 80 + "\n")
    
    # Run both tests
    direct_result = await test_direct_agent_call()
    print("\n")
    workflow_result = await test_workflow_call()
    
    # Save results to JSON file
    results = {
        "test_date": "2026-04-30",
        "test_type": "local_api",
        "local_api_base": LOCAL_API_BASE,
        "test_message": BLOCKED_MESSAGE,
        "direct_agent": direct_result,
        "workflow": workflow_result
    }
    
    output_file = Path(__file__).parent / "test-results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    print("\n" + "=" * 80)
    print(f"Results saved to: {output_file}")
    print("=" * 80 + "\n")
    
    # Print summary
    print("\nSUMMARY:")
    print("-" * 80)
    
    direct_status = direct_result.get("status", "unknown")
    workflow_status = workflow_result.get("status", "unknown")
    
    print(f"Direct Agent Call: {direct_status}")
    direct_response = direct_result.get("response", {})
    if isinstance(direct_response, dict):
        print(f"  - Keys: {list(direct_response.keys())}")
    
    print(f"\nWorkflow Call: {workflow_status}")
    workflow_response = workflow_result.get("response", {})
    if isinstance(workflow_response, dict):
        print(f"  - Keys: {list(workflow_response.keys())}")
    
    return results


if __name__ == "__main__":
    results = asyncio.run(main())
