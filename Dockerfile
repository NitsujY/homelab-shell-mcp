FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir . \
    && install -d -o nobody -g nogroup /var/log/homelab-shell-mcp

USER nobody
EXPOSE 8080
# MCP_AUTH_TOKEN must be provided at runtime (-e MCP_AUTH_TOKEN=...).
ENTRYPOINT ["homelab-shell-mcp"]
