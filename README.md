# MemoriProxy

MemoriProxy is a FastAPI service that embeds [Memori](https://github.com/GibsonAI/Memori) context-aware memory into any LiteLLM workflow. It boots a LiteLLM proxy in-process, enriches every call through the Memori SDK, and exposes a friendly `/chat/completions` endpoint with both standard and streaming responses.

## Features
- **Chat Completions API** mirroring the OpenAI-compatible `/v1/chat/completions` contract
- **Server-Sent Events (SSE) streaming** support with `[DONE]` termination tokens
- **Automatic LiteLLM proxy management** (startup, readiness check, graceful shutdown)
- **Memori conscious ingestion** enabled by default for contextual memory
- **Catch-all HTTP proxy** so non-chat routes still flow through LiteLLM

## Requirements
- Python 3.12+
- Access to providers configured through LiteLLM (see `litellm_config.yaml`)
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
Configuration is primarily handled via command-line arguments.

| Argument | Default | Description |
| --- | --- | --- |
| `--host` | `0.0.0.0` | Host to bind the server to. |
| `--port` | `8000` | Port to bind the server to. |
| `--proxy-host` | `127.0.0.1` | Hostname for the embedded LiteLLM proxy. |
| `--proxy-port` | `10001` | Port for the proxy server. |
| `--proxy-timeout` | `15.0` | Seconds to wait for proxy readiness. |
| `--proxy-log-level` | `warning` | Logging level fed to Uvicorn for the proxy. |
| `--litellm-config` | `None` | Path to the LiteLLM YAML config (e.g., `./litellm_config.yaml`). |

**Memori Configuration:**
Memori settings are managed through `ConfigManager.auto_load()`, so they continue to use environment variables (e.g., `MEMORI_LOGGING__LEVEL=DEBUG`) or config files.

> ℹ️ Use `--litellm-config` to point to a valid LiteLLM config file to bootstrap the embedded proxy; if omitted or the file is missing, proxying routes return `503` and no background server is launched.

## Running the server
After installing the project (`pip install .` or `pip install -e .`), you can start it with the CLI:
```bash
memori-proxy --port 4000 --litellm-config ./litellm_config.yaml
```

Or running from source:
```bash
python src/main.py --port 4000 --litellm-config ./litellm_config.yaml
```

This starts the FastAPI app, bootstraps LiteLLM in a background thread, and exposes:
- `POST /chat/completions`
- `POST /v1/chat/completions`
- Any other path proxied directly to LiteLLM

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
