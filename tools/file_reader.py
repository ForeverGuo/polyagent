import os
import requests
from langchain_core.tools import tool

_SUPPORTED_EXTENSIONS = (".txt", ".md", ".markdown")


@tool
def read_spec_file(source: str) -> str:
    """
    读取 txt 或 md 格式的 API/功能描述文件，返回文本内容，用于生成测试用例。
    source 可以是：
    - 线上 URL（如 https://example.com/api.md 或 https://s.apifox.cn/xxx/llms.txt）
    - 本地文件路径（如 ./docs/api.md）
    """
    if source.startswith("http://") or source.startswith("https://"):
        return _fetch_remote(source)
    else:
        return _read_local(source)


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/plain,text/markdown,text/html,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _fetch_remote(url: str) -> str:
    try:
        resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        return f"错误：请求失败（HTTP {e.response.status_code}）：{url}"
    except requests.exceptions.RequestException as e:
        return f"错误：无法访问 URL：{url}\n详情：{e}"

    content_type = resp.headers.get("Content-Type", "")

    # 拒绝 HTML 页面
    if "text/html" in content_type and not _looks_like_markdown(resp.text):
        return (
            f"错误：该 URL 返回的是 HTML 页面，不是 txt/md 文档。\n"
            f"Content-Type: {content_type}\nURL: {url}"
        )

    content = resp.text
    if not content.strip():
        return f"错误：文件内容为空：{url}"

    return f"[来源：{url}]\n\n{content}"


def _read_local(file_path: str) -> str:
    path = os.path.expanduser(file_path)

    if not os.path.exists(path):
        return f"错误：文件不存在：{path}"

    ext = os.path.splitext(path)[1].lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        return (
            f"错误：不支持的文件类型 '{ext}'。\n"
            f"支持的格式：{', '.join(_SUPPORTED_EXTENSIONS)}"
        )

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        return f"错误：文件内容为空：{path}"

    return f"[文件：{path}]\n\n{content}"


def _looks_like_markdown(text: str) -> bool:
    """简单判断内容是否像 Markdown/文本而非纯 HTML"""
    stripped = text.lstrip()
    return not (stripped.startswith("<!") or stripped.startswith("<html"))
