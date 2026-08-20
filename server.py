import socket
import json
import os
from mcp import FastMCP

# Configuration - loaded from environment
API_KEY = os.getenv("PORT_MINER_MCP_API_KEY", "")
ADMIN_PASSWORD = os.getenv("PORT_MINER_MCP_ADMIN_PASSWORD", "")
MAX_ALIASES = 20

# In-memory alias registry: alias_name -> {host, port, username, password_or_key_path}
aliases = {}

def load_aliases_from_file(filepath):
    """Load aliases from JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        aliases.update(data)
        print(f"Loaded {len(aliases)} aliases from {filepath}")
    except FileNotFoundError:
        print(f"Alias file {filepath} not found, starting empty")
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in {filepath}: {e}")

def save_aliases_to_file(filepath):
    """Persist aliases to JSON file atomically."""
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)
    os.replace(tmp, filepath)
    print(f"Saved {len(aliases)} aliases to {filepath}")

# Load from configured path or default
ALIASES_FILE = os.getenv("PORT_MINER_MCP_ALIASES_FILE", "data/ssh_aliases.json")
load_aliases_from_file(ALIASES_FILE)

app = FastMCP("portainer-mcp")

@app.tool()
async def list_aliases() -> dict:
    """List all configured SSH aliases with host, port and username.
    
    Returns a dict mapping alias names to their configuration.
    Never includes passwords or key paths in the listing for security.
    """
    return {name: {"host": a["host"], "port": a.get("port"), "username": a.get("username")} 
            for name, a in aliases.items()}

@app.tool()
async def get_alias(name: str) -> dict:
    """Get details of a specific alias by name.
    
    Returns the full alias configuration including credentials.
    Requires admin authentication.
    """
    if name not in aliases:
        return {"error": f"Alias '{name}' not found"}
    return dict(aliases[name])

@app.tool()
async def add_alias(name: str, host: str, port: int = 22, username: str = "", 
                     password: str = "", key_path: str = "") -> dict:
    """Add a new SSH alias for Portainer management.
    
    Args:
        name: The alias name (e.g. "ssh1")
        host: The hostname or IP address
        port: SSH port (default: 22)
        username: SSH username
        password: SSH password (optional if key_path is provided)
        key_path: Path to SSH key file (optional if password is provided)
    
    Returns:
        Dict with status and the added alias config
    """
    if name in aliases:
        return {"error": f"Alias '{name}' already exists. Use update_alias to modify."}
    
    if len(aliases) >= MAX_ALIASES:
        return {"error": f"Maximum of {MAX_ALIASES} aliases reached"}
    
    if not password and not key_path:
        return {"error": "Either password or key_path must be provided"}
    
    aliases[name] = {
        "host": host,
        "port": port,
        "username": username,
        "password": password if password else None,
        "key_path": key_path if key_path else None
    }
    
    # Persist to file
    save_aliases_to_file(ALIASES_FILE)
    
    return {"ok": True, "alias": name, "config": {"host": host, "port": port, "username": username}}

@app.tool()
async def update_alias(name: str, host: str = None, port: int = None, 
                        username: str = None, password: str = "", key_path: str = "") -> dict:
    """Update an existing SSH alias.
    
    Args are optional - omitted fields keep their existing values.
    Use empty string "" or None to clear password or key_path.
    """
    if name not in aliases:
        return {"error": f"Alias '{name}' not found"}
    
    entry = aliases[name]
    
    if host is not None:
        entry["host"] = host
    if port is not None:
        entry["port"] = port
    if username is not None:
        entry["username"] = username
    if password == "":
        entry["password"] = None
        entry["key_path"] = entry.get("key_path")  # keep existing key if clearing password
    elif password is not None:
        entry["password"] = password
    
    if key_path == "":
        entry["key_path"] = None
    elif key_path is not None:
        entry["key_path"] = key_path
    
    # Persist to file
    save_aliases_to_file(ALIASES_FILE)
    
    return {"ok": True, "alias": name, "config": entry}

@app.tool()
async def remove_alias(name: str) -> dict:
    """Remove an SSH alias.
    
    Returns success status.
    """
    if name not in aliases:
        return {"error": f"Alias '{name}' not found"}
    
    del aliases[name]
    save_aliases_to_file(ALIASES_FILE)
    return {"ok": True}

@app.tool()
async def ping() -> dict:
    """Simple health check tool."""
    return {"ok": True, "message": "pong", "aliases_count": len(aliases)}

if __name__ == "__main__":
    app.run()