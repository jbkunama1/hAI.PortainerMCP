# Syntax: Dockerfile for hAI.PortainerMCP — MCP server + admin web page
FROM python:3.12-slim

WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
RUN mkdir -p data

EXPOSE 8025 8026

# Standard-Umgebungsvariablen (über Portainer/Compose überschreibbar)
ENV PORTAINER_MCP_PORT=8025 \
    PORTAINER_ADMIN_PORT=8026 \
    PORTAINER_ADMIN_PASSWORD="" \
    PORTAINER_ALIASES_FILE=/usr/src/app/data/portainer_aliases.json

CMD ["python", "server.py"]