#!/usr/bin/env python3
"""
Validation script for agent and workflow YAML files.
"""
import argparse
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Any, Tuple


class YAMLValidator:
    """Validates YAML files for agents and workflows."""
    
    REQUIRED_AGENT_FIELDS = ['name', 'version', 'definition']
    REQUIRED_WORKFLOW_FIELDS = ['kind', 'trigger', 'id', 'name']
    
    def __init__(self):
        self.errors = []
        self.warnings = []
    
    def validate_yaml_syntax(self, file_path: Path) -> bool:
        """Check if YAML file has valid syntax."""
        try:
            with open(file_path, 'r') as f:
                yaml.safe_load(f)
            return True
        except yaml.YAMLError as e:
            self.errors.append(f"{file_path}: Invalid YAML syntax - {e}")
            return False
    
    def validate_agent(self, data: Dict[str, Any], file_path: Path) -> bool:
        """Validate agent definition."""
        valid = True
        
        # Check required fields
        for field in self.REQUIRED_AGENT_FIELDS:
            if field not in data:
                self.errors.append(f"{file_path}: Missing required field '{field}'")
                valid = False
        
        # Validate definition structure
        if 'definition' in data:
            definition = data['definition']
            if 'kind' not in definition:
                self.errors.append(f"{file_path}: Missing 'kind' in definition")
                valid = False
            
            if 'model' not in definition:
                self.warnings.append(f"{file_path}: No model specified in definition")
        
        # Validate version format
        if 'version' in data:
            try:
                int(data['version'])
            except ValueError:
                self.warnings.append(f"{file_path}: Version should be numeric")
        
        return valid
    
    def validate_workflow(self, data: Dict[str, Any], file_path: Path) -> bool:
        """Validate workflow definition."""
        valid = True
        
        # Check required fields
        for field in self.REQUIRED_WORKFLOW_FIELDS:
            if field not in data:
                self.errors.append(f"{file_path}: Missing required field '{field}'")
                valid = False
        
        # Validate trigger structure
        if 'trigger' in data:
            trigger = data['trigger']
            if 'kind' not in trigger:
                self.errors.append(f"{file_path}: Missing 'kind' in trigger")
                valid = False
            
            if 'actions' not in trigger:
                self.errors.append(f"{file_path}: Missing 'actions' in trigger")
                valid = False
        
        # Validate workflow kind
        if data.get('kind') != 'workflow':
            self.errors.append(f"{file_path}: 'kind' must be 'workflow'")
            valid = False
        
        return valid
    
    def validate_directory(self, directory: str, file_type: str) -> Tuple[int, int]:
        """Validate all YAML files in a directory."""
        path = Path(directory)
        
        if not path.exists():
            self.errors.append(f"Directory not found: {directory}")
            return 0, 0
        
        yaml_files = list(path.glob("**/*.yaml"))
        if not yaml_files:
            self.warnings.append(f"No YAML files found in {directory}")
            return 0, 0
        
        total = len(yaml_files)
        valid = 0
        
        for file_path in yaml_files:
            if not self.validate_yaml_syntax(file_path):
                continue
            
            with open(file_path, 'r') as f:
                data = yaml.safe_load(f)
            
            if file_type == "agents":
                if self.validate_agent(data, file_path):
                    valid += 1
            elif file_type == "workflows":
                if self.validate_workflow(data, file_path):
                    valid += 1
        
        return total, valid


def main():
    parser = argparse.ArgumentParser(
        description="Validate agent and workflow YAML files"
    )
    parser.add_argument(
        "--type",
        choices=["agents", "workflows", "all"],
        default="all",
        help="What to validate"
    )
    
    args = parser.parse_args()
    validator = YAMLValidator()
    
    print("🔍 Validating YAML files...\n")
    
    total_files = 0
    valid_files = 0
    
    if args.type in ["agents", "all"]:
        print("Validating agents...")
        t, v = validator.validate_directory("agents", "agents")
        total_files += t
        valid_files += v
        print(f"  ✓ {v}/{t} agent files valid\n")
    
    if args.type in ["workflows", "all"]:
        print("Validating workflows...")
        t, v = validator.validate_directory("workflows", "workflows")
        total_files += t
        valid_files += v
        print(f"  ✓ {v}/{t} workflow files valid\n")
    
    # Print warnings
    if validator.warnings:
        print(f"⚠ Warnings ({len(validator.warnings)}):")
        for warning in validator.warnings:
            print(f"  {warning}")
        print()
    
    # Print errors
    if validator.errors:
        print(f"✗ Errors ({len(validator.errors)}):")
        for error in validator.errors:
            print(f"  {error}")
        print()
        return 1
    
    print(f"✓ All {total_files} files validated successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
