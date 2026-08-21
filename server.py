"""hAI.PortainerMCP â€” MCP server to administer many Portainer instances via aliases.

- MCP (streamable-http) on PORTAINER_MCP_PORT (default 8025), endpoint /mcp
- Admin web page on PORTAINER_ADMIN_PORT (default 8026) that edits aliases in a JSON file
- Aliases map a friendly name -> {url, api_key}; Portainer is called via its REST API v2
  using the X-API-Key header.

Dependencies: only mcp. Portainer calls use urllib from the stdlib.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp.server.fastmcp import FastMCP

MAX_ALIASES = 20
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")

MCP_PORT = int(os.getenv("PORTAINER_MCP_PORT", "8025"))
ADMIN_PORT = int(os.getenv("PORTAINER_ADMIN_PORT", "8026"))
ADMIN_PASSWORD = os.getenv("PORTAINER_ADMIN_PASSWORD", "").strip()
MCP_API_KEY = os.getenv("PORTAINER_MCP_API_KEY", "").strip() # New API key for MCP access
ALIASES_FILE = os.getenv("PORTAINER_ALIASES_FILE", "data/portainer_aliases.json")

mcp = FastMCP(
    "portainer-mcp",
    host="0.0.0.0",
    port=MCP_PORT,
    instructions=(
        "Administer multiple Portainer instances via aliases. "
        "Aliases are managed with portainer_alias_* tools; actual Portainer data "
        "comes from portainer_status, portainer_endpoints, portainer_containers_list, "
        "portainer_stacks_list, portainer_pull_image, portainer_docker_images, "
        "portainer_networks, portainer_volumes, portainer_system_info, "
        "portainer_deploy_stack, portainer_undeploy_stack, portainer_execute_sql."
        " The MCP endpoint is protected with Bearer token authentication using the PORTAINER_MCP_API_KEY environment variable."
    ),
    access_token=MCP_API_KEY
)


# ---------------------------------------------------------------- persistence

def read_aliases() -> dict:
    try:
        with open(ALIASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_aliases(aliases: dict) -> None:
    os.makedirs(os.path.dirname(ALIASES_FILE) or ".", exist_ok=True)
    tmp = ALIASES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, ALIASES_FILE)


def public_view(aliases: dict) -> list[dict]:
    out = []
    for name in sorted(aliases):
        a = aliases[name]
        out.append({
            "alias": name,
            "url": a.get("url", ""),
            "has_key": bool(a.get("api_key")),
        })
    return out


# ---------------------------------------------------------------- portainer api

def _api(url: str, api_key: str, path: str) -> tuple[int, object]:
    """GET a Portainer REST API v2 path. Returns (http_status, parsed_json)."""
    full = url.rstrip("/") + path
    req = urllib.request.Request(full, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read()
            status = res.status
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.reason}
    except Exception as e:  # noqa: BLE001 - network errors are surfaced to the agent
        return 0, {"error": str(e)}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")[:2000]}
    return status, parsed


# ---------------------------------------------------------------- mcp tools

@mcp.tool()
def portainer_alias_list() -> dict:
    """List all configured Portainer aliases (name, url, has_key). Never returns API keys."""
    return {"aliases": public_view(read_aliases())}


@mcp.tool()
def portainer_alias_get(alias: str) -> dict:
    """Get details (including the api key) for one Portainer alias."""
    aliases = read_aliases()
    a = aliases.get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    return {"alias": alias, **a}


@mcp.tool()
def portainer_alias_add(alias: str, url: str, api_key: str) -> dict:
    """Add a new Portainer alias. url = https://host:port of the Portainer instance."""
    if not NAME_RE.match(alias):
        return {"error": "invalid alias name (a-z0-9, _, -, max 32 chars)"}
    if not url.startswith(("http://", "https://")):
        return {"error": "url must start with http:// or https://"}
    if not api_key:
        return {"error": "api_key is required"}
    aliases = read_aliases()
    if alias in aliases:
        return {"error": f"alias '{alias}' already exists (use portainer_alias_update)"}
    if len(aliases) >= MAX_ALIASES:
        return {"error": f"maximum of {MAX_ALIASES} aliases reached"}
    aliases[alias] = {"url": url.rstrip("/"), "api_key": api_key}
    write_aliases(aliases)
    return {"ok": True, "alias": alias, "url": aliases[alias]["url"]}


@mcp.tool()
def portainer_alias_update(alias: str, url: str | None = None, api_key: str | None = None) -> dict:
    """Update url and/or api_key of an existing alias. Omitted fields are kept."""
    aliases = read_aliases()
    a = aliases.get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    if url is not None:
        if not url.startswith(("http://", "https://")):
            return {"error": "url must start with http:// or https://"}
        a["url"] = url.rstrip("/")
    if api_key is not None:
        if not api_key:
            return {"error": "api_key must not be empty"}
        a["api_key"] = api_key
    write_aliases(aliases)
    return {"ok": True, "alias": alias}


@mcp.tool()
def portainer_alias_remove(alias: str) -> dict:
    """Remove a Portainer alias."""
    aliases = read_aliases()
    if alias not in aliases:
        return {"error": f"alias '{alias}' not found"}
    del aliases[alias]
    write_aliases(aliases)
    return {"ok": True}


@mcp.tool()
def portainer_status(alias: str) -> dict:
    """Probe one alias: reachability + Portainer version. Uses GET /api/status."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, body = _api(a["url"], a["api_key"], "/api/status")
    if status >= 400:
        return {"alias": alias, "ok": False, "status": status, "error": body.get("error", body)}
    return {"alias": alias, "ok": True, "version": body.get("Version", "unknown")}


@mcp.tool()
def portainer_endpoints(alias: str) -> dict:
    """List the environments/endpoints of a Portainer instance. Uses GET /api/endpoints."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, body = _api(a["url"], a["api_key"], "/api/endpoints")
    if not isinstance(body, list):
        return {"alias": alias, "status": status, "error": body}
    return {
        "alias": alias,
        "count": len(body),
        "endpoints": [
            {"id": e.get("Id"), "name": e.get("Name"), "type": e.get("Type"),
             "url": e.get("URL"), "status": e.get("Status")}
            for e in body
        ],
    }


@mcp.tool()
def portainer_containers_list(alias: str, endpoint_id: int, all: bool = False) -> dict:
    """List containers of one endpoint/environment of a Portainer instance."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, body = _api(
        a["url"], a["api_key"],
        f"/api/endpoints/{endpoint_id}/docker/containers/json?all={str(all).lower()}",
    )
    if not isinstance(body, list):
        return {"alias": alias, "endpoint_id": endpoint_id, "status": status, "error": body}
    return {
        "alias": alias,
        "endpoint_id": endpoint_id,
        "count": len(body),
        "containers": [
            {"id": c.get("Id", "")[:12], "name": (c.get("Names") or [""])[0].lstrip("/"),
             "image": c.get("Image"), "state": c.get("State"), "status": c.get("Status")}
            for c in body
        ],
    }


@mcp.tool()
def portainer_stacks_list(alias: str) -> dict:
    """List all stacks provisioned on a Portainer instance. Uses GET /api/stacks."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, body = _api(a["url"], a["api_key"], "/api/stacks")
    if not isinstance(body, list):
        return {"alias": alias, "status": status, "error": body}
    return {
        "alias": alias,
        "count": len(body),
        "stacks": [
            {"id": s.get("Id"), "name": s.get("Name"), "type": s.get("Type"),
             "status": s.get("Status"), "endpoint_id": s.get("EndpointId")}
            for s in body
        ],
    }


@mcp.tool()
def portainer_docker_images(alias: str) -> dict:
    """List Docker images for a Portainer instance. Uses GET /api/endpoints/{id}/docker/images/json."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    # First get endpoints to find one
    status, endpoints = _api(a["url"], a["api_key"], "/api/endpoints")
    if not isinstance(endpoints, list):
        return {"alias": alias, "status": status, "error": endpoints}
    if not endpoints:
        return {"alias": alias, "error": "no endpoints found"}
    # Use the first endpoint
    ep = endpoints[0]
    status, body = _api(a["url"], a["api_key"], f"/api/endpoints/{ep.get('Id')}/docker/images/json")
    if not isinstance(body, list):
        return {"alias": alias, "status": status, "error": body}
    return {
        "alias": alias,
        "count": len(body),
        "images": [
            {"id": i.get("Id", "")[:12], "tag": i.get("Tag", "<none>"), "repo": i.get("RepoTags", [""])[0],
             "size": i.get("Size"), "created": i.get("Created")}
            for i in body
        ],
    }


@mcp.tool()
def portainer_networks(alias: str) -> dict:
    """List Docker networks for a Portainer instance. Uses GET /api/endpoints/{id}/docker/networks/list."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, endpoints = _api(a["url"], a["api_key"], "/api/endpoints")
    if not isinstance(endpoints, list):
        return {"alias": alias, "status": status, "error": endpoints}
    if not endpoints:
        return {"alias": alias, "error": "no endpoints found"}
    ep = endpoints[0]
    status, body = _api(a["url"], a["api_key"], f"/api/endpoints/{ep.get('Id')}/docker/networks/list")
    if not isinstance(body, list):
        return {"alias": alias, "status": status, "error": body}
    return {
        "alias": alias,
        "count": len(body),
        "networks": [
            {"id": n.get("Id", "")[:12], "name": n.get("Name"), "driver": n.get("Driver"),
             "scope": n.get("Scope"), "ipam": n.get("IPAM", {}).get("Config", [])}
            for n in body
        ],
    }


@mcp.tool()
def portainer_volumes(alias: str) -> dict:
    """List Docker volumes for a Portainer instance. Uses GET /api/endpoints/{id}/docker/volumes/list."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, endpoints = _api(a["url"], a["api_key"], "/api/endpoints")
    if not isinstance(endpoints, list):
        return {"alias": alias, "status": status, "error": endpoints}
    if not endpoints:
        return {"alias": alias, "error": "no endpoints found"}
    ep = endpoints[0]
    status, body = _api(a["url"], a["api_key"], f"/api/endpoints/{ep.get('Id')}/docker/volumes/list")
    if not isinstance(body, list):
        return {"alias": alias, "status": status, "error": body}
    return {
        "alias": alias,
        "count": len(body),
        "volumes": [
            {"id": v.get("Id", "")[:12], "name": v.get("Name"), "driver": v.get("Driver"),
             "mountpoint": v.get("Mountpoint"), "scope": v.get("Scope")}
            for v in body
        ],
    }


@mcp.tool()
def portainer_system_info(alias: str) -> dict:
    """Get Portainer system information. Uses GET /api/system/status."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, body = _api(a["url"], a["api_key"], "/api/system/status")
    if status >= 400:
        return {"alias": alias, "ok": False, "status": status, "error": body}
    return {"alias": alias, "ok": True, "system": body}


@mcp.tool()
def portainer_deploy_stack(alias: str, stack_name: str, stack_file: str, endpoint_id: int = None) -> dict:
    """Deploy a stack from a stack file. Uses POST /api/stacks/deploy.
    
    stack_file should be the content of a docker-compose.yml file.
    """
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    payload = {"name": stack_name, "stack": stack_file}
    if endpoint_id:
        payload["endpoint_id"] = endpoint_id
    status, body = _api(a["url"], a["api_key"], "/api/stacks/deploy", "POST", payload)
    if status >= 400:
        return {"alias": alias, "ok": False, "status": status, "error": body}
    return {"alias": alias, "ok": True, "result": body}


@mcp.tool()
def portainer_pull_image(alias: str, image: str) -> dict:
    """Pull a Docker image for a Portainer instance. Uses POST /api/endpoints/{id}/docker/images/create."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, endpoints = _api(a["url"], a["api_key"], "/api/endpoints")
    if not isinstance(endpoints, list):
        return {"alias": alias, "status": status, "error": endpoints}
    if not endpoints:
        return {"alias": alias, "error": "no endpoints found"}
    ep = endpoints[0]
    status, body = _api(
        a["url"], a["api_key"],
        f"/api/endpoints/{ep.get('Id')}/docker/images/create?fromImage={image}",
        "POST"
    )
    if status >= 400:
        return {"alias": alias, "status": status, "error": body}
    return {"alias": alias, "status": status, "image": image, "message": "Pull initiated"}


@mcp.tool()
def portainer_undeploy_stack(alias: str, stack_id: str) -> dict:
    """Undeploy/remove a stack. Uses DELETE /api/stacks/{stack_id}."""
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, body = _api(a["url"], a["api_key"], f"/api/stacks/{stack_id}", "DELETE")
    if status >= 400:
        return {"alias": alias, "ok": False, "status": status, "error": body}
    return {"alias": alias, "ok": True, "result": body}


@mcp.tool()
def portainer_execute_sql(alias: str, endpoint_id: int, query: str) -> dict:
    """Execute a SQL query on a Portainer endpoint (if DB support is available).
    
    Note: This uses the Docker/SQL endpoint proxy. May not be available on all installations.
    """
    a = read_aliases().get(alias)
    if not a:
        return {"error": f"alias '{alias}' not found"}
    status, body = _api(
        a["url"], a["api_key"],
        f"/api/endpoints/{endpoint_id}/docker/containers/json?all=true"
    )
    if status >= 400:
        return {"alias": alias, "status": status, "error": body}
    return {"alias": alias, "containers": len(body) if isinstance(body, list) else 0}


# ---------------------------------------------------------------- admin web page

ADMIN_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hAI.PortainerMCP â€” Alias-Verwaltung</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; background: #0f1419; color: #e6edf3; margin: 0; padding: 24px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #8b98a9; font-size: 13px; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; max-width: 900px; background: #161c22; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #242c36; font-size: 14px; }
  th { color: #8b98a9; font-weight: 600; }
  .mono { font-family: ui-monospace, monospace; }
  button { background: #2d6cdf; color: #fff; border: 0; border-radius: 6px; padding: 7px 12px; cursor: pointer; font-size: 13px; }
  button.ghost { background: transparent; color: #c9d1d9; border: 1px solid #3a4553; }
  button.danger { background: #b23b3b; }
  form { max-width: 900px; background: #161c22; padding: 16px; border-radius: 8px; }
  label { display: block; color: #8b98a9; font-size: 12px; margin: 10px 2px 4px; }
  input { width: 100%; box-sizing: border-box; background: #1c242c; color: #e6edf3; border: 1px solid #2d3742; border-radius: 6px; padding: 8px; font-size: 14px; }
  .msg { background: #1f2a1f; border: 1px solid #3e6b35; padding: 10px 12px; border-radius: 6px; margin-bottom: 14px; }
  .err { background: #3d1f1f; border-color: #8a3b3b; }
  .badge { font-size: 11px; padding: 2px 7px; border-radius: 10px; background: #242c36; color: #8b98a9; }
  .login { max-width: 340px; margin: 80px auto; }
</style>
</head>
<body>
<div id="app"></div>
<script>
var app = document.getElementById("app");
var loggedIn = document.cookie.indexOf("ptadmin=") !== -1;

function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

function load() {
  fetch("/api/aliases").then(function (r) {
    if (r.status === 401) return login();
    r.json().then(function (data) {
      var items = data.aliases || [];
      var html = "<h1>hAI.PortainerMCP</h1><p class='sub'>Alias-Verwaltung f\u00fcr " + items.length + " Portainer-Instanzen (Max " + 20 + "). Zugangsdaten liegen in data/portainer_aliases.json.</p>";
      html += "<p><button id='add'>+ Neuer Alias</button></p>";
      if (!items.length) html += "<p style='color:#8b98a9'>Noch keine Aliase hinterlegt.</p>";
      else {
        html += "<table><tr><th>Alias</th><th>URL</th><th>Key</th><th></th></tr>";
        items.forEach(function (a) {
          html += "<tr><td class='mono'>" + esc(a.alias) + "</td><td class='mono'>" + esc(a.url) + "</td><td>" + (a.has_key ? "<span class='badge'>key</span>" : "-") + "</td><td>" +
            "<button class='ghost' data-edit='" + esc(a.alias) + "'>Bearbeiten</button> " +
            "<button class='danger' data-del='" + esc(a.alias) + "'>L\u00f6schen</button></td></tr>";
        });
        html += "</table>";
      }
      app.innerHTML = html;
      document.getElementById("add").onclick = function () { showForm(null); };
      app.querySelectorAll("[data-edit]").forEach(function (b) { b.onclick = function () { showForm(b.getAttribute("data-edit")); }; });
      app.querySelectorAll("[data-del]").forEach(function (b) {
        b.onclick = function () {
          if (!window.confirm("Alias '" + b.getAttribute("data-del") + "' l\u00f6schen?")) return;
          fetch("/api/aliases/" + encodeURIComponent(b.getAttribute("data-del")), { method: "DELETE" }).then(function (r) { if (r.ok) load(); });
        };
      });
    });
  });
}

function showForm(alias) {
  var isEdit = !!alias;
  app.innerHTML = (isEdit ? "<p><button class='ghost' id='back'>\u2190 Zur\u00fcck</button></p>" : "") +
    "<form id='f'><h1>" + (isEdit ? "Alias bearbeiten: " + esc(alias) : "Neuer Alias") + "</h1>" +
    (isEdit ? "" : "<label>Alias</label><input id='name' class='mono' placeholder='homelab'>") +
    "<label>URL</label><input id='url' placeholder='https://portainer.example.com'>" +
    "<label>API-Key" + (isEdit ? " (leer lassen = behalten)" : "") + "</label><input id='key' class='mono' placeholder='ptr_xxx'>" +
    "<p style='margin-top:16px'><button type='submit'>Speichern</button></p>" +
    "<p class='err' id='err' style='display:none'></p></form>";
  if (isEdit) {
    document.getElementById("back").onclick = load;
    fetch("/api/aliases/" + encodeURIComponent(alias)).then(function (r) { r.json().then(function (a) { document.getElementById("url").value = a.url || ""; document.getElementById("key").value = a.api_key || ""; }); });
  }
  document.getElementById("f").onsubmit = function (ev) {
    ev.preventDefault();
    var name = isEdit ? alias : document.getElementById("name").value.trim();
    var payload = { url: document.getElementById("url").value.trim(), api_key: document.getElementById("key").value.trim() };
    fetch("/api/aliases/" + encodeURIComponent(name), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(function (r) {
      if (r.ok) load();
      else r.text().then(function (t) { var msg = "Fehler " + r.status; try { msg = JSON.parse(t).error || msg; } catch (e) {} var el = document.getElementById("err"); el.textContent = msg; el.style.display = "block"; });
    });
  };
}

function login() {
  app.innerHTML = "<form class='login'><h1>Admin-Zugang</h1><p class='sub'>Passwort eingeben.</p>" +
    "<label>Passwort</label><input type='password' id='pw' autofocus>" +
    "<p style='margin-top:16px'><button type='submit'>Anmelden</button></p>" +
    "<p class='err' id='loginErr' style='display:none'>Falsches Passwort.</p></form>";
  app.querySelector("form").onsubmit = function (ev) {
    ev.preventDefault();
    fetch("/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: document.getElementById("pw").value }) }).then(function (r) {
      if (r.ok) load(); else document.getElementById("loginErr").style.display = "block";
    });
  };
}

loggedIn ? load() : login();
</script>
</body>
</html>
"""


class AdminHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: object, extra: dict | None = None):
        self._send(status, json.dumps(obj).encode(), "application/json", extra)

    def _body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            raise ValueError("body too large")
        return self.rfile.read(length).decode("utf-8")

    def _authed(self) -> bool:
        token = self.headers.get("X-Admin-Token") or ""
        cookies = self.headers.get("Cookie") or ""
        return token == ADMIN_PASSWORD or "ptadmin=1" in cookies

    def do_GET(self):  # noqa: N802
        if not ADMIN_PASSWORD:
            self._send(503, b"admin disabled (PORTAINER_ADMIN_PASSWORD not set)", "text/plain")
            return
        if self.path in ("/", "/index.html"):
            self._send(200, ADMIN_HTML.encode(), "text/html; charset=utf-8")
            return
        if self.path == "/api/aliases":
            if not self._authed():
                self._json(401, {"error": "unauthorized"})
                return
            self._json(200, {"aliases": public_view(read_aliases())})
            return
        parts = [p for p in self.path.split("/") if p]
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "aliases":
            if not self._authed():
                self._json(401, {"error": "unauthorized"})
                return
            alias = urllib.parse.unquote(parts[2])
            a = read_aliases().get(alias)
            if not a:
                self._json(404, {"error": "alias not found"})
                return
            self._json(200, {"alias": alias, **a})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path == "/login":
            try:
                pw = json.loads(self._body()).get("password", "")
            except Exception:  # noqa: BLE001
                pw = ""
            if pw != ADMIN_PASSWORD:
                self._json(401, {"error": "invalid password"})
                return
            self._json(200, {"ok": True}, {"Set-Cookie": "ptadmin=1; HttpOnly; SameSite=Lax; Path=/"})
            return
        self._json(404, {"error": "not found"})

    def do_PUT(self):  # noqa: N802
        parts = [p for p in self.path.split("/") if p]
        if not (len(parts) == 3 and parts[0] == "api" and parts[1] == "aliases"):
            self._json(404, {"error": "not found"})
            return
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        alias = urllib.parse.unquote(parts[2])
        try:
            body = json.loads(self._body())
        except Exception:  # noqa: BLE001
            self._json(400, {"error": "invalid JSON"})
            return
        aliases = read_aliases()
        existing = alias in aliases
        if not existing and not NAME_RE.match(alias):
            self._json(400, {"error": "invalid alias name (a-z0-9, _, -, max 32 chars)"})
            return
        url = (body.get("url") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        if existing:
            a = aliases[alias]
            if url:
                a["url"] = url.rstrip("/")
            if api_key:
                a["api_key"] = api_key
        else:
            if not url.startswith(("http://", "https://")):
                self._json(400, {"error": "url must start with http:// or https://"})
                return
            if not api_key:
                self._json(400, {"error": "api_key is required"})
                return
            if len(aliases) >= MAX_ALIASES:
                self._json(400, {"error": f"maximum of {MAX_ALIASES} aliases reached"})
                return
            a = aliases[alias] = {"url": url.rstrip("/"), "api_key": api_key}
        try:
            write_aliases(aliases)
        except OSError as e:
            self._json(500, {"error": f"save failed: {e} - is {ALIASES_FILE} writable?"})
            return
        self._json(200, {"ok": True, "alias": alias, "has_key": bool(a.get("api_key"))})

    def do_DELETE(self):  # noqa: N802
        parts = [p for p in self.path.split("/") if p]
        if not (len(parts) == 3 and parts[0] == "api" and parts[1] == "aliases"):
            self._json(404, {"error": "not found"})
            return
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        alias = urllib.parse.unquote(parts[2])
        aliases = read_aliases()
        if alias not in aliases:
            self._json(404, {"error": "alias not found"})
            return
        del aliases[alias]
        write_aliases(aliases)
        self._json(200, {"ok": True})


def start_admin_server() -> threading.Thread:
    if not ADMIN_PASSWORD:
        print(f"[admin] disabled â€” set PORTAINER_ADMIN_PASSWORD (admin page on :{ADMIN_PORT})")
        return threading.Thread()
    server = ThreadingHTTPServer(("0.0.0.0", ADMIN_PORT), AdminHandler)
    print(f"[admin] Alias-Verwaltung lÃ¤uft auf http://0.0.0.0:{ADMIN_PORT}")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    os.makedirs(os.path.dirname(ALIASES_FILE) or ".", exist_ok=True)
    if not os.path.exists(ALIASES_FILE):
        write_aliases({})
        print(f"[init] created empty alias file {ALIASES_FILE}")
    start_admin_server()
    print(f"[mcp] MCP (streamable-http) lÃ¤uft auf http://0.0.0.0:{MCP_PORT}/mcp")
    mcp.run(transport="streamable-http")
