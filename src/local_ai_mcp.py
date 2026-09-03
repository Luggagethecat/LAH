import ctypes
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any

from mcp.server import MCPServer


# =============================================================================
# Codex Local AI Helper Bridge
# Version 1.3.0
#
# Design:
#   Codex -> MCP bridge -> local OpenWebUI/Ollama model
#                         -> deterministic OpenWebUI/SearXNG search when needed
#
# The local model may reason about whether fresh information is needed, but the
# Python bridge performs and records the actual web search. The model is never
# trusted merely because it claims that it searched the web.
# =============================================================================

BRIDGE_VERSION = "0.1.0"

OPENWEBUI_URL = os.getenv("OPENWEBUI_URL", "http://127.0.0.1:3000").rstrip("/")
DEFAULT_MODEL = os.getenv("OPENWEBUI_DEFAULT_MODEL", "")
DEFAULT_WEB_MODEL = os.getenv("OPENWEBUI_DEFAULT_WEB_MODEL", "")

MODEL_CACHE_TTL_SEC = float(os.getenv("LOCAL_AI_MODEL_CACHE_TTL_SEC", "15"))
HTTP_TIMEOUT_SEC = int(os.getenv("LOCAL_AI_HTTP_TIMEOUT_SEC", "180"))
LOCK_WAIT_TIMEOUT_SEC = int(os.getenv("LOCAL_AI_LOCK_WAIT_TIMEOUT_SEC", "300"))

MAX_AUTO_QUERIES = int(os.getenv("LOCAL_AI_MAX_AUTO_QUERIES", "4"))
DEFAULT_SEARCH_RESULTS = int(os.getenv("LOCAL_AI_DEFAULT_SEARCH_RESULTS", "6"))
MAX_SEARCH_RESULTS = int(os.getenv("LOCAL_AI_MAX_SEARCH_RESULTS", "10"))
MAX_SNIPPET_CHARS = int(os.getenv("LOCAL_AI_MAX_SNIPPET_CHARS", "500"))
MAX_RESEARCH_ROUNDS = int(os.getenv("LOCAL_AI_MAX_RESEARCH_ROUNDS", "2"))
MAX_TOTAL_RESEARCH_RESULTS = int(os.getenv("LOCAL_AI_MAX_TOTAL_RESEARCH_RESULTS", "10"))

# One inference slot shared across every bridge process on this Windows machine.
MUTEX_NAME = os.getenv(
    "LOCAL_AI_MUTEX_NAME",
    r"Local\CodexOpenWebUILocalAIInference",
)


MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "": {
        "recommended_for": [
            "code generation",
            "debugging",
            "code review",
            "unit-test ideas",
        ],
        "priority": {
            "code": 120,
            "debugging": 120,
            "tests": 115,
            "review": 110,
            "general": 55,
            "research": 35,
            "web": 30,
            "current": 30,
        },
    },
    "": {
        "recommended_for": [
            "general reasoning",
            "research synthesis",
            "writing",
            "explanations",
            "planning",
        ],
        "priority": {
            "research": 120,
            "web": 120,
            "current": 120,
            "reasoning": 115,
            "general": 110,
            "writing": 105,
            "review": 85,
            "code": 80,
        },
    },
    "llama3.2:latest": {
        "recommended_for": [
            "fast small tasks",
            "summarization",
            "simple transformations",
        ],
        "priority": {
            "fast": 120,
            "small": 120,
            "summarization": 110,
            "general": 70,
            "research": 65,
            "web": 65,
            "code": 60,
        },
    },
    "gemma3:latest": {
        "recommended_for": [
            "general reasoning",
            "summarization",
            "independent second opinions",
            "research synthesis",
        ],
        "priority": {
            "summarization": 115,
            "review": 105,
            "reasoning": 105,
            "general": 100,
            "research": 95,
            "web": 95,
            "code": 65,
        },
    },
    "default": {
        "recommended_for": [
            "OpenWebUI default preset",
            "general assistance",
        ],
        "priority": {
            "general": 85,
            "research": 80,
            "web": 80,
        },
    },
}


# Keep the critical policy compact and early for Codex tool discovery.
mcp = MCPServer(
    "Local AI Worker",
    instructions=(
        "Private local sub-agent. ask_local_ai can answer offline or perform bounded iterative "
        "research using deterministic web searches. web_mode=auto lets it request current evidence; "
        "required forces research; never forbids it. The helper may inspect real search evidence and "
        "request follow-up searches, but the bridge executes every search. One local inference runs "
        "globally at once. Codex owns security, integration, verification and final tests."
    ),
)


# =============================================================================
# Logging / HTTP
# =============================================================================

_cache_lock = threading.Lock()
_model_cache: dict[str, Any] = {"timestamp": 0.0, "models": None}


def _log(message: str) -> None:
    # MCP stdio owns stdout. Diagnostics must only use stderr.
    print(f"[local_ai] {message}", file=sys.stderr, flush=True)


def _api_key() -> str:
    key = os.getenv("OPENWEBUI_API_KEY")
    if not key:
        raise RuntimeError(
            "[CONFIG_MISSING_API_KEY] OPENWEBUI_API_KEY is not set."
        )
    return key


def _request_json(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> Any:
    headers = {"Authorization": f"Bearer {_api_key()}"}
    data = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        f"{OPENWEBUI_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout if timeout is not None else HTTP_TIMEOUT_SEC,
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        _log(f"HTTP {exc.code}: {detail[:500]}")
        raise RuntimeError(
            f"[OPENWEBUI_HTTP_ERROR] HTTP {exc.code}: {detail[:1200]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"[OPENWEBUI_UNREACHABLE] Could not reach {OPENWEBUI_URL}: {exc.reason}"
        ) from exc

    if not raw:
        return None

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        preview = raw.decode("utf-8", errors="replace")[:500]
        _log(f"Invalid JSON: {preview}")
        raise RuntimeError(
            "[OPENWEBUI_INVALID_JSON] OpenWebUI returned invalid JSON."
        ) from exc


# =============================================================================
# Model inventory / routing
# =============================================================================

def _profile_for(model_id: str) -> dict[str, Any]:
    return MODEL_PROFILES.get(
        model_id,
        {
            "recommended_for": ["unprofiled local model"],
            "priority": {},
        },
    )


def _fetch_local_models() -> list[dict[str, Any]]:
    data = _request_json("/api/models", timeout=30)
    models: list[dict[str, Any]] = []

    for item in (data or {}).get("data", []):
        if item.get("owned_by") != "ollama":
            continue
        if item.get("connection_type") != "local":
            continue

        model_id = item.get("id")
        if not model_id:
            continue

        details = (item.get("ollama") or {}).get("details") or {}
        profile = _profile_for(model_id)

        models.append(
            {
                "id": model_id,
                "name": item.get("name", model_id),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
                "loaded": bool(item.get("loaded")),
                "preset": bool(item.get("preset")),
                "recommended_for": profile["recommended_for"],
            }
        )

    return models


def _local_models(force_refresh: bool = False) -> list[dict[str, Any]]:
    now = time.monotonic()

    with _cache_lock:
        cached = _model_cache["models"]
        age = now - float(_model_cache["timestamp"])
        if (
            not force_refresh
            and cached is not None
            and age < MODEL_CACHE_TTL_SEC
        ):
            return cached

    models = _fetch_local_models()

    with _cache_lock:
        _model_cache["timestamp"] = time.monotonic()
        _model_cache["models"] = models

    return models


def _normalize_task_type(task_type: str) -> str:
    value = (task_type or "general").strip().lower()
    aliases = {
        "coding": "code",
        "programming": "code",
        "debug": "debugging",
        "test": "tests",
        "testing": "tests",
        "unit tests": "tests",
        "code review": "review",
        "summary": "summarization",
        "summarize": "summarization",
        "reason": "reasoning",
        "quick": "fast",
        "internet": "web",
        "online": "web",
        "current information": "current",
        "current events": "current",
    }
    return aliases.get(value, value)


def _choose_model(
    available_ids: list[str],
    task_type: str,
    *,
    prefer_research: bool = False,
) -> str:
    normalized = _normalize_task_type(task_type)

    if prefer_research and DEFAULT_WEB_MODEL in available_ids:
        return DEFAULT_WEB_MODEL

    scored: list[tuple[int, str]] = []
    for model_id in available_ids:
        score = int(
            _profile_for(model_id).get("priority", {}).get(normalized, 0)
        )
        scored.append((score, model_id))

    scored.sort(reverse=True)

    if scored and scored[0][0] > 0:
        return scored[0][1]

    preferred = DEFAULT_WEB_MODEL if prefer_research else DEFAULT_MODEL
    if preferred in available_ids:
        return preferred

    return available_ids[0]


def _resolve_model(
    requested_model: str,
    task_type: str,
    *,
    prefer_research: bool = False,
) -> tuple[str, str]:
    models = _local_models()
    ids = [model["id"] for model in models]

    if not ids:
        raise RuntimeError("[NO_LOCAL_MODELS] No local Ollama models are available.")

    if requested_model in ("", "auto", None):
        return (
            _choose_model(
                ids,
                task_type,
                prefer_research=prefer_research,
            ),
            "auto",
        )

    if requested_model not in ids:
        raise RuntimeError(
            "[MODEL_NOT_AVAILABLE] "
            f"Requested '{requested_model}'. Available: {', '.join(ids)}"
        )

    return requested_model, "explicit"


# =============================================================================
# Windows global inference mutex
# =============================================================================

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CreateMutexW = kernel32.CreateMutexW
CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
CreateMutexW.restype = ctypes.c_void_p

WaitForSingleObject = kernel32.WaitForSingleObject
WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
WaitForSingleObject.restype = ctypes.c_uint32

ReleaseMutex = kernel32.ReleaseMutex
ReleaseMutex.argtypes = [ctypes.c_void_p]
ReleaseMutex.restype = ctypes.c_bool

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [ctypes.c_void_p]
CloseHandle.restype = ctypes.c_bool

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102


def _new_mutex_handle():
    handle = CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise RuntimeError(
            f"[MUTEX_CREATE_FAILED] Win32 error {ctypes.get_last_error()}."
        )
    return handle


def _mutex_busy() -> bool:
    handle = _new_mutex_handle()
    try:
        result = WaitForSingleObject(handle, 0)
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            ReleaseMutex(handle)
            return False
        if result == WAIT_TIMEOUT:
            return True
        raise RuntimeError(f"[MUTEX_WAIT_FAILED] Result={result}")
    finally:
        CloseHandle(handle)


@contextmanager
def _single_inference_slot():
    handle = _new_mutex_handle()
    acquired = False
    started = time.monotonic()

    try:
        result = WaitForSingleObject(
            handle,
            max(0, LOCK_WAIT_TIMEOUT_SEC * 1000),
        )

        if result not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            if result == WAIT_TIMEOUT:
                raise RuntimeError(
                    "[LOCAL_AI_BUSY_TIMEOUT] Timed out waiting for local inference."
                )
            raise RuntimeError(f"[MUTEX_WAIT_FAILED] Result={result}")

        acquired = True
        yield round(time.monotonic() - started, 3)

    finally:
        if acquired:
            ReleaseMutex(handle)
        CloseHandle(handle)


# =============================================================================
# Local inference
# =============================================================================

def _run_model(
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.1,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }

    with _single_inference_slot() as wait_seconds:
        started = time.monotonic()
        data = _request_json(
            "/api/chat/completions",
            method="POST",
            payload=payload,
            timeout=HTTP_TIMEOUT_SEC,
        )
        inference_seconds = round(time.monotonic() - started, 3)

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "[OPENWEBUI_UNEXPECTED_RESPONSE] "
            + json.dumps(data, ensure_ascii=False, default=str)[:2000]
        ) from exc

    return {
        "content": content,
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
        "lock_wait_seconds": wait_seconds,
        "inference_seconds": inference_seconds,
    }


# =============================================================================
# Deterministic web search
# =============================================================================

def _clean_query(query: str) -> str:
    return " ".join(str(query).split())[:600]


def _extract_search_items(
    response: Any,
    *,
    max_results: int,
) -> list[dict[str, Any]]:
    candidates: list[Any] = []

    if isinstance(response, dict):
        for key in ("items", "results"):
            value = response.get(key)
            if isinstance(value, list):
                candidates.extend(value)

        # Be tolerant of nested response wrappers.
        for key in ("data", "result"):
            value = response.get(key)
            if isinstance(value, dict):
                for nested_key in ("items", "results"):
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        candidates.extend(nested)

    elif isinstance(response, list):
        candidates.extend(response)

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in candidates:
        if len(results) >= max_results:
            break
        if not isinstance(item, dict):
            continue

        title = (
            item.get("title")
            or item.get("name")
            or ((item.get("metadata") or {}).get("title")
                if isinstance(item.get("metadata"), dict) else None)
        )

        url = (
            item.get("link")
            or item.get("url")
            or item.get("href")
            or ((item.get("metadata") or {}).get("url")
                if isinstance(item.get("metadata"), dict) else None)
        )

        snippet = (
            item.get("snippet")
            or item.get("description")
            or item.get("content")
            or item.get("text")
        )

        if url:
            url = str(url).strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)

        if isinstance(snippet, list):
            snippet = " ".join(str(part) for part in snippet[:3])

        result: dict[str, Any] = {}
        if title:
            result["title"] = str(title)[:500]
        if url:
            result["url"] = url[:2000]
        if snippet:
            result["snippet"] = str(snippet)[:MAX_SNIPPET_CHARS]

        if result:
            results.append(result)

    return results


def _search_one(query: str, max_results: int) -> dict[str, Any]:
    clean = _clean_query(query)
    if not clean:
        return {"query": query, "ok": False, "results": [], "error": "Empty query"}

    payload = {"queries": [clean]}

    try:
        response = _request_json(
            "/api/v1/retrieval/process/web/search",
            method="POST",
            payload=payload,
            timeout=HTTP_TIMEOUT_SEC,
        )
        results = _extract_search_items(
            response,
            max_results=max_results,
        )
        return {
            "query": clean,
            "ok": True,
            "results": results,
            "result_count": len(results),
        }
    except Exception as exc:
        return {
            "query": clean,
            "ok": False,
            "results": [],
            "result_count": 0,
            "error": str(exc),
        }


def _search_many(
    queries: list[str],
    *,
    max_results_total: int,
) -> dict[str, Any]:
    normalized: list[str] = []
    seen_queries: set[str] = set()

    for query in queries:
        clean = _clean_query(query)
        if not clean:
            continue
        key = clean.casefold()
        if key in seen_queries:
            continue
        seen_queries.add(key)
        normalized.append(clean)
        if len(normalized) >= MAX_AUTO_QUERIES:
            break

    per_query_limit = min(
        DEFAULT_SEARCH_RESULTS,
        MAX_SEARCH_RESULTS,
        max_results_total,
    )

    searches = [
        _search_one(query, per_query_limit)
        for query in normalized
    ]

    combined: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for search in searches:
        for result in search["results"]:
            url = result.get("url")
            dedupe_key = str(url or result.get("title", "")).casefold()
            if dedupe_key and dedupe_key in seen_urls:
                continue
            if dedupe_key:
                seen_urls.add(dedupe_key)

            combined.append(
                {
                    **result,
                    "search_query": search["query"],
                }
            )

            if len(combined) >= max_results_total:
                break

        if len(combined) >= max_results_total:
            break

    return {
        "ok": any(search["ok"] for search in searches),
        "queries": normalized,
        "searches": searches,
        "results": combined,
        "result_count": len(combined),
        "evidence_present": bool(combined),
    }


# =============================================================================
# Auto web-decision planner
# =============================================================================

def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None

    stripped = text.strip()

    # Remove a common fenced-code wrapper.
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    # Best effort: find the first balanced-looking JSON object.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(stripped[start:end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None

    return None


def _task_looks_freshness_sensitive(task: str, task_type: str) -> bool:
    normalized = _normalize_task_type(task_type)
    if normalized in {"research", "web", "current"}:
        return True

    text = task.casefold()

    patterns = (
        "latest",
        "current",
        "today",
        "recent",
        "this week",
        "this month",
        "right now",
        "news",
        "article",
        "published",
        "release",
        "version",
        "price",
        "available",
        "availability",
        "2026",
        "website",
        "url",
        "verify",
        "confirm",
        "look up",
        "search",
        "find online",
        "documentation",
        "docs",
    )

    return any(pattern in text for pattern in patterns)


def _extract_search_hints(task: str) -> dict[str, str | None]:
    """Extract deterministic article/document lookup hints from a natural-language task."""
    title = None
    author = None
    publisher = None

    quoted = re.findall(r'["“](.+?)["”]', task)
    if quoted:
        title = _clean_query(quoted[0])

    author_match = re.search(
        r"\bby\s+([A-Za-zÀ-ÖØ-öø-ÿ'’.\- ]{2,80}?)(?=,\s*(?:published|publication)|\s+published\b|\n|$)",
        task,
        flags=re.IGNORECASE,
    )
    if author_match:
        author = _clean_query(author_match.group(1))

    publisher_map = [
        ("rnz", "RNZ"),
        ("stuff", "Stuff"),
        ("new zealand herald", "New Zealand Herald"),
        ("nz herald", "NZ Herald"),
        ("reuters", "Reuters"),
        ("associated press", "Associated Press"),
        ("bbc", "BBC"),
        ("the guardian", "The Guardian"),
        ("guardian", "The Guardian"),
        ("cnn", "CNN"),
    ]
    folded = task.casefold()
    for needle, label in publisher_map:
        if needle in folded:
            publisher = label
            break

    return {
        "title": title,
        "author": author,
        "publisher": publisher,
    }


def _derive_search_queries(
    task: str,
    suggested_queries: list[str] | None = None,
) -> list[str]:
    """Build several deterministic query variants for robust article/document lookup.

    Suggested model/Codex queries are considered, but exact-title variants derived from
    the task are also added so one weak query cannot cause the entire search to fail.
    """
    candidates: list[str] = []

    for query in suggested_queries or []:
        clean = _clean_query(query)
        if clean:
            candidates.append(clean)

    hints = _extract_search_hints(task)
    title = hints["title"]
    author = hints["author"]
    publisher = hints["publisher"]

    if title:
        candidates.append(f'"{title}"')
        if author:
            candidates.append(f'"{title}" "{author}"')
        if publisher:
            candidates.append(f'"{title}" {publisher}')
        if author and publisher:
            candidates.append(f'"{title}" "{author}" {publisher}')
    else:
        candidates.append(_clean_query(task))

    # Add the old broad fallback as a final recovery path when space permits.
    candidates.append(_clean_query(task))

    normalized: list[str] = []
    seen: set[str] = set()

    for query in candidates:
        clean = _clean_query(query)
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clean)
        if len(normalized) >= MAX_AUTO_QUERIES:
            break

    return normalized


def _fallback_query(task: str) -> str:
    queries = _derive_search_queries(task)
    return queries[0] if queries else _clean_query(task)


def _compact_search_attempts(search: dict[str, Any]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for item in search.get("searches", []):
        attempt = {
            "query": item.get("query"),
            "ok": bool(item.get("ok")),
            "result_count": int(item.get("result_count") or 0),
        }
        if item.get("error"):
            attempt["error"] = str(item["error"])[:500]
        attempts.append(attempt)
    return attempts


def _plan_auto_web(
    model: str,
    task: str,
    context: str,
    task_type: str,
    acceptance_criteria: str,
) -> dict[str, Any]:
    system_prompt = (
        "You are a planning sub-agent. Decide whether the task can be answered safely from "
        "stable knowledge or needs current web evidence. Return ONLY JSON. "
        'Schema: {"decision":"ANSWER_DIRECTLY|NEED_WEB|NEED_MORE_CONTEXT",'
        '"reason":"short reason","queries":["up to 3 search queries"],'
        '"answer":"concise answer or missing-context request"}. '
        "Use NEED_WEB when facts may have changed, a URL/article/version/date/current fact "
        "must be verified, or external evidence would materially improve accuracy. "
        "Use NEED_MORE_CONTEXT when required project/code information was not supplied. "
        "Never claim that a web search occurred."
    )

    user_parts = [
        f"TASK:\n{task.strip()}",
        f"TASK TYPE:\n{_normalize_task_type(task_type)}",
    ]

    if context.strip():
        user_parts.append(f"CONTEXT:\n{context.strip()[:12000]}")

    if acceptance_criteria.strip():
        user_parts.append(
            f"ACCEPTANCE CRITERIA:\n{acceptance_criteria.strip()}"
        )

    raw = _run_model(
        model,
        system_prompt,
        "\n\n".join(user_parts),
        temperature=0.0,
    )

    parsed = _extract_json_object(raw["content"])

    if parsed:
        decision = str(parsed.get("decision", "")).strip().upper()
        if decision in {"ANSWER_DIRECTLY", "NEED_WEB", "NEED_MORE_CONTEXT"}:
            queries = parsed.get("queries") or []
            if not isinstance(queries, list):
                queries = []

            return {
                "decision": decision,
                "reason": str(parsed.get("reason", "")).strip(),
                "queries": [
                    _clean_query(str(query))
                    for query in queries[:MAX_AUTO_QUERIES]
                    if _clean_query(str(query))
                ],
                "answer": str(parsed.get("answer", "")).strip(),
                "planner_parse_ok": True,
                "planner_usage": raw.get("usage"),
                "planner_inference_seconds": raw.get("inference_seconds"),
                "planner_lock_wait_seconds": raw.get("lock_wait_seconds"),
            }

    # Small local models will occasionally fail JSON. Use a deterministic safety fallback.
    needs_web = _task_looks_freshness_sensitive(task, task_type)

    return {
        "decision": "NEED_WEB" if needs_web else "ANSWER_DIRECTLY",
        "reason": (
            "Planner output was not valid structured JSON; deterministic freshness "
            "heuristics were used."
        ),
        "queries": [_fallback_query(task)] if needs_web else [],
        "answer": "",
        "planner_parse_ok": False,
        "planner_usage": raw.get("usage"),
        "planner_inference_seconds": raw.get("inference_seconds"),
        "planner_lock_wait_seconds": raw.get("lock_wait_seconds"),
    }


# =============================================================================
# Answer construction
# =============================================================================

def _build_task_prompt(
    task: str,
    context: str,
    output_kind: str,
    acceptance_criteria: str,
) -> str:
    sections = [f"TASK:\n{task.strip()}"]

    if context.strip():
        sections.append(f"CONTEXT:\n{context.strip()}")

    if output_kind.strip():
        sections.append(f"OUTPUT KIND:\n{output_kind.strip()}")

    if acceptance_criteria.strip():
        sections.append(
            f"ACCEPTANCE CRITERIA:\n{acceptance_criteria.strip()}"
        )

    return "\n\n".join(sections)


def _format_web_evidence(search_result: dict[str, Any]) -> str:
    if not search_result.get("results"):
        return "No web search results were found."

    lines = [
        "WEB EVIDENCE FROM DETERMINISTIC SEARCH:",
        "Only URLs listed below may be represented as discovered web sources.",
        "",
    ]

    for index, result in enumerate(search_result["results"], start=1):
        lines.append(f"SOURCE {index}")
        if result.get("title"):
            lines.append(f"Title: {result['title']}")
        if result.get("url"):
            lines.append(f"URL: {result['url']}")
        if result.get("snippet"):
            lines.append(f"Snippet: {result['snippet']}")
        if result.get("search_query"):
            lines.append(f"Search query: {result['search_query']}")
        lines.append("")

    return "\n".join(lines)


def _answer_with_evidence(
    model: str,
    task: str,
    context: str,
    output_kind: str,
    acceptance_criteria: str,
    search_result: dict[str, Any],
) -> dict[str, Any]:
    system_prompt = (
        "You are a local research and reasoning sub-agent working for a senior orchestration "
        "agent. Real deterministic web search results are supplied below. Use them as evidence. "
        "Do not claim to have searched, browsed, opened, or verified anything beyond the supplied "
        "evidence. Never invent a URL. Distinguish evidence-supported facts from your reasoning. "
        "If the evidence is insufficient, state exactly what remains unverified. "
        "Be concise so the senior agent receives useful information without unnecessary context."
    )

    user_prompt = (
        _build_task_prompt(
            task,
            context,
            output_kind,
            acceptance_criteria,
        )
        + "\n\n"
        + _format_web_evidence(search_result)
    )

    return _run_model(
        model,
        system_prompt,
        user_prompt,
        temperature=0.1,
    )



def _merge_research_results(
    accumulated: list[dict[str, Any]],
    new_results: list[dict[str, Any]],
    *,
    max_total: int,
) -> list[dict[str, Any]]:
    merged = list(accumulated)
    seen = {
        str(item.get("url") or item.get("title") or "").casefold()
        for item in merged
        if item.get("url") or item.get("title")
    }

    for item in new_results:
        key = str(item.get("url") or item.get("title") or "").casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(item)
        if len(merged) >= max_total:
            break

    return merged[:max_total]


def _generate_recovery_queries(
    model: str,
    task: str,
    context: str,
    previous_queries: list[str],
) -> dict[str, Any]:
    """Ask a local model only for alternative search queries, never factual answers."""
    system_prompt = (
        "You are a search-query planner. A deterministic web search failed to find useful evidence. "
        "Return ONLY JSON with schema "
        '{"queries":["up to 3 alternative queries"],"reason":"short reason"}. '
        "Generate materially different searches: use distinctive title phrases, author/publisher names, "
        "synonyms, shortened titles, likely site/domain terms, or other discriminating terms. "
        "Do not answer the underlying question and do not claim that any search occurred."
    )

    user_parts = [
        f"TASK:\n{task.strip()}",
        "PREVIOUS QUERIES:\n" + "\n".join(f"- {q}" for q in previous_queries),
    ]
    if context.strip():
        user_parts.append(f"CONTEXT:\n{context.strip()[:6000]}")

    raw = _run_model(
        model,
        system_prompt,
        "\n\n".join(user_parts),
        temperature=0.0,
    )

    parsed = _extract_json_object(raw["content"]) or {}
    queries = parsed.get("queries") or []
    if not isinstance(queries, list):
        queries = []

    cleaned = []
    seen = {q.casefold() for q in previous_queries}

    for query in queries:
        clean = _clean_query(str(query))
        if not clean or clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        cleaned.append(clean)
        if len(cleaned) >= MAX_AUTO_QUERIES:
            break

    return {
        "queries": cleaned,
        "reason": str(parsed.get("reason", "")).strip(),
        "parse_ok": bool(parsed),
        "usage": raw.get("usage"),
        "inference_seconds": raw.get("inference_seconds"),
        "lock_wait_seconds": raw.get("lock_wait_seconds"),
    }


def _review_research_evidence(
    model: str,
    task: str,
    context: str,
    output_kind: str,
    acceptance_criteria: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Judge whether real search evidence is sufficient and optionally request follow-ups."""
    system_prompt = (
        "You are a local research sub-agent. You are given real deterministic web search results. "
        "Return ONLY JSON with schema: "
        '{"sufficient":true,"confidence":"low|medium|high","answer":"concise grounded answer",'
        '"missing":"what remains unverified","follow_up_queries":["up to 3 queries"]}. '
        "Set sufficient=true only when the supplied evidence actually supports the requested answer "
        "and acceptance criteria. Never treat facts merely repeated in the user prompt as verified. "
        "Never invent URLs or claim to have opened pages. If evidence is weak, off-topic, or missing "
        "a requested metadata field, set sufficient=false and propose focused follow-up searches."
    )

    user_prompt = (
        _build_task_prompt(
            task,
            context,
            output_kind,
            acceptance_criteria,
        )
        + "\n\n"
        + _format_web_evidence(evidence)
    )

    raw = _run_model(
        model,
        system_prompt,
        user_prompt,
        temperature=0.0,
    )

    parsed = _extract_json_object(raw["content"])

    if not parsed:
        return {
            "parse_ok": False,
            "sufficient": False,
            "confidence": "low",
            "answer": "",
            "missing": "The local research reviewer did not return valid structured output.",
            "follow_up_queries": [],
            "usage": raw.get("usage"),
            "inference_seconds": raw.get("inference_seconds"),
            "lock_wait_seconds": raw.get("lock_wait_seconds"),
        }

    followups = parsed.get("follow_up_queries") or []
    if not isinstance(followups, list):
        followups = []

    cleaned_followups = []
    for query in followups:
        clean = _clean_query(str(query))
        if clean:
            cleaned_followups.append(clean)
        if len(cleaned_followups) >= MAX_AUTO_QUERIES:
            break

    confidence = str(parsed.get("confidence", "low")).strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"

    return {
        "parse_ok": True,
        "sufficient": bool(parsed.get("sufficient")),
        "confidence": confidence,
        "answer": str(parsed.get("answer", "")).strip(),
        "missing": str(parsed.get("missing", "")).strip(),
        "follow_up_queries": cleaned_followups,
        "usage": raw.get("usage"),
        "inference_seconds": raw.get("inference_seconds"),
        "lock_wait_seconds": raw.get("lock_wait_seconds"),
    }


def _bounded_web_research(
    model: str,
    task: str,
    context: str,
    output_kind: str,
    acceptance_criteria: str,
    initial_queries: list[str],
    *,
    max_results: int,
) -> dict[str, Any]:
    """Search, assess evidence, and perform a bounded number of follow-up searches."""
    max_total = max(
        max_results,
        min(MAX_TOTAL_RESEARCH_RESULTS, max_results + 4),
    )

    all_results: list[dict[str, Any]] = []
    all_attempts: list[dict[str, Any]] = []
    all_queries: list[str] = []
    rounds: list[dict[str, Any]] = []
    current_queries = list(initial_queries)
    final_review: dict[str, Any] | None = None

    for round_number in range(1, MAX_RESEARCH_ROUNDS + 1):
        # If a previous round returned no evidence and no useful follow-up queries,
        # allow one local query-expansion pass before giving up.
        if not current_queries:
            recovery = _generate_recovery_queries(
                model,
                task,
                context,
                all_queries,
            )
            current_queries = recovery["queries"]
            rounds.append(
                {
                    "round": round_number,
                    "phase": "query_recovery",
                    "queries": current_queries,
                    "reason": recovery["reason"],
                    "parse_ok": recovery["parse_ok"],
                }
            )

        if not current_queries:
            break

        search = _search_many(
            current_queries,
            max_results_total=max_results,
        )

        all_queries.extend(
            query for query in search["queries"]
            if query not in all_queries
        )
        all_attempts.extend(_compact_search_attempts(search))
        before = len(all_results)
        all_results = _merge_research_results(
            all_results,
            search["results"],
            max_total=max_total,
        )
        new_result_count = len(all_results) - before

        evidence = {
            "results": all_results,
            "result_count": len(all_results),
            "evidence_present": bool(all_results),
        }

        round_record: dict[str, Any] = {
            "round": round_number,
            "phase": "search_and_review",
            "queries": search["queries"],
            "new_result_count": new_result_count,
            "total_result_count": len(all_results),
        }

        if not all_results:
            round_record["sufficient"] = False
            round_record["confidence"] = "low"
            round_record["missing"] = "No deterministic web evidence was returned."
            rounds.append(round_record)

            if round_number < MAX_RESEARCH_ROUNDS:
                recovery = _generate_recovery_queries(
                    model,
                    task,
                    context,
                    all_queries,
                )
                current_queries = recovery["queries"]
                rounds.append(
                    {
                        "round": round_number,
                        "phase": "query_recovery",
                        "queries": current_queries,
                        "reason": recovery["reason"],
                        "parse_ok": recovery["parse_ok"],
                    }
                )
                continue
            break

        review = _review_research_evidence(
            model,
            task,
            context,
            output_kind,
            acceptance_criteria,
            evidence,
        )
        final_review = review

        round_record.update(
            {
                "review_parse_ok": review["parse_ok"],
                "sufficient": review["sufficient"],
                "confidence": review["confidence"],
                "missing": review["missing"],
                "follow_up_queries": review["follow_up_queries"],
            }
        )
        rounds.append(round_record)

        if review["sufficient"]:
            break

        # Stop if the reviewer cannot suggest anything new or we've reached the budget.
        if round_number >= MAX_RESEARCH_ROUNDS:
            break

        seen = {query.casefold() for query in all_queries}
        current_queries = [
            query
            for query in review["follow_up_queries"]
            if query.casefold() not in seen
        ]

        if not current_queries:
            recovery = _generate_recovery_queries(
                model,
                task,
                context,
                all_queries,
            )
            current_queries = recovery["queries"]
            rounds.append(
                {
                    "round": round_number,
                    "phase": "query_recovery",
                    "queries": current_queries,
                    "reason": recovery["reason"],
                    "parse_ok": recovery["parse_ok"],
                }
            )

    sufficient = bool(final_review and final_review.get("sufficient"))
    answer = final_review.get("answer", "") if final_review else ""
    confidence = final_review.get("confidence", "low") if final_review else "low"
    missing = (
        final_review.get("missing", "")
        if final_review
        else "No usable deterministic web evidence was found."
    )

    return {
        "ok": sufficient,
        "evidence_present": bool(all_results),
        "evidence_sufficient": sufficient,
        "confidence": confidence,
        "answer": answer,
        "missing": missing,
        "results": all_results,
        "result_count": len(all_results),
        "queries": all_queries,
        "search_attempts": all_attempts,
        "research_rounds": rounds,
        "round_count": len(
            [item for item in rounds if item.get("phase") == "search_and_review"]
        ),
    }


def _answer_offline(
    model: str,
    task: str,
    context: str,
    output_kind: str,
    acceptance_criteria: str,
) -> dict[str, Any]:
    system_prompt = (
        "You are a local coding/reasoning sub-agent working for a senior orchestration agent. "
        "Complete only the bounded task given. Be concise and technically precise. "
        "Before responding, silently review the answer for correctness. "
        "For code, verify API semantics, signatures and examples. "
        "Never claim execution, web access, file access, testing, or command results unless "
        "that evidence was explicitly supplied."
    )

    return _run_model(
        model,
        system_prompt,
        _build_task_prompt(
            task,
            context,
            output_kind,
            acceptance_criteria,
        ),
        temperature=0.1,
    )


def _validate_web_mode(web_mode: str) -> str:
    mode = (web_mode or "auto").strip().lower()
    if mode not in {"auto", "never", "required"}:
        raise RuntimeError(
            "[INVALID_WEB_MODE] web_mode must be auto, never, or required."
        )
    return mode


# =============================================================================
# MCP tools
# =============================================================================

@mcp.tool()
def local_ai_status(force_refresh: bool = False) -> dict[str, Any]:
    """Check the local helper, OpenWebUI connectivity, model inventory and busy state."""
    try:
        models = _local_models(force_refresh=force_refresh)
        ids = {model["id"] for model in models}

        return {
            "ok": True,
            "bridge_version": BRIDGE_VERSION,
            "openwebui_url": OPENWEBUI_URL,
            "reachable": True,
            "model_count": len(models),
            "configured_default_model": DEFAULT_MODEL,
            "default_model_available": DEFAULT_MODEL in ids,
            "configured_default_web_model": DEFAULT_WEB_MODEL,
            "default_web_model_available": DEFAULT_WEB_MODEL in ids,
            "inference_busy": _mutex_busy(),
            "one_inference_globally_enforced": True,
            "web_search_method": "deterministic OpenWebUI retrieval endpoint",
            "supported_web_modes": ["auto", "never", "required"],
            "max_research_rounds": MAX_RESEARCH_ROUNDS,
            "max_total_research_results": MAX_TOTAL_RESEARCH_RESULTS,
        }
    except Exception as exc:
        return {
            "ok": False,
            "bridge_version": BRIDGE_VERSION,
            "openwebui_url": OPENWEBUI_URL,
            "reachable": False,
            "error": str(exc),
            "inference_busy": _mutex_busy(),
        }


@mcp.tool()
def list_local_models(force_refresh: bool = False) -> dict[str, Any]:
    """List local Ollama-backed models and routing hints without running inference."""
    models = _local_models(force_refresh=force_refresh)
    ids = {model["id"] for model in models}

    return {
        "ok": True,
        "bridge_version": BRIDGE_VERSION,
        "models": models,
        "count": len(models),
        "configured_default_model": DEFAULT_MODEL,
        "default_model_available": DEFAULT_MODEL in ids,
        "configured_default_web_model": DEFAULT_WEB_MODEL,
        "default_web_model_available": DEFAULT_WEB_MODEL in ids,
    }


@mcp.tool()
def search_local_web(
    query: str,
    max_results: int = 6,
) -> dict[str, Any]:
    """Perform real deterministic web search through OpenWebUI/SearXNG without using an LLM.

    Use when Codex only needs search evidence, URLs, titles or snippets and does not need
    the local model to interpret them. Returned results are the actual results recorded by
    the configured search backend; the local model is not involved.

    Args:
        query: Web search query.
        max_results: Number of compact results to return, from 1 to 10.
    """
    bounded = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
    result = _search_many([query], max_results_total=bounded)

    return {
        "ok": result["ok"],
        "bridge_version": BRIDGE_VERSION,
        "web_used": True,
        "evidence_present": result["evidence_present"],
        "queries": result["queries"],
        "search_attempts": _compact_search_attempts(result),
        "results": result["results"],
        "result_count": result["result_count"],
    }


@mcp.tool()
def ask_local_ai(
    task: str,
    context: str = "",
    model: str = "auto",
    task_type: str = "general",
    web_mode: str = "auto",
    output_kind: str = "text",
    acceptance_criteria: str = "",
    search_queries: list[str] | None = None,
    max_search_results: int = 6,
) -> dict[str, Any]:
    """Delegate one bounded task to the local AI helper with optional deterministic web evidence.

    web_mode:
    - auto: the local model first decides ANSWER_DIRECTLY, NEED_WEB or NEED_MORE_CONTEXT.
            If it requests web evidence, the Python bridge performs the real search and then
            gives recorded results back to a local model, which may request bounded follow-up searches.
    - never: no web request is permitted; answer entirely offline.
    - required: bounded iterative deterministic research is mandatory before the final answer.

    search_queries:
    Optional explicit queries supplied by Codex. In required mode they avoid a planning pass.
    In auto mode they are used if the planner decides web evidence is necessary.

    Resource/safety rules:
    - Only one local LLM inference runs at any moment across bridge processes.
    - Web search itself is deterministic and does not rely on a model claiming it searched.
    - Local output is assistance, not ground truth. Codex must review important results.
    - The helper has no filesystem, shell or SSH privileges.

    Args:
        task: Clear bounded task.
        context: Only relevant code, errors, constraints or background.
        model: Exact local model ID or "auto".
        task_type: Routing hint: code, debugging, tests, review, reasoning, writing,
                   summarization, research, web, current, fast, small or general.
        web_mode: auto, never or required.
        output_kind: Desired compact form, e.g. text, python, json, markdown, checklist.
        acceptance_criteria: Concrete requirements for the answer.
        search_queries: Optional list of up to three explicit web searches.
        max_search_results: Maximum compact web results returned/passed to the model, 1-10.
    """
    mode = _validate_web_mode(web_mode)
    bounded_results = max(
        1,
        min(int(max_search_results), MAX_SEARCH_RESULTS),
    )

    explicit_queries = [
        _clean_query(query)
        for query in (search_queries or [])[:MAX_AUTO_QUERIES]
        if _clean_query(query)
    ]

    # ---------------------------------------------------------------------
    # NEVER: one offline inference.
    # ---------------------------------------------------------------------
    if mode == "never":
        selected_model, selection_mode = _resolve_model(
            model,
            task_type,
            prefer_research=False,
        )

        answer = _answer_offline(
            selected_model,
            task,
            context,
            output_kind,
            acceptance_criteria,
        )

        return {
            "ok": True,
            "bridge_version": BRIDGE_VERSION,
            "web_mode": mode,
            "web_used": False,
            "web_evidence_present": False,
            "decision": "ANSWER_DIRECTLY",
            "model": selected_model,
            "selection_mode": selection_mode,
            "content": answer["content"],
            "sources": [],
            "finish_reason": answer["finish_reason"],
            "usage": answer["usage"],
            "lock_wait_seconds": answer["lock_wait_seconds"],
            "inference_seconds": answer["inference_seconds"],
        }

    # ---------------------------------------------------------------------
    # REQUIRED: bounded iterative deterministic research.
    # ---------------------------------------------------------------------
    if mode == "required":
        selected_model, selection_mode = _resolve_model(
            model,
            task_type,
            prefer_research=True,
        )

        queries = explicit_queries or _derive_search_queries(task)
        research = _bounded_web_research(
            selected_model,
            task,
            context,
            output_kind,
            acceptance_criteria,
            queries,
            max_results=bounded_results,
        )

        return {
            "ok": research["ok"],
            "bridge_version": BRIDGE_VERSION,
            "web_mode": mode,
            "web_used": True,
            "web_evidence_present": research["evidence_present"],
            "evidence_sufficient": research["evidence_sufficient"],
            "confidence": research["confidence"],
            "decision": "NEED_WEB",
            "model": selected_model,
            "selection_mode": selection_mode,
            "content": (
                research["answer"]
                if research["answer"]
                else (
                    "Local research could not verify the task from the deterministic evidence. "
                    + (research["missing"] or "")
                ).strip()
            ),
            "missing": research["missing"],
            "sources": research["results"],
            "source_count": research["result_count"],
            "search_queries": research["queries"],
            "search_attempts": research["search_attempts"],
            "research_rounds": research["research_rounds"],
            "research_round_count": research["round_count"],
            "warning": (
                None
                if research["evidence_sufficient"]
                else "The local research budget ended without sufficient evidence; use a higher-level fallback if verification is required."
            ),
        }

    # ---------------------------------------------------------------------
    # AUTO:
    # 1. Use a local planning pass.
    # 2. If stable knowledge is enough, accept its concise direct answer when present.
    # 3. If web is needed, deterministic search occurs outside the model.
    # 4. A research-oriented model synthesizes only the recorded evidence.
    # ---------------------------------------------------------------------
    planner_model, planner_selection_mode = _resolve_model(
        model,
        task_type,
        prefer_research=False,
    )

    plan = _plan_auto_web(
        planner_model,
        task,
        context,
        task_type,
        acceptance_criteria,
    )

    if plan["decision"] == "NEED_MORE_CONTEXT":
        return {
            "ok": True,
            "bridge_version": BRIDGE_VERSION,
            "web_mode": mode,
            "web_used": False,
            "web_evidence_present": False,
            "decision": "NEED_MORE_CONTEXT",
            "model": planner_model,
            "selection_mode": planner_selection_mode,
            "content": plan["answer"] or "More context is required before this task can be answered reliably.",
            "reason": plan["reason"],
            "sources": [],
            "planner_parse_ok": plan["planner_parse_ok"],
            "planner_usage": plan["planner_usage"],
            "planner_inference_seconds": plan["planner_inference_seconds"],
            "planner_lock_wait_seconds": plan["planner_lock_wait_seconds"],
        }

    if plan["decision"] == "ANSWER_DIRECTLY":
        # If the planner supplied a direct final answer, reuse it and avoid a second
        # local inference. Otherwise perform one normal offline answer pass.
        if plan["answer"]:
            content = plan["answer"]
            answer_meta = {
                "finish_reason": None,
                "usage": plan["planner_usage"],
                "lock_wait_seconds": plan["planner_lock_wait_seconds"],
                "inference_seconds": plan["planner_inference_seconds"],
            }
            reused_planner_answer = True
        else:
            direct = _answer_offline(
                planner_model,
                task,
                context,
                output_kind,
                acceptance_criteria,
            )
            content = direct["content"]
            answer_meta = direct
            reused_planner_answer = False

        return {
            "ok": True,
            "bridge_version": BRIDGE_VERSION,
            "web_mode": mode,
            "web_used": False,
            "web_evidence_present": False,
            "decision": "ANSWER_DIRECTLY",
            "reason": plan["reason"],
            "model": planner_model,
            "selection_mode": planner_selection_mode,
            "content": content,
            "sources": [],
            "planner_parse_ok": plan["planner_parse_ok"],
            "reused_planner_answer": reused_planner_answer,
            "finish_reason": answer_meta["finish_reason"],
            "usage": answer_meta["usage"],
            "lock_wait_seconds": answer_meta["lock_wait_seconds"],
            "inference_seconds": answer_meta["inference_seconds"],
        }

    # NEED_WEB
    queries = explicit_queries or _derive_search_queries(task, plan["queries"])

    # Prefer the research model for web synthesis unless the caller explicitly chose one.
    if model in ("", "auto", None):
        synthesis_model, synthesis_selection_mode = _resolve_model(
            "auto",
            "research",
            prefer_research=True,
        )
    else:
        synthesis_model = planner_model
        synthesis_selection_mode = planner_selection_mode

    research = _bounded_web_research(
        synthesis_model,
        task,
        context,
        output_kind,
        acceptance_criteria,
        queries,
        max_results=bounded_results,
    )

    return {
        "ok": research["ok"],
        "bridge_version": BRIDGE_VERSION,
        "web_mode": mode,
        "web_used": True,
        "web_evidence_present": research["evidence_present"],
        "evidence_sufficient": research["evidence_sufficient"],
        "confidence": research["confidence"],
        "decision": "NEED_WEB",
        "reason": plan["reason"],
        "planner_model": planner_model,
        "model": synthesis_model,
        "selection_mode": synthesis_selection_mode,
        "content": (
            research["answer"]
            if research["answer"]
            else (
                "The helper determined that current web evidence was needed but could not "
                "verify the task within its bounded research budget. "
                + (research["missing"] or "")
            ).strip()
        ),
        "missing": research["missing"],
        "sources": research["results"],
        "source_count": research["result_count"],
        "search_queries": research["queries"],
        "search_attempts": research["search_attempts"],
        "research_rounds": research["research_rounds"],
        "research_round_count": research["round_count"],
        "planner_parse_ok": plan["planner_parse_ok"],
        "planner_usage": plan["planner_usage"],
        "planner_inference_seconds": plan["planner_inference_seconds"],
        "warning": (
            None
            if research["evidence_sufficient"]
            else "The local research budget ended without sufficient evidence; use a higher-level fallback if verification is required."
        ),
    }


@mcp.tool()
def ask_local_ai_web(
    task: str,
    context: str = "",
    model: str = "auto",
    task_type: str = "research",
    output_kind: str = "concise research answer",
    acceptance_criteria: str = "",
    search_queries: list[str] | None = None,
    max_search_results: int = 6,
) -> dict[str, Any]:
    """Compatibility shortcut for ask_local_ai(..., web_mode="required").

    Unlike the older implementation, this does NOT rely on the local model to invoke
    a search tool. The bridge performs deterministic OpenWebUI/SearXNG search first,
    then asks one local model to synthesize the recorded evidence.
    """
    return ask_local_ai(
        task=task,
        context=context,
        model=model,
        task_type=task_type,
        web_mode="required",
        output_kind=output_kind,
        acceptance_criteria=acceptance_criteria,
        search_queries=search_queries,
        max_search_results=max_search_results,
    )


if __name__ == "__main__":
    mcp.run()
