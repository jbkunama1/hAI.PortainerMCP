# hAI.PortainerMCP

Ein MCP-Server zur Verwaltung mehrerer Portainer Instanzen per Alias (REST API + API Key).

## Funktionen
- API-Schlüssel Authentifizierung
- SSH Alias Registrierung
- Serverseitige Containerverwaltung
- PostgreSQL/Docker Compatibility

## Setup
1. Klonen Sie das Repository
2. Konfigurieren Sie `.env`-Datei
3. starten Sie mit `docker-compose up -d`

## Architektur
- **Auth Layer**: `auth/auth-proxy.js` – API-Schlüsselbasierte Authentifizierung
- **Admin UI/CLI**: `admin-server.js` – Web-Interface und CLI für Alias-Management
- **Core-Dienst**: `core-server.js` – Haupt-MCP-Service für Portainer-API-Endpunkte
- **Dockerfile**: Multi-Stage-Build für den MCP-Server
- **Deployment**: `docker-compose.yml` – Netzwerk und Services

## Alias-Beispiele
```json
{
  "ssh1": {
    "host": "10.0.0.11",
    "port": 22,
    "username": "EXAMPLE_USER",
    "password": "EXAMPLE_PASSWORD"
  }
}
```

## Sicherheitshinweis
Nur in vertrauenswürdigen Netzwerken verwenden. Nutzen Sie SSH-Schlüssel statt Passwörtern.