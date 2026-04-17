from dotenv import load_dotenv
import os
import json
import time
import functools
from pathlib import Path
import httpx
from langchain_openai import ChatOpenAI

# 优先加载用户全局配置（polyagent init 写入），再 fallback 到当前目录 .env
_global_config = Path.home() / ".polyagent" / "config.env"
if _global_config.exists():
    load_dotenv(_global_config)
else:
    load_dotenv()

MODEL    = os.getenv("MODEL")
API_BASE = os.getenv("API_BASE")
API_KEY  = os.getenv("API_KEY")


def node_retry(max_attempts: int = 3, base_delay: float = 3.0):
    """
    节点级重试装饰器。

    捕获 LLM API 返回的临时错误（503 / 500 / RateLimit / 超时等）并自动重试，
    不修改 LLM 对象本身，因此与 bind_tools / with_structured_output 完全兼容。

    用法：
        @node_retry()
        def my_node(state): ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    err_str = str(exc).lower()
                    # 只对临时性错误重试：
                    #   - 503 / 500 / rate limit / timeout / connection（API 层错误）
                    #   - json.JSONDecodeError / "expected value"：LLM 返回空流时
                    #     with_structured_output 解析失败，根源仍是 API 不稳定
                    retryable = (
                        isinstance(exc, json.JSONDecodeError)
                        or any(k in err_str for k in (
                            "503", "500", "rate limit", "ratelimit",
                            "timeout", "timed out", "connection", "overload",
                            "too many requests", "expected value",
                            "output_parsing", "outputparsingerror",
                        ))
                    )
                    if retryable and attempt < max_attempts - 1:
                        wait = base_delay * (2 ** attempt)   # 3s, 6s, 12s
                        print(f"\n[retry] LLM 暂时不可用，{wait:.0f}s 后重试"
                              f"（{attempt + 1}/{max_attempts - 1}）…", flush=True)
                        time.sleep(wait)
                        last_exc = exc
                    else:
                        raise
            raise last_exc  # 超出重试次数，抛出最后一次异常
        return wrapper
    return decorator


def _build_http_client() -> httpx.Client | None:
    """根据环境变量构造自定义 httpx 客户端（处理企业 SSL 代理）。"""
    ssl_verify_env = os.getenv("SSL_VERIFY", "true").lower()
    ca_bundle = os.getenv("SSL_CA_BUNDLE", "")

    if ssl_verify_env in ("false", "0", "no"):
        verify: bool | str = False
    elif ca_bundle:
        verify = ca_bundle
    else:
        return None  # 使用默认客户端

    return httpx.Client(verify=verify)


def get_llm(max_tokens: int = 8192):
    http_client = _build_http_client()
    kwargs = dict(
        model=MODEL,
        api_key=API_KEY,
        base_url=API_BASE,
        temperature=0.7,
        max_tokens=max_tokens,
        streaming=True,
    )
    if http_client is not None:
        kwargs["http_client"] = http_client
    return ChatOpenAI(**kwargs)
