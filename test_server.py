"""Self-check for the alias logic of hAI.PortainerMCP.

Run:  python test_server.py
"""

import os
import tempfile

import server

TMP = tempfile.mkdtemp()
server.ALIASES_FILE = os.path.join(TMP, "aliases.json")
server.write_aliases({})


def check(name, cond):
    if not cond:
        raise SystemExit(f"FAIL: {name}")
    print(f"ok - {name}")


# add
r = server.portainer_alias_add("homelab", "https://portainer.example.com", "ptr_abc")
check("add homelab", r.get("ok") is True)

# duplicate rejected
r = server.portainer_alias_add("homelab", "https://x", "ptr_yyy")
check("duplicate rejected", "already exists" in r.get("error", ""))

# invalid name rejected
r = server.portainer_alias_add("bad name!", "https://x", "ptr_yyy")
check("invalid name rejected", "invalid alias name" in r.get("error", ""))

# list does not leak keys
r = server.portainer_alias_list()
check("list has homelab", any(a["alias"] == "homelab" for a in r["aliases"]))
check("list hides key", all("api_key" not in a for a in r["aliases"]))
check("list hides key (has_key only)", all("has_key" in a for a in r["aliases"]))

# get returns key
r = server.portainer_alias_get("homelab")
check("get returns key", r.get("api_key") == "ptr_abc")

# update url, keep key
r = server.portainer_alias_update("homelab", url="http://10.0.0.20:9000")
check("update url", r.get("ok") is True)
check("key kept", server.read_aliases()["homelab"]["api_key"] == "ptr_abc")

# unknown alias error
r = server.portainer_alias_get("nope")
check("unknown alias error", "not found" in r.get("error", ""))

# remove
r = server.portainer_alias_remove("homelab")
check("remove", r.get("ok") is True)
check("gone after remove", "homelab" not in server.read_aliases())

print("ALL CHECKS PASSED")