#!/usr/bin/env python3
"""
Migrate filesystem prompts to the database via the API.
"""

import os
import sys
import json
import requests

API_BASE = os.getenv("VOS_API_BASE", "https://api.jarvos.dev")
API_KEY = os.getenv("VOS_API_KEY")

if not API_KEY:
    print("Error: VOS_API_KEY environment variable is required.")
    print("Set it with: export VOS_API_KEY=your_api_key_here")
    sys.exit(1)

AGENTS = [
    ("primary", "Primary Agent", "services/agents/primary_agent/system_prompt.txt"),
    ("browser", "Browser Agent", "services/agents/browser_agent/system_prompt.txt"),
    ("weather", "Weather Agent", "services/agents/weather_agent/system_prompt.txt"),
    ("search", "Search Agent", "services/agents/search_agent/system_prompt.txt"),
    ("notes", "Notes Agent", "services/agents/notes_agent/system_prompt.txt"),
    ("calendar", "Calendar Agent", "services/agents/calendar_agent/system_prompt.txt"),
    ("calculator", "Calculator Agent", "services/agents/calculator_agent/system_prompt.txt"),
]

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def read_prompt(path):
    """Read prompt from filesystem."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(base_dir, path)

    if not os.path.exists(full_path):
        print(f"  Warning: {full_path} not found")
        return None

    with open(full_path, 'r', encoding='utf-8') as f:
        return f.read()

def create_prompt(agent_id, name, content):
    """Create a prompt in the database via API."""
    url = f"{API_BASE}/api/v1/system-prompts/agents/{agent_id}"

    payload = {
        "name": f"{name} Default",
        "content": content,
        "section_ids": [],  # No sections included by default
        "is_active": True,
        "tools_position": "end"
    }

    response = requests.post(url, headers=HEADERS, json=payload)

    if response.status_code == 200:
        result = response.json()
        print(f"  Created prompt ID {result['id']} (v{result['version']})")
        return True
    else:
        print(f"  Error: {response.status_code} - {response.text}")
        return False

def main():
    print("Migrating agent prompts to database...\n")

    success_count = 0
    fail_count = 0

    for agent_id, name, path in AGENTS:
        print(f"Processing {agent_id}...")

        content = read_prompt(path)
        if content is None:
            fail_count += 1
            continue

        print(f"  Read {len(content)} characters from {path}")

        if create_prompt(agent_id, name, content):
            success_count += 1
        else:
            fail_count += 1

    print(f"\nMigration complete: {success_count} succeeded, {fail_count} failed")

if __name__ == "__main__":
    main()
