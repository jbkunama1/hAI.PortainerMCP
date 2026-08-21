# hAI.PortainerMCP

Ein MCP-Server, mit dem du 10–20 Portainer-Instanzen über Aliase administrieren kannst. Jede Instanz wird über REST-API + API-Key angesprochen – du verwaltest nur einen Friendly-Namen (Alias) pro Instanz.

[![Build and publish Docker image](https://github.com/jbkunama1/hAI.PortainerMCP/actions/workflows/docker-image.yml/badge.svg)](https://github.com/jbkunama1/hAI.PortainerMCP/actions/workflows/docker-image.yml)
[![TruffleHog](https://github.com/jbkunama1/hAI.PortainerMCP/actions/workflows/trufflehog.yml/badge.svg)](https://github.com/jbkunama1/hAI.PortainerMCP/actions/workflows/trufflehog.yml)
[![Docker Image](https://img.shields.io/badge/ghcr.io-image-2496ED?logo=docker&logoColor=white)](https://github.com/jbkunama1/hAI.PortainerMCP/pkgs/container/hai.portainermcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

```
MCP client / AnythingMCP / Agenten
        |
        |  streamable HTTP (MCP)
        v
portainer-mcp :8025/mcp         <-- dieser MCP-Server
        |
        |  Portainer REST API v2 + X-API-Key
        v
Portainer-1  Portainer-2  ...  Portainer-20   (ADMIN über Alias "homelab", "prod", ...)
        |
        v
    Admin-Webseite :8026        <-- verwaltet die Aliase in data/portainer_aliases.json
```

## Inhalte

- **Python-MCP-Server** (`server.py`) – spricht mit N Portainer-Instanzen über API v2
- **Admin-Webseite** – verwalte Aliase (Name, URL, API-Key) in JSON, läuft mit im selben Prozess
- **Docker-Image** – GitHub Actions pusht nach `ghcr.io/jbkunama1/hai.portainermcp:latest`
- **TruffleHog-Workflow** – Secret-Scan bei jedem Push/PR
- **`index.html`** – statische Projektseite für GitHub Pages (synced mit README)

---

## MCP-Tools

| Tool | Beschreibung |
|---|---|
| `portainer_alias_list` | Alle Aliase (Name, URL, hat Key) – ohne API-Keys |
| `portainer_alias_get` | Ein Alias inkl. API-Key |
| `portainer_alias_add` | Neuen Alias anlegen (Name, URL, API-Key) |
| `portainer_alias_update` | Alias ändern (URL/API-Key) |
| `portainer_alias_remove` | Alias löschen |
| `portainer_status` | Erreichbarkeit + Version einer Instanz (`GET /api/status`) |
| `portainer_endpoints` | Environments/Endpoints auflisten |
| `portainer_containers_list` | Container eines Endpoints auflisten |
| `portainer_stacks_list` | Stacks auflisten |
| `portainer_docker_images` | Docker-Images auflisten |
| `portainer_networks` | Docker-Netzwerke auflisten |
| `portainer_volumes` | Docker-Volumes auflisten |
| `portainer_system_info` | System-Informationen abfragen |
| `portainer_pull_image` | Docker-Image per Name pullen (`POST /images/create`) |
| `portainer_deploy_stack` | Stack deployen |
| `portainer_undeploy_stack` | Stack löschen |
| `portainer_execute_sql` | SQL auf einer Instanz ausführen |

Alle Treffer laufen über die Portainer-REST-API v2 mit `X-API-Key`-Header.

---

## Deployment via Portainer (Stack)

1. **Stacks → Add stack → Repository**
2. Repository-URL: `https://github.com/jbkunama1/hAI.PortainerMCP.git`
3. Compose-Pfad: `docker-compose.yml`, Branch: `main`
4. Umgebungsvariablen setzen (`.env`-Vorlage):

| Variable | Default | Zweck |
|---|---|---|
| `PORTAINER_MCP_PORT` | `8025` | MCP (streamable HTTP) |
| `PORTAINER_ADMIN_PORT` | `8026` | Admin-Webseite |
| `PORTAINER_ADMIN_PASSWORD` | `CHANGE_ME_ADMIN` | Passwort der Admin-Webseite (leer = deaktiviert) |
| `PORTAINER_MCP_API_KEY` | *(leer)* | **Bearer-Token für MCP-Endpoint** (leer = keine Auth) |
| `PORTAINER_ALIASES_FILE` | `/usr/src/app/data/portainer_aliases.json` | JSON mit Aliasen |

5. Docker-Netzwerk (falls nicht vorhanden): `docker network create highfishNetwork`.

Die Compose-Datei referenziert bereits:

```text
image: ghcr.io/jbkunama1/hai.portainermcp:latest
```

Die API-Keys pro Alias liegen in `data/portainer_aliases.json` (Volume-Mount `./data`), nicht im Image.

---

## Kurzstart (lokal)

```bash
pip install "mcp>=1.9"
export PORTAINER_ADMIN_PASSWORD=meinpass   # Windows: $env:PORTAINER_ADMIN_PASSWORD="meinpass"
export PORTAINER_MCP_API_KEY=mysecretkey   # optional: Bearer-Token für MCP-Endpoint
python server.py
```

- MCP-Endpoint: `http://localhost:8025/mcp`
- Admin-Seite: `http://localhost:8026`

MCP-Client/AnythingMCP-Konfiguration:

```json
{
  "mcpServers": {
    "portainer": {
      "name": "Portainer MCP",
      "type": "streamable",
      "url": "http://localhost:8025/mcp",
      "enabled": true,
      "bearer_token": "YOUR_API_KEY_HERE"
    }
  }
}
}
```

---

## Alias-Datei

Beispiel `data/portainer_aliases.example.json`:

```json
{
  "homelab": { "url": "https://portainer.example.com", "api_key": "ptr_XXXX" },
  "prod":    { "url": "http://10.0.0.20:9000",          "api_key": "ptr_YYYY" }
}
```

- Die echte Datei wird beim ersten Start automatisch angelegt.
- Sie wird per `.gitignore` vom Commit ausgeschlossen.

---

## Sicherheit

- Nur in vertrautem Netzwerk oder hinter VPN/HTTPS betreiben.
- `PORTAINER_ADMIN_PASSWORD` setzen – sonst ist die Admin-Seite deaktiviert.
- `PORTAINER_MCP_API_KEY` setzen, um den MCP-Endpoint mit Bearer-Auth (`Authorization: Bearer <key>`) zu schützen.
- Portainer-API-Keys möglichst minimal (RBAC) vergeben.
- TruffleHog-Workflow prüft automatisch auf versehentlich eingecheckte Secrets.

## Lizenz

MIT