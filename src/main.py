import argparse
import asyncio
import json
import os
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Dict, Iterator

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from litellm import completion as litellm_completion
from litellm.proxy import proxy_server as litellm_proxy_server
from memori import ConfigManager, Memori
from memori.core.providers import ProviderConfig


@dataclass
class ProxyConfig:
    host: str = "127.0.0.1"
    port: int = 10001
    startup_timeout: float = 60.0
    log_level: str = "warning"
    config_path: str = "./litellm_config.yaml"

    @classmethod
    def base_url(cls) -> str:
        return f"http://{cls.host}:{cls.port}"


EXCLUDED_PROXY_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
}


config = ConfigManager()
config.auto_load()  # Loads from environment or config files

if os.getenv("OPENAI_BASE_URL"):
    provider_config = ProviderConfig.from_custom(
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        model=os.getenv("OPENAI_MODEL"),
    )
    memori = Memori(
        conscious_ingest=True, auto_ingest=True, provider_config=provider_config
    )
else:
    memori = Memori(conscious_ingest=True, auto_ingest=True)
memori.enable()

app = FastAPI(title="MemoriProxy")

_proxy_thread: threading.Thread | None = None
_proxy_server_ref: dict[str, uvicorn.Server | None] = {"server": None}
_http_client: httpx.AsyncClient | None = None


def _run_proxy_server() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    litellm_proxy_server.user_config_file_path = ProxyConfig.config_path

    loop.run_until_complete(
        litellm_proxy_server.initialize(config=ProxyConfig.config_path)
    )

    config = uvicorn.Config(
        litellm_proxy_server.app,
        host=ProxyConfig.host,
        port=ProxyConfig.port,
        log_level=ProxyConfig.log_level,
        access_log=False,
    )
    server = uvicorn.Server(config)
    _proxy_server_ref["server"] = server
    with suppress(KeyboardInterrupt):
        loop.run_until_complete(server.serve())


def _start_proxy_thread() -> None:
    global _proxy_thread
    if _proxy_thread and _proxy_thread.is_alive():
        return

    _proxy_thread = threading.Thread(
        target=_run_proxy_server, name="litellm-proxy", daemon=True
    )
    _proxy_thread.start()


async def _ensure_proxy_ready() -> None:
    assert _http_client is not None  # nosec - initialized during startup
    deadline = time.monotonic() + ProxyConfig.startup_timeout
    while time.monotonic() < deadline:
        try:
            response = await _http_client.get("/health")
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.2)
    raise RuntimeError("LiteLLM proxy failed to start before timeout")


def _require_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise HTTPException(status_code=503, detail="LiteLLM proxy is not ready")
    return _http_client


def _serialize_llm_payload(llm_obj: Any) -> Any:
    if hasattr(llm_obj, "model_dump"):
        return llm_obj.model_dump()
    if hasattr(llm_obj, "dict"):
        return llm_obj.dict()
    return llm_obj


@app.on_event("startup")
async def _startup_event() -> None:
    global _http_client
    _start_proxy_thread()
    if _http_client is None:
        _http_client = httpx.AsyncClient(base_url=ProxyConfig.base_url(), timeout=None)
    await _ensure_proxy_ready()


@app.on_event("shutdown")
async def _shutdown_event() -> None:
    client = _http_client
    if client is not None:
        await client.aclose()
    server = _proxy_server_ref.get("server")
    if server is not None:
        server.should_exit = True
    thread = _proxy_thread
    if thread is not None:
        thread.join(timeout=5)


@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions_endpoint(
    request: Request,
) -> Response:
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception as exc:  # pragma: no cover - FastAPI handles parsing
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # If an Authorization header is present, forward the API key to LiteLLM.
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        api_key = auth_header.split(" ", 1)[1].strip()
        # Do not overwrite an explicit api_key in the body.
        payload.setdefault("api_key", api_key)

    streaming_requested = bool(payload.pop("stream", False))

    if streaming_requested:
        payload["stream"] = True
        try:
            llm_stream = litellm_completion(**payload)
        except Exception as exc:  # pragma: no cover - depends on provider config
            raise HTTPException(
                status_code=502, detail=f"LiteLLM completion failed: {exc}"
            ) from exc

        def event_stream() -> Iterator[str]:
            try:
                for chunk in llm_stream:
                    data = _serialize_llm_payload(chunk)
                    yield f"data: {json.dumps(data, separators=(',', ':'))}\n\n"
            except Exception as exc:  # pragma: no cover - depends on provider config
                raise HTTPException(
                    status_code=502, detail=f"LiteLLM completion failed: {exc}"
                ) from exc
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        llm_response = litellm_completion(**payload)
    except Exception as exc:  # pragma: no cover - depends on provider config
        raise HTTPException(
            status_code=502, detail=f"LiteLLM completion failed: {exc}"
        ) from exc

    data = _serialize_llm_payload(llm_response)

    return JSONResponse(content=data)


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_all_other_requests(full_path: str, request: Request) -> Response:
    client = _require_http_client()
    body = await request.body()
    target_path = request.url.path or "/"
    if request.url.query:
        target_path = f"{target_path}?{request.url.query}"

    headers = dict(request.headers)
    headers["host"] = f"{ProxyConfig.host}:{ProxyConfig.port}"

    try:
        proxy_response = await client.request(
            request.method,
            target_path,
            content=body if body else None,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Proxy request failed: {exc}"
        ) from exc

    forwarded_response = Response(
        content=proxy_response.content,
        status_code=proxy_response.status_code,
        media_type=proxy_response.headers.get("content-type"),
    )

    for key, value in proxy_response.headers.items():
        if key.lower() in EXCLUDED_PROXY_HEADERS:
            continue
        forwarded_response.headers[key] = value

    return forwarded_response


def cli() -> None:
    parser = argparse.ArgumentParser(description="MemoriProxy Server")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind the server to",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server to",
    )
    parser.add_argument(
        "--proxy-host",
        default="127.0.0.1",
        help="LiteLLM proxy host",
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=10001,
        help="LiteLLM proxy port",
    )
    parser.add_argument(
        "--proxy-timeout",
        type=float,
        default=15.0,
        help="LiteLLM proxy startup timeout",
    )
    parser.add_argument(
        "--proxy-log-level",
        default="warning",
        help="LiteLLM proxy log level",
    )

    args = parser.parse_args()

    ProxyConfig.host = args.proxy_host
    ProxyConfig.port = args.proxy_port
    ProxyConfig.startup_timeout = args.proxy_timeout
    ProxyConfig.log_level = args.proxy_log_level

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    cli()
