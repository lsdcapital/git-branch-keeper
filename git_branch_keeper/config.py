"""Configuration handling for git-branch-keeper"""

import json
import os
from typing import Optional, Dict, Any


def load_config(config_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Load configuration from JSON file.
    
    Args:
        config_file: Optional path to config file. If not provided,
                    looks in current directory first, then user's home directory.
    
    Returns:
        Dict containing configuration values
    """
    if config_file and os.path.exists(config_file):
        try:
            with open(config_file) as f:
                config = json.load(f)
            print(f"Loaded config from {config_file}")
            return config
        except json.JSONDecodeError as e:
            print(f"Error loading config file: {e}")
            return {}
        except Exception as e:
            print(f"Error reading config file: {e}")
            return {}

    # Look for config in current directory
    if os.path.exists("gitclean.json"):
        try:
            with open("gitclean.json") as f:
                config = json.load(f)
            print("Loaded config from gitclean.json")
            return config
        except Exception:
            pass

    # Look for config in home directory
    home = os.path.expanduser("~")
    home_config = os.path.join(home, ".gitclean.json")
    if os.path.exists(home_config):
        try:
            with open(home_config) as f:
                config = json.load(f)
            print(f"Loaded config from {home_config}")
            return config
        except Exception:
            pass

    return {}
