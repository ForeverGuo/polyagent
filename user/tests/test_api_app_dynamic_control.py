import os
import json as _json
import pytest
import requests

# ─── 运行配置 ────────────────────────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
TOKEN    = os.environ.get("TOKEN", "")

# Token 与 Authorization 设置相同的值，兼容不同接口的鉴权方式
HEADERS = {"Content-Type": "application/json"}
if TOKEN:
    HEADERS["Token"]         = TOKEN
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


def _req(method: str, path: str, **kwargs) -> requests.Response:
    """统一请求入口：打印结构化日志供报告采集，不向控制台输出多余内容。"""
    url     = f"{BASE_URL}{path}"
    params  = kwargs.get("params")
    payload = kwargs.get("json")

    print(f"[REQ] {method.upper()} {url}")
    print(f"[HEADERS] {_json.dumps(HEADERS, ensure_ascii=False)}")
    if params:
        print(f"[PARAMS] {_json.dumps(params, ensure_ascii=False)}")
    if payload:
        print(f"[BODY] {_json.dumps(payload, ensure_ascii=False)}")

    resp = requests.request(method, url, headers=HEADERS, timeout=15, **kwargs)

    try:
        body = _json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        body = resp.text
    print(f"[RSP] {resp.status_code} {body[:1000]}")
    return resp


class TestAppDynamicControlQuery:
    """App 动态版本控制配置查询接口"""

    def test_01_load_mini_program(self):
        """查询小程序平台最新配置（platform=1）"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": 1})
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("code") == 200, f"响应码异常: {data}"
        assert data.get("data") is not None
        # 验证返回数据结构
        config = data["data"]
        assert config.get("platform") == 1
        assert config.get("appVersionName") is not None
        assert config.get("upLine") is not None

    def test_02_load_android(self):
        """查询 Android 平台最新配置（platform=2）"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": 2})
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("code") == 200, f"响应码异常: {data}"
        assert data.get("data") is not None
        config = data["data"]
        assert config.get("platform") == 2

    def test_03_load_ios(self):
        """查询 iOS 平台最新配置（platform=3）"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": 3})
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("code") == 200, f"响应码异常: {data}"
        assert data.get("data") is not None
        config = data["data"]
        assert config.get("platform") == 3

    def test_04_check_items_structure(self):
        """验证返回的 items 数组结构"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": 1})
        assert resp.status_code in (200, 201)
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        # items 可能为空数组，但必须存在
        assert isinstance(items, list)
        if items:
            # 验证第一个 item 的结构
            item = items[0]
            assert "controlId" in item
            assert "controlKey" in item
            assert "controlAction" in item

    def test_05_check_response_fields(self):
        """验证响应包含所有必要字段"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": 1})
        assert resp.status_code in (200, 201)
        data = resp.json()
        # 验证顶层字段
        assert "code" in data
        assert "msg" in data
        assert "data" in data
        assert "requestId" in data
        # 验证 data 字段结构
        config = data["data"]
        assert "platform" in config
        assert "appVersionName" in config
        assert "appVersionCode" in config
        assert "upLine" in config
        assert "items" in config


class TestAppDynamicControlValidation:
    """App 动态版本控制配置参数校验"""

    def test_01_missing_platform(self):
        """缺少必填参数 platform → 400/422"""
        resp = _req("GET", "/appDynamicControl/loadLatest")
        assert resp.status_code in (400, 401, 403, 422)

    def test_02_invalid_platform_zero(self):
        """无效的 platform 值：0 → 400/422"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": 0})
        assert resp.status_code in (400, 404, 422)

    def test_03_invalid_platform_negative(self):
        """无效的 platform 值：负数 → 400/422"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": -1})
        assert resp.status_code in (400, 404, 422)

    def test_04_invalid_platform_large(self):
        """无效的 platform 值：过大值 → 400/404"""
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": 999})
        # 可能返回 400 参数错误，也可能返回 404 未找到配置
        assert resp.status_code in (400, 404, 422)

    def test_05_invalid_platform_type(self):
        """无效的 platform 值：字符串 → 400/422"""
        # 某些框架可能自动转换，这里测试不标准格式
        resp = _req("GET", "/appDynamicControl/loadLatest", params={"platform": "abc"})
        assert resp.status_code in (400, 404, 422)
