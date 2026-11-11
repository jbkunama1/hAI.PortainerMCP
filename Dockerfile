# Syntax: Dockerfile for hAI.PortainerMCP - MCP server + admin web page
FROM python:3.12-slim

WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
RUN mkdir -p data

EXPOSE 8025 8026

# Standard-Umgebungsvariablen (ueber Portainer/Compose ueberschreibbar)
ENV PORTAINER_MCP_PORT=8025 \
    PORTAINER_ADMIN_PORT=8026 \
    PORTAINER_ADMIN_PASSWORD="" \
    PORTAINER_ALIASES_FILE=/usr/src/app/data/portainer_aliases.json

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORTAINER_MCP_PORT','8025'), timeout=3)" || exit 1

CMD ["python", "server.py"]
