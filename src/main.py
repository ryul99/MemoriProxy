import argparse
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator

import httpx
import litellm
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from litellm import completion as litellm_completion
from memori import ConfigManager, Memori


@dataclass
class UpstreamConfig:
    base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
    timeout: float = 60.0


config = ConfigManager()
config.auto_load()  # Loads from environment or config files

memori = Memori(conscious_ingest=True, auto_ingest=True)
memori.enable()

_http_client: httpx.AsyncClient | None = None


def _require_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise HTTPException(status_code=503, detail="Upstream service is not ready")
    return _http_client


def _serialize_llm_payload(llm_obj: Any) -> Any:
    if hasattr(llm_obj, "model_dump"):
        return llm_obj.model_dump()
    if hasattr(llm_obj, "dict"):
        return llm_obj.dict()
    return llm_obj


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    # Ensure LiteLLM uses the same upstream base URL as the generic HTTP proxy.
    os.environ["OPENAI_BASE_URL"] = UpstreamConfig.base_url
    if hasattr(litellm, "api_base"):
        litellm.api_base = UpstreamConfig.base_url

    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=UpstreamConfig.base_url,
            timeout=UpstreamConfig.timeout,
        )

    try:
        yield
    finally:
        client = _http_client
        if client is not None:
            await client.aclose()


app = FastAPI(title="MemoriProxy", lifespan=lifespan)


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

    headers = {
        key: value for key, value in request.headers.items() if key.lower() != "host"
    }

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
        "--proxy-timeout",
        type=float,
        default=15.0,
        help="Timeout in seconds for upstream HTTP requests",
    )
    parser.add_argument(
        "--openai-base-url",
        default=UpstreamConfig.base_url,
        help="Base URL for the OpenAI-compatible upstream API",
    )

    args = parser.parse_args()

    UpstreamConfig.base_url = args.openai_base_url
    UpstreamConfig.timeout = args.proxy_timeout

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    cli()
