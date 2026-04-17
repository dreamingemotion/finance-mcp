# finance-mcp

Remote MCP server that answers finance questions for Claude (mobile or desktop).

- **Indexes** (S&P 500, NASDAQ, VIX, etc.) → Yahoo Finance
- **Stocks, portfolio, watchlists, fundamentals** → your running `ibkr-agent`

---

## Prerequisites on the remote server

- Python 3.11+
- `ibkr-agent` already running on `localhost:8000`  
  (its own README covers the IBKR Client Portal Gateway setup)
- A reverse proxy (nginx recommended) for HTTPS — required by the Claude mobile app

---

## 1. Deploy

```bash
git clone <this-repo> ~/finance-mcp
cd ~/finance-mcp

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 2. Configure

```bash
cp .env.example .env
nano .env
```

Fill in the three values:

```env
IBKR_AGENT_URL=http://localhost:8000   # ibkr-agent address (same host → localhost)
MCP_API_KEY=<your-secret-token>        # see below
MCP_PORT=8080
```

Generate a secure token:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 3. Run as a systemd service

Create `/etc/systemd/system/finance-mcp.service`:

```ini
[Unit]
Description=Finance MCP Server
After=network.target ibkr-agent.service

[Service]
User=<your-user>
WorkingDirectory=/home/<your-user>/finance-mcp
EnvironmentFile=/home/<your-user>/finance-mcp/.env
ExecStart=/home/<your-user>/finance-mcp/.venv/bin/python server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now finance-mcp
sudo systemctl status finance-mcp
```

---

## 4. Expose via nginx (HTTPS)

Claude's mobile app requires HTTPS. Add a server block to your nginx config
(replace `mcp.example.com` with your domain and make sure you have a TLS cert,
e.g. from Let's Encrypt):

```nginx
server {
    listen 443 ssl;
    server_name mcp.example.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;

        # Required for SSE (keep the connection open)
        proxy_set_header   Connection "";
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600s;

        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 5. Connect Claude

### Mobile app (claude.ai)

1. Open the Claude mobile app → **Settings → Integrations** (or **MCP Servers**)
2. Add a new server:
   - **URL:** `https://mcp.example.com/sse`
   - **Auth header:** `Authorization: Bearer <your-MCP_API_KEY>`

### Claude Code desktop / CLI

Add to `~/.claude.json` under `mcpServers` (or your project's `.mcp.json`):

```json
{
  "mcpServers": {
    "finance": {
      "type": "sse",
      "url": "https://mcp.example.com/sse",
      "headers": {
        "Authorization": "Bearer <your-MCP_API_KEY>"
      }
    }
  }
}
```

Restart Claude Code. Run `/mcp` to verify the server shows as connected.

---

## Tools exposed to Claude

| Tool | Source | When Claude uses it |
|------|--------|---------------------|
| `get_quote(symbol)` | yfinance (indexes) / IBKR (stocks) | Any single-symbol price question |
| `get_major_indexes()` | yfinance | "How are markets doing?" |
| `ask_ibkr(question)` | ibkr-agent (natural language) | Portfolio, watchlists, fundamentals, trends |

Index detection: symbols starting with `^` or known aliases (SPX, VIX, NDX, etc.)
are routed to Yahoo Finance automatically. Everything else goes to IBKR.

---

## Authentication

Every request must include one of:

```
Authorization: Bearer <MCP_API_KEY>
X-API-Key: <MCP_API_KEY>
```

Requests without a valid token receive `401 Unauthorized`.
