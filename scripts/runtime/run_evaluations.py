#!/usr/bin/env python3
"""
Evaluation runner for testing agents and workflows.
"""
import argparse
import json
import os
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient


# Environment configurations
ENVIRONMENTS = {
    "dev": {
        "endpoint": os.getenv("DEV_AZURE_PROJECT_ENDPOINT", "https://sweeden-test.services.ai.azure.com/api/projects/swe-proj-dev"),
    },
    "test": {
        "endpoint": os.getenv("TEST_AZURE_PROJECT_ENDPOINT", "https://sweeden-test.services.ai.azure.com/api/projects/swe-proj-test"),
    },
    "prod": {
        "endpoint": os.getenv("PROD_AZURE_PROJECT_ENDPOINT", "https://sweeden-test.services.ai.azure.com/api/projects/swe-proj-prod"),
    }
}


class EvaluationRunner:
    """Runs evaluations against deployed agents."""
    
    def __init__(self, environment: str, suite: str = "full"):
        self.environment = environment
        self.suite = suite
        self.config = ENVIRONMENTS.get(environment)
        
        if not self.config:
            raise ValueError(f"Unknown environment: {environment}")
        
        self.credential = DefaultAzureCredential()
        self.client = AIProjectClient(
            endpoint=self.config["endpoint"],
            credential=self.credential
        )
        
        # Load evaluation config
        with open("evaluations/config.yaml", 'r') as f:
            self.eval_config = yaml.safe_load(f)
        
        self.results = []
        print(f"✓ Evaluation runner initialized ({environment}, suite: {suite})")
    
    def load_test_cases(self) -> List[Dict[str, Any]]:
        """Load test cases based on suite."""
        suite_config = self.eval_config['suites'].get(self.suite)
        if not suite_config:
            raise ValueError(f"Unknown suite: {self.suite}")
        
        test_cases = []
        for test_file in suite_config['test_files']:
            file_path = Path("evaluations") / test_file
            with open(file_path, 'r') as f:
                data = yaml.safe_load(f)
                test_cases.extend(data.get('test_cases', []))
        
        print(f"  Loaded {len(test_cases)} test cases")
        return test_cases
    
    def run_test_case(self, test_case: Dict[str, Any]) -> Dict[str, Any]:
        """Run a single test case."""
        test_id = test_case['id']
        test_name = test_case['name']
        agent_name = test_case['agent']
        test_input = test_case['input']
        
        print(f"  Running: {test_name} ({test_id})")
        
        result = {
            'test_id': test_id,
            'test_name': test_name,
            'agent': agent_name,
            'timestamp': datetime.utcnow().isoformat(),
            'passed': False,
            'metrics': {}
        }
        
        try:
            start_time = time.time()
            
            # TODO: Implement actual agent invocation
            # This is a placeholder - replace with actual SDK methods
            # response = self.client.agents.invoke(
            #     agent_name=agent_name,
            #     input=test_input
            # )
            
            # Simulate response for now
            response = {
                'content': f"Response for: {test_input}",
                'tokens': 50
            }
            
            end_time = time.time()
            response_time = (end_time - start_time) * 1000  # milliseconds
            
            # Record metrics
            result['metrics']['response_time'] = response_time
            result['metrics']['token_count'] = response.get('tokens', 0)
            result['response'] = response.get('content', '')
            
            # Check expected contains
            passed = True
            if 'expected_contains' in test_case:
                for expected in test_case['expected_contains']:
                    if expected.lower() not in result['response'].lower():
                        passed = False
                        result['failure_reason'] = f"Expected '{expected}' not found in response"
                        break
            
            # Check response time threshold
            if 'evaluation_criteria' in test_case:
                threshold = test_case['evaluation_criteria'].get('response_time_threshold', 5000)
                if response_time > threshold:
                    passed = False
                    result['failure_reason'] = f"Response time {response_time}ms exceeded threshold {threshold}ms"
            
            result['passed'] = passed
            
            if passed:
                print(f"    ✓ PASSED ({response_time:.0f}ms)")
            else:
                print(f"    ✗ FAILED: {result.get('failure_reason', 'Unknown')}")
        
        except Exception as e:
            result['passed'] = False
            result['error'] = str(e)
            result['failure_reason'] = f"Exception: {e}"
            print(f"    ✗ ERROR: {e}")
        
        return result
    
    def run_evaluations(self) -> bool:
        """Run all evaluations."""
        print(f"\n🧪 Running {self.suite} evaluation suite...\n")
        
        test_cases = self.load_test_cases()
        
        for test_case in test_cases:
            result = self.run_test_case(test_case)
            self.results.append(result)
        
        return self.generate_report()
    
    def generate_report(self) -> bool:
        """Generate evaluation report."""
        print("\n📊 Generating report...\n")
        
        total = len(self.results)
        passed = sum(1 for r in self.results if r['passed'])
        failed = total - passed
        pass_rate = (passed / total * 100) if total > 0 else 0
        
        # Create results directory
        results_dir = Path("evaluations/results") / self.environment
        results_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        
        # Save JSON results
        json_file = results_dir / f"results-{timestamp}.json"
        with open(json_file, 'w') as f:
            json.dump({
                'environment': self.environment,
                'suite': self.suite,
                'timestamp': datetime.utcnow().isoformat(),
                'summary': {
                    'total': total,
                    'passed': passed,
                    'failed': failed,
                    'pass_rate': pass_rate
                },
                'results': self.results
            }, f, indent=2)
        
        print(f"  Saved results: {json_file}")
        
        # Generate markdown summary
        md_file = results_dir / f"summary-{timestamp}.md"
        with open(md_file, 'w') as f:
            f.write(f"# Evaluation Report\n\n")
            f.write(f"**Environment:** {self.environment}\n")
            f.write(f"**Suite:** {self.suite}\n")
            f.write(f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n")
            f.write(f"## Summary\n\n")
            f.write(f"- **Total Tests:** {total}\n")
            f.write(f"- **Passed:** {passed} ✓\n")
            f.write(f"- **Failed:** {failed} ✗\n")
            f.write(f"- **Pass Rate:** {pass_rate:.1f}%\n\n")
            
            if failed > 0:
                f.write(f"## Failed Tests\n\n")
                for result in self.results:
                    if not result['passed']:
                        f.write(f"### {result['test_name']} ({result['test_id']})\n")
                        f.write(f"- **Agent:** {result['agent']}\n")
                        f.write(f"- **Reason:** {result.get('failure_reason', 'Unknown')}\n\n")
        
        print(f"  Saved summary: {md_file}")
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"  EVALUATION SUMMARY")
        print(f"{'='*60}")
        print(f"  Total Tests:  {total}")
        print(f"  Passed:       {passed} ✓")
        print(f"  Failed:       {failed} ✗")
        print(f"  Pass Rate:    {pass_rate:.1f}%")
        print(f"{'='*60}\n")
        
        # Check against thresholds
        threshold = self.eval_config['thresholds']['global_pass_rate']
        if pass_rate / 100 >= threshold:
            print(f"✓ Evaluation passed (>= {threshold*100}% pass rate)")
            return True
        else:
            print(f"✗ Evaluation failed (< {threshold*100}% pass rate)")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Run evaluations against deployed agents"
    )
    parser.add_argument(
        "--environment",
        choices=["dev", "test", "prod"],
        required=True,
        help="Target environment"
    )
    parser.add_argument(
        "--suite",
        choices=["smoke", "full", "regression"],
        default="full",
        help="Evaluation suite to run"
    )
    
    args = parser.parse_args()
    
    try:
        runner = EvaluationRunner(args.environment, args.suite)
        success = runner.run_evaluations()
        return 0 if success else 1
    except Exception as e:
        print(f"\n✗ Evaluation failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
