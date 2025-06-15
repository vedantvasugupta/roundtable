from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import json

# Define the configuration schema for each voting model
VOTING_MODEL_CONFIGS = {
    "plurality": {
        "name": "Plurality Voting",
        "description": "Simple majority voting where each voter selects one option",
        "fields": [
            {
                "name": "winning_threshold",
                "type": "number",
                "label": "Winning Threshold (%)",
                "required": True,
                "min": 0,
                "max": 100,
                "default": 50
            },
            {
                "name": "allow_abstain",
                "type": "boolean",
                "label": "Allow Abstain Votes",
                "required": True,
                "default": True
            }
        ]
    },
    "borda": {
        "name": "Borda Count",
        "description": "Ranked voting where options are scored based on their position",
        "fields": [
            {
                "name": "points_system",
                "type": "select",
                "label": "Points System",
                "required": True,
                "options": [
                    {"value": "linear", "label": "Linear (n, n-1, n-2, ...)"},
                    {"value": "exponential", "label": "Exponential (2^n, 2^(n-1), ...)"}
                ],
                "default": "linear"
            },
            {
                "name": "allow_abstain",
                "type": "boolean",
                "label": "Allow Abstain Votes",
                "required": True,
                "default": True
            }
        ]
    },
    "approval": {
        "name": "Approval Voting",
        "description": "Voters can approve multiple options",
        "fields": [
            {
                "name": "min_approvals",
                "type": "number",
                "label": "Minimum Approvals Required",
                "required": True,
                "min": 1,
                "default": 1
            },
            {
                "name": "max_approvals",
                "type": "number",
                "label": "Maximum Approvals Allowed",
                "required": True,
                "min": 1,
                "default": 3
            },
            {
                "name": "allow_abstain",
                "type": "boolean",
                "label": "Allow Abstain Votes",
                "required": True,
                "default": True
            }
        ]
    }
}

def validate_config(mechanism: str, config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Validate a voting mechanism configuration"""
    if mechanism not in VOTING_MODEL_CONFIGS:
        return False, f"Unknown voting mechanism: {mechanism}"

    schema = VOTING_MODEL_CONFIGS[mechanism]

    # Check required fields
    for field in schema["fields"]:
        if field["required"] and field["name"] not in config:
            return False, f"Missing required field: {field['name']}"

    # Validate field values
    for field in schema["fields"]:
        if field["name"] in config:
            value = config[field["name"]]

            # Type validation
            if field["type"] == "number":
                if not isinstance(value, (int, float)):
                    return False, f"Field {field['name']} must be a number"
                if "min" in field and value < field["min"]:
                    return False, f"Field {field['name']} must be at least {field['min']}"
                if "max" in field and value > field["max"]:
                    return False, f"Field {field['name']} must be at most {field['max']}"

            elif field["type"] == "boolean":
                if not isinstance(value, bool):
                    return False, f"Field {field['name']} must be a boolean"

            elif field["type"] == "select":
                if value not in [opt["value"] for opt in field["options"]]:
                    return False, f"Field {field['name']} must be one of: {[opt['value'] for opt in field['options']]}"

    return True, None

def get_default_config(mechanism: str) -> Dict[str, Any]:
    """Get default configuration for a voting mechanism"""
    if mechanism not in VOTING_MODEL_CONFIGS:
        raise ValueError(f"Unknown voting mechanism: {mechanism}")

    config = {}
    for field in VOTING_MODEL_CONFIGS[mechanism]["fields"]:
        if "default" in field:
            config[field["name"]] = field["default"]

    return config

def get_voting_model_info(mechanism: str) -> Dict[str, Any]:
    """Get information about a voting mechanism"""
    if mechanism not in VOTING_MODEL_CONFIGS:
        raise ValueError(f"Unknown voting mechanism: {mechanism}")

    return {
        "name": VOTING_MODEL_CONFIGS[mechanism]["name"],
        "description": VOTING_MODEL_CONFIGS[mechanism]["description"],
        "fields": VOTING_MODEL_CONFIGS[mechanism]["fields"]
    }