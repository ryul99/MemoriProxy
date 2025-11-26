# MemoriProxy

MemoriProxy is a FastAPI service that embeds [Memori](https://github.com/GibsonAI/Memori) context-aware memory into any LiteLLM workflow. It enriches every call through the Memori SDK, exposes a friendly `/chat/completions` endpoint with both standard and streaming responses, and forwards all other routes directly to an OpenAI-compatible upstream API.

## Features
- **Chat Completions API** mirroring the OpenAI-compatible `/v1/chat/completions` contract
- **Server-Sent Events (SSE) streaming** support with `[DONE]` termination tokens
- **Memori conscious ingestion** enabled by default for contextual memory
- **Catch-all HTTP proxy** so non-chat routes are forwarded unchanged to your upstream (e.g. `https://api.openai.com`)

## Requirements
- Python 3.12+
- Access to providers configured through LiteLLM (via environment variables or your own config)
- Optional: `uv` or `pip` for dependency installation

## Installation
```bash
uv tool install .
```

Or install directly from GitHub:
```bash
uv tool install https://github.com/ryul99/MemoriProxy.git
```

## Configuration
Configuration is primarily handled via command-line arguments and environment variables.

Runtime arguments:

| Argument | Default | Description |
| --- | --- | --- |
| `--host` | `0.0.0.0` | Host to bind the server to. |
| `--port` | `8000` | Port to bind the server to. |
| `--proxy-timeout` | `15.0` | Timeout in seconds for upstream HTTP requests. |
| `--openai-base-url` | `https://api.openai.com` (or `OPENAI_BASE_URL`) | Base URL for the OpenAI-compatible upstream API. |

**Environment variables:**
- `OPENAI_BASE_URL` (optional): Base URL for the upstream API (e.g., `https://api.openai.com`).

**Memori Configuration:**
Memori settings are managed through `ConfigManager.auto_load()`, so they continue to use environment variables (e.g., `MEMORI_LOGGING__LEVEL=DEBUG`) or config files.

## Running the server
After installing the project (`pip install .` or `pip install -e .`), you can start it with the CLI:
```bash
memori-proxy --port 4000 --openai-base-url https://api.openai.com
```

Or running from source:
```bash
python src/main.py --port 4000 --openai-base-url https://api.openai.com
```

This starts the FastAPI app, initializes Memori, and exposes:
- `POST /chat/completions`
- `POST /v1/chat/completions`
- Any other path proxied directly to the configured upstream API

## Docker

Run with Docker Compose (uses SQLite by default):

```bash
export MEMORI_AGENTS__OPENAI_API_KEY="sk-..."
docker compose up --build
```

The image is built using `uv` for fast dependency resolution. A GitHub Action is included for automated builds.

## API usage
### Non-streaming request
```bash
curl -X POST http://localhost:4000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
        "model": "gpt-4o-mini",
        "messages": [
          {"role": "user", "content": "Hello from MemoriProxy"}
        ]
      }'
```

### Streaming request (SSE)
```bash
curl -N -X POST http://localhost:4000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
        "model": "gpt-4o-mini",
        "stream": true,
        "messages": [
          {"role": "user", "content": "Stream this response"}
        ]
      }'
```
The response is emitted as `text/event-stream` with each chunk prefixed by `data:` and ends with `data: [DONE]`.
