import yaml
import logging
import os
from dotenv import load_dotenv
from agents.manager import ManagerAgent

# Load environment
load_dotenv()

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Nexus")

def load_config(path="config/agents.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def main():
    logger.info("Starting Nexus System...")
    
    # Load Config
    try:
        config = load_config()
    except FileNotFoundError:
        logger.warning("Config file not found, using defaults.")
        config = {"agents": {}}

    # Initialize Manager (who initializes the rest)
    team_config = {
        "name": config.get("agents", {}).get("manager", {}).get("name", "Manager"),
        "team": config.get("agents", {})
    }
    
    manager = ManagerAgent(team_config)
    
    # Run loop
    manager.run()

if __name__ == "__main__":
    main()
