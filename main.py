import asyncio
import os
import threading
import time
from contextlib import suppress
from typing import Any, Dict

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from litellm import completion as litellm_completion
from litellm.proxy import proxy_server as litellm_proxy_server
from memori import ConfigManager, Memori

PROXY_HOST = os.getenv("LITELLM_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.getenv("LITELLM_PROXY_PORT", "10001"))
PROXY_STARTUP_TIMEOUT = float(os.getenv("LITELLM_PROXY_STARTUP_TIMEOUT", "15"))
PROXY_LOG_LEVEL = os.getenv("LITELLM_PROXY_LOG_LEVEL", "warning")
PROXY_BASE_URL = f"http://{PROXY_HOST}:{PROXY_PORT}"
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

memori = Memori(conscious_ingest=True, auto_ingest=True)
memori.enable()

app = FastAPI(title="MemoriProxy")

_proxy_thread: threading.Thread | None = None
_proxy_server_ref: dict[str, uvicorn.Server | None] = {"server": None}
_http_client: httpx.AsyncClient | None = None


def _run_proxy_server() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    config_path = os.getenv("LITELLM_CONFIG_PATH")
    if config_path:
        litellm_proxy_server.user_config_file_path = config_path

    loop.run_until_complete(litellm_proxy_server.initialize(config=config_path))

    config = uvicorn.Config(
        litellm_proxy_server.app,
        host=PROXY_HOST,
        port=PROXY_PORT,
        log_level=PROXY_LOG_LEVEL,
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
    deadline = time.monotonic() + PROXY_STARTUP_TIMEOUT
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


@app.on_event("startup")
async def _startup_event() -> None:
    global _http_client
    _start_proxy_thread()
    if _http_client is None:
        _http_client = httpx.AsyncClient(base_url=PROXY_BASE_URL, timeout=None)
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
async def chat_completions_endpoint(request: Request) -> JSONResponse:
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception as exc:  # pragma: no cover - FastAPI handles parsing
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if payload.get("stream"):
        raise HTTPException(
            status_code=400, detail="Streaming is not supported on this endpoint"
        )

    try:
        llm_response = litellm_completion(**payload)
    except Exception as exc:  # pragma: no cover - depends on provider config
        raise HTTPException(
            status_code=502, detail=f"LiteLLM completion failed: {exc}"
        ) from exc

    if hasattr(llm_response, "model_dump"):
        data = llm_response.model_dump()
    elif hasattr(llm_response, "dict"):
        data = llm_response.dict()
    else:
        data = llm_response  # fallback for unexpected return types

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
    headers["host"] = f"{PROXY_HOST}:{PROXY_PORT}"

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


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=False,
    )
