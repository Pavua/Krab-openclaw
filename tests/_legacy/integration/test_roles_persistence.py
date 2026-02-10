
import os
import json
import pytest
from unittest.mock import patch
import src.employee_templates as et

@pytest.fixture
def temp_roles_file(tmp_path):
    # Create a dummy roles file
    f = tmp_path / "roles.json"
    return str(f)

def test_save_and_load_roles(temp_roles_file):
    # Patch the ROLES_FILE constant in the module
    with patch("src.employee_templates.ROLES_FILE", temp_roles_file):
        # Force reload to use new file
        et.ROLES = et.load_roles()
        
        # Initial state should span defaults
        assert "coder" in et.ROLES
        
        # Test Save
        agent_name = "test_agent_007"
        prompt = "You are James Bond."
        
        success = et.save_role(agent_name, prompt)
        assert success is True
        
        # Verify in memory
        assert agent_name in et.ROLES
        assert et.get_role_prompt(agent_name) == prompt
        
        # Verify on disk
        with open(temp_roles_file, "r") as f:
            data = json.load(f)
            assert data[agent_name] == prompt
            
        # Verify persistence (simulate restart)
        # Reload roles from disk
        et.ROLES = et.load_roles()
        assert agent_name in et.ROLES
        assert et.get_role_prompt(agent_name) == prompt

