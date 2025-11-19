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
Key environment variables (defaults shown in parentheses):

| Variable | Description |
| --- | --- |
| `LITELLM_CONFIG_PATH` | Path to the LiteLLM YAML config (`./litellm_config.yaml`). |
| `LITELLM_PROXY_HOST` (`127.0.0.1`) | Hostname for the embedded LiteLLM proxy. |
| `LITELLM_PROXY_PORT` (`10001`) | Port for the proxy server. |
| `LITELLM_PROXY_STARTUP_TIMEOUT` (`15`) | Seconds to wait for proxy readiness. |
| `LITELLM_PROXY_LOG_LEVEL` (`warning`) | Logging level fed to Uvicorn for the proxy. |
| `APP_HOST` (`0.0.0.0`) / `APP_PORT` (`8000`) | FastAPI host/port when launching via `python main.py`. |
| `MEMORI_*` | Any Memori SDK settings (e.g., `MEMORI_LOGGING__LEVEL=DEBUG`). |

Memori settings are managed through `ConfigManager.auto_load()`, so `.env`, environment variables, or config files are automatically consumed.

> ℹ️ Set `LITELLM_CONFIG_PATH` to a valid LiteLLM config file to bootstrap the embedded proxy; if the variable is unset or the file is missing, proxying routes return `503` and no background server is launched.

## Running the server
After installing the project (`pip install .` or `pip install -e .`), you can start it with the CLI:
```bash
memori-proxy
```
Example command (matching the one used during development):
```bash
MEMORI_LOGGING__LEVEL=DEBUG \
LITELLM_CONFIG_PATH=./litellm_config.yaml \
python -m uvicorn main:app --host 0.0.0.0 --port 4000
```
This starts the FastAPI app, bootstraps LiteLLM in a background thread, and exposes:
- `POST /chat/completions`
- `POST /v1/chat/completions`
- Any other path proxied directly to LiteLLM

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
