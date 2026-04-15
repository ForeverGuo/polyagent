import json
import os
import re
import requests
import yaml
from langchain_core.tools import tool

# 常见 Swagger/OpenAPI 文档自动探测路径（按优先级排列）
_SWAGGER_PROBE_PATHS = [
    "/swagger.json",
    "/openapi.json",
    "/api-docs",
    "/v2/api-docs",
    "/v3/api-docs",
    "/swagger/v1/swagger.json",
    "/swagger/v2/swagger.json",
    "/api/swagger.json",
    "/api/openapi.json",
    "/docs/swagger.json",
    "/swagger-ui/swagger.json",
]


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/yaml,text/plain,*/*;q=0.8",
}


def _try_fetch(url: str) -> tuple[str, str] | None:
    """尝试请求一个 URL，返回 (content, content_type) 或 None"""
    try:
        resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text, resp.headers.get("Content-Type", "")
    except Exception:
        pass
    return None


def _extract_spec_url_from_html(html: str, base_url: str) -> str | None:
    """从 Swagger UI 页面 HTML 中提取 spec JSON 地址"""
    # 匹配 SwaggerUIBundle({ url: "..." }) 或 url: "..."
    patterns = [
        r'url\s*:\s*["\']([^"\']+\.(?:json|yaml))["\']',
        r'url\s*:\s*["\']([^"\']+api-docs[^"\']*)["\']',
        r'"swagger-ui"\s*,\s*\{[^}]*url\s*:\s*["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            found = m.group(1)
            if found.startswith("http"):
                return found
            return base_url.rstrip("/") + "/" + found.lstrip("/")
    return None


def _autodiscover_swagger(base_url: str) -> tuple[str, str] | None:
    """
    给定 API 根地址，自动探测 Swagger 文档位置。
    返回 (content, content_type) 或 None。
    """
    # 先探测常见路径
    for path in _SWAGGER_PROBE_PATHS:
        url = base_url.rstrip("/") + path
        result = _try_fetch(url)
        if result:
            content, ct = result
            stripped = content.lstrip()
            if stripped.startswith("{") or stripped.startswith("swagger") or "openapi" in stripped[:200]:
                return result

    # 尝试从根页面 HTML 中提取 spec URL（Swagger UI 页面）
    root_result = _try_fetch(base_url)
    if root_result:
        html, ct = root_result
        if "text/html" in ct or html.lstrip().startswith("<"):
            spec_url = _extract_spec_url_from_html(html, base_url)
            if spec_url:
                return _try_fetch(spec_url)

    return None


@tool
def fetch_swagger(source: str) -> str:
    """
    获取并解析 Swagger/OpenAPI 文档，返回结构化的 JSON 字符串。
    source 可以是：
    - API 根地址（如 https://api.example.com），会自动探测 swagger 文档位置
    - 直接指向文档的 URL（如 https://petstore.swagger.io/v2/swagger.json）
    - 本地文件路径（如 ./api/swagger.yaml）
    """
    if source.startswith("http://") or source.startswith("https://"):
        resp = requests.get(source, headers=_BROWSER_HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        content = resp.text
        content_type = resp.headers.get("Content-Type", "")
    else:
        with open(os.path.expanduser(source), "r", encoding="utf-8") as f:
            content = f.read()
        content_type = ""

    # 检测是否为 HTML 页面 → 启动自动探测
    stripped = content.lstrip()
    if (stripped.startswith("<!") or stripped.startswith("<html") or "text/html" in content_type) \
            and source.startswith("http"):
        discovered = _autodiscover_swagger(source)
        if discovered:
            content, content_type = discovered
            stripped = content.lstrip()
        else:
            return (
                "错误：无法在以下地址找到 Swagger/OpenAPI 文档：\n"
                f"  {source}\n\n"
                "已自动探测以下路径均未找到：\n" +
                "\n".join(f"  {source.rstrip('/')}{p}" for p in _SWAGGER_PROBE_PATHS) +
                "\n\n请直接提供 swagger.json 或 openapi.json 的完整 URL。"
            )

    # 支持 JSON 和 YAML 格式
    spec = None
    try:
        spec = json.loads(content)
    except json.JSONDecodeError:
        pass

    if spec is None:
        try:
            spec = yaml.safe_load(content)
        except yaml.YAMLError as e:
            return f"错误：无法解析文档内容，既不是合法的 JSON 也不是合法的 YAML。\n详情：{e}"

    if not isinstance(spec, dict):
        return f"错误：解析结果不是有效的 Swagger 文档（期望 dict，实际为 {type(spec).__name__}）。"

    # 验证是否包含 Swagger/OpenAPI 必要字段
    if "paths" not in spec and "openapi" not in spec and "swagger" not in spec:
        return (
            "错误：文档缺少 Swagger/OpenAPI 必要字段（paths / openapi / swagger）。\n"
            "请确认提供的是 Swagger/OpenAPI 规范文档。"
        )

    # 提取关键信息，避免 token 超限
    summary = _extract_summary(spec)
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _extract_summary(spec: dict) -> dict:
    """提取 Swagger 文档的核心信息：端点列表、参数、响应"""
    result = {
        "info": spec.get("info", {}),
        "servers": spec.get("servers") or _build_server(spec),
        "endpoints": [],
    }

    paths = spec.get("paths", {})
    for path, path_item in paths.items():
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            operation = path_item.get(method)
            if not operation:
                continue

            endpoint = {
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary", ""),
                "operationId": operation.get("operationId", ""),
                "parameters": _extract_params(operation.get("parameters", [])),
                "requestBody": _extract_request_body(operation.get("requestBody")),
                "responses": _extract_responses(operation.get("responses", {})),
            }
            result["endpoints"].append(endpoint)

    return result


def _build_server(spec: dict) -> list:
    """兼容 Swagger 2.0 的 host/basePath"""
    host = spec.get("host", "localhost")
    base = spec.get("basePath", "/")
    scheme = "https" if "https" in spec.get("schemes", []) else "http"
    return [{"url": f"{scheme}://{host}{base}"}]


def _extract_params(params: list) -> list:
    return [
        {
            "name": p.get("name"),
            "in": p.get("in"),
            "required": p.get("required", False),
            "type": p.get("schema", {}).get("type") or p.get("type", "string"),
            "description": p.get("description", ""),
        }
        for p in params
    ]


def _extract_request_body(body) -> dict | None:
    if not body:
        return None
    content = body.get("content", {})
    for media_type, media_obj in content.items():
        schema = media_obj.get("schema", {})
        return {
            "mediaType": media_type,
            "required": body.get("required", False),
            "schema": schema,
        }
    return None


def _extract_responses(responses: dict) -> dict:
    return {
        code: {
            "description": resp.get("description", ""),
        }
        for code, resp in responses.items()
    }
