"""
Appium 移动端自动化工具集（Android MVP 版本）。

复用 browser.py 的独立工作线程模式：Appium session 也绑定创建它的线程，
所以用一个专用后台线程独占 driver，所有操作通过队列派发到该线程执行。

使用前提：
  1. 安装 Appium Server：npm install -g appium && appium driver install uiautomator2
  2. 启动服务：appium --port 4723
  3. 连接 Android 设备或模拟器：adb devices 能看到设备
  4. .env 配置 APPIUM_SERVER / APP_PLATFORM / APP_DEVICE_NAME
"""
import os
import queue
import threading
from pathlib import Path
from langchain_core.tools import tool

# ─── 配置 ──────────────────────────────────────────────────────────────────────

APPIUM_SERVER   = os.getenv("APPIUM_SERVER", "http://localhost:4723")
APP_PLATFORM    = os.getenv("APP_PLATFORM", "Android")
APP_DEVICE_NAME = os.getenv("APP_DEVICE_NAME", "emulator-5554")
APP_PACKAGE     = os.getenv("APP_PACKAGE", "")
APP_ACTIVITY    = os.getenv("APP_ACTIVITY", "")

_OUT = os.environ.get("POLYAGENT_OUTPUT_DIR", "user")
_SCREENSHOT_DIR = os.path.join(_OUT, "screenshots")

# ─── 专用 Appium 工作线程 ──────────────────────────────────────────────────────

_task_queue: queue.Queue = queue.Queue()
_worker_thread: threading.Thread | None = None
_worker_started = threading.Event()
_driver_state: dict = {}


def _build_driver():
    """创建 Appium Android driver。延迟导入，避免未装 Appium 时影响其他链路。"""
    from appium import webdriver
    from appium.options.android import UiAutomator2Options

    opts = UiAutomator2Options()
    opts.platform_name = APP_PLATFORM
    opts.device_name = APP_DEVICE_NAME
    opts.automation_name = "UiAutomator2"
    opts.no_reset = True                # 保留 App 登录态
    opts.new_command_timeout = 300
    if APP_PACKAGE:
        opts.app_package = APP_PACKAGE
    if APP_ACTIVITY:
        opts.app_activity = APP_ACTIVITY
    return webdriver.Remote(APPIUM_SERVER, options=opts)


def _appium_worker():
    """后台线程：独占 Appium driver，循环处理任务队列"""
    try:
        driver = _build_driver()
    except Exception as e:
        _driver_state["error"] = str(e)
        _worker_started.set()
        return
    _driver_state["driver"] = driver
    _worker_started.set()

    while True:
        task = _task_queue.get()
        if task is None:
            break
        fn, result_holder = task
        try:
            result_holder["result"] = fn(_driver_state["driver"])
        except Exception as e:
            result_holder["result"] = f"错误：{e}"
        finally:
            result_holder["done"].set()

    try:
        _driver_state.get("driver") and _driver_state["driver"].quit()
    except Exception:
        pass
    _driver_state.clear()


def _ensure_worker():
    """确保后台线程已启动。"""
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_started.clear()
        _driver_state.clear()
        _worker_thread = threading.Thread(target=_appium_worker, daemon=True)
        _worker_thread.start()
        _worker_started.wait(timeout=60)  # Appium 冷启动较慢
        if "error" in _driver_state:
            raise RuntimeError(
                f"Appium 启动失败：{_driver_state['error']}\n"
                f"请检查：1) appium server 是否已启动（{APPIUM_SERVER}）"
                f"  2) adb devices 能否看到设备  3) .env 配置是否正确"
            )


def _run(fn, timeout: int = 35) -> str:
    """将一个 fn(driver) -> str 派发到 Appium 线程执行，阻塞等待结果"""
    _ensure_worker()
    result_holder = {"result": None, "done": threading.Event()}
    _task_queue.put((fn, result_holder))
    finished = result_holder["done"].wait(timeout=timeout)
    if not finished:
        return f"错误：操作超时（>{timeout}s）"
    return result_holder["result"] or "错误：无返回结果"


# ─── 选择器解析 ────────────────────────────────────────────────────────────────

def _parse_selector(selector: str):
    """
    统一选择器格式，返回 (By 常量, value)。支持：
      - text=登录        → 按可见文本
      - id=com.xx/login  → resource-id
      - desc=返回按钮    → content-description
      - xpath=//...      → 原生 XPath
      - 其他             → 当作 XPath
    """
    from appium.webdriver.common.appiumby import AppiumBy

    s = selector.strip()
    if s.startswith("text="):
        text = s[5:]
        return AppiumBy.XPATH, f'//*[@text="{text}"]'
    if s.startswith("id="):
        return AppiumBy.ID, s[3:]
    if s.startswith("desc="):
        return AppiumBy.ACCESSIBILITY_ID, s[5:]
    if s.startswith("xpath="):
        return AppiumBy.XPATH, s[6:]
    return AppiumBy.XPATH, s


def _find(driver, selector: str, timeout: int = 5):
    """查找元素，失败返回 None（不抛异常）"""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    by, value = _parse_selector(selector)
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
    except Exception:
        return None


# ─── 工具定义 ──────────────────────────────────────────────────────────────────

@tool
def app_launch(package: str = "", activity: str = "") -> str:
    """
    启动指定 App。如果 session 未开启会自动创建 driver。
    package：App 包名（如 com.tencent.mm）
    activity：启动 Activity（可选，默认用 App 入口）
    留空则使用 .env 中的 APP_PACKAGE / APP_ACTIVITY。
    """
    def _fn(driver):
        pkg = package or APP_PACKAGE
        act = activity or APP_ACTIVITY
        if not pkg:
            return "错误：未指定包名（参数或 APP_PACKAGE 环境变量至少给一个）"
        try:
            if act:
                driver.start_activity(pkg, act)
            else:
                driver.activate_app(pkg)
            current = driver.current_activity
            return f"已启动：{pkg}\n当前 Activity：{current}"
        except Exception as e:
            return f"启动失败：{e}"
    return _run(_fn, timeout=60)


@tool
def app_click(selector: str) -> str:
    """
    点击元素。selector 支持：
      - text=登录             按可见文本
      - id=com.xx/btn_login   按 resource-id
      - desc=返回              按 content-description
      - xpath=//android...    原生 XPath

    元素不存在时返回 ❌ 错误信息（含截图），不抛异常。
    """
    def _fn(driver):
        el = _find(driver, selector, timeout=5)
        if el is None:
            try:
                os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
                driver.save_screenshot(f"{_SCREENSHOT_DIR}/app_click_fail.png")
            except Exception:
                pass
            return f"❌ 点击失败：未找到元素 {selector}（截图已保存 app_click_fail.png）"
        el.click()
        return f"已点击：{selector}"
    return _run(_fn)


@tool
def app_fill(selector: str, value: str) -> str:
    """
    在输入框填入文字（先清空再输入）。
    selector：输入框选择器
    value：要填入的内容
    """
    def _fn(driver):
        el = _find(driver, selector, timeout=5)
        if el is None:
            return f"❌ 未找到输入框：{selector}"
        el.clear()
        el.send_keys(value)
        return f"已在 {selector} 填写：{value}"
    return _run(_fn)


@tool
def app_assert_visible(selector: str, timeout_ms: int = 5000) -> str:
    """
    断言元素在屏幕上可见。
    - 元素存在且可见 → 返回 ✅ 断言通过 + 文本
    - 不存在 / 超时 → 返回 ❌ 断言失败（自动截图）
    """
    def _fn(driver):
        el = _find(driver, selector, timeout=timeout_ms // 1000 or 1)
        if el is None or not el.is_displayed():
            try:
                os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
                driver.save_screenshot(f"{_SCREENSHOT_DIR}/app_assert_fail.png")
            except Exception:
                pass
            return f"❌ 断言失败：{selector} 在 {timeout_ms}ms 内未可见"
        text = (el.text or "").strip()[:200]
        return f"✅ 断言通过：{selector} 可见\n内容：{text}"
    return _run(_fn)


@tool
def app_screenshot(filename: str = "app_screenshot.png") -> str:
    """
    对当前屏幕截图，保存到 user/screenshots/。
    filename：文件名，如 home.png / login_result.png
    """
    def _fn(driver):
        os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
        name = filename if filename.endswith(".png") else filename + ".png"
        path = os.path.join(_SCREENSHOT_DIR, name)
        driver.save_screenshot(path)
        return f"截图已保存：{path}"
    return _run(_fn)


@tool
def app_scroll(direction: str = "down") -> str:
    """
    滚动屏幕，加载列表后续内容或找到下方元素。
    direction：down（向下滑，页面向上）/ up / left / right
    """
    def _fn(driver):
        size = driver.get_window_size()
        w, h = size["width"], size["height"]
        cx, cy = w // 2, h // 2
        if direction == "down":
            sx, sy, ex, ey = cx, int(h * 0.75), cx, int(h * 0.25)
        elif direction == "up":
            sx, sy, ex, ey = cx, int(h * 0.25), cx, int(h * 0.75)
        elif direction == "left":
            sx, sy, ex, ey = int(w * 0.75), cy, int(w * 0.25), cy
        elif direction == "right":
            sx, sy, ex, ey = int(w * 0.25), cy, int(w * 0.75), cy
        else:
            return f"错误：不支持的方向 {direction}（支持 down/up/left/right）"
        driver.swipe(sx, sy, ex, ey, duration=400)
        return f"已向 {direction} 滑动"
    return _run(_fn)


@tool
def app_back() -> str:
    """按 Android 物理返回键，退出当前页。"""
    def _fn(driver):
        driver.back()
        return "已按返回键"
    return _run(_fn)


@tool
def app_get_page_source() -> str:
    """
    获取当前页面的 XML 结构（截取前 4000 字符避免超 token）。
    用于分析页面元素、找不到选择器时先调用看有哪些控件。
    """
    def _fn(driver):
        src = driver.page_source
        if len(src) > 4000:
            src = src[:4000] + "\n...(已截断)"
        return src
    return _run(_fn)


@tool
def app_get_form_fields() -> str:
    """
    扫描当前屏幕所有可编辑输入框（EditText），返回每个字段的：
    resource-id、hint/text、content-desc、类型。
    填写表单前必须先调用，避免瞎填。
    """
    def _fn(driver):
        from appium.webdriver.common.appiumby import AppiumBy
        try:
            els = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
        except Exception as e:
            return f"扫描失败：{e}"
        if not els:
            return "当前页面未发现 EditText 输入框"
        lines = ["当前屏幕输入框："]
        for i, el in enumerate(els, 1):
            try:
                rid = el.get_attribute("resource-id") or ""
                text = (el.text or "").strip()
                hint = el.get_attribute("hint") or ""
                desc = el.get_attribute("content-desc") or ""
                label = hint or desc or text or "(无提示)"
                sel = f"id={rid}" if rid else f"desc={desc}" if desc else "xpath=需根据父容器定位"
                lines.append(f"[{i}] 提示: {label}  选择器: {sel}")
            except Exception:
                continue
        return "\n".join(lines)
    return _run(_fn)


@tool
def app_human_intervention(message: str) -> str:
    """
    暂停自动化，请求人工介入。适用于：
      - 短信/图形验证码
      - 不知道账号密码
      - 复杂手势或实名认证

    调用后截图当前屏幕，终端打印提示，阻塞等待人工按 Enter 继续（最多 10 分钟）。
    返回值包含人工操作前后的截图路径和当前 Activity。
    """
    def _fn(driver):
        try:
            os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
            driver.save_screenshot(f"{_SCREENSHOT_DIR}/app_human_before.png")
        except Exception:
            pass

        border = "=" * 60
        print(f"\n{border}")
        print(f"🙋 需要人工介入（App 测试）")
        print(f"   {message}")
        print(f"   当前截图：{_SCREENSHOT_DIR}/app_human_before.png")
        print(f"   ⏎  完成操作后请按 Enter 键继续...")
        print(f"{border}\n", flush=True)
        input()

        after_activity = ""
        after_shot = ""
        try:
            after_activity = driver.current_activity
            driver.save_screenshot(f"{_SCREENSHOT_DIR}/app_human_after.png")
            after_shot = f"{_SCREENSHOT_DIR}/app_human_after.png"
        except Exception:
            pass

        return (
            f"✅ 人工介入完成\n"
            f"━━ 操作前截图：{_SCREENSHOT_DIR}/app_human_before.png\n"
            f"━━ 操作后截图：{after_shot}\n"
            f"━━ 当前 Activity：{after_activity}"
        )
    return _run(_fn, timeout=600)


@tool
def app_close() -> str:
    """关闭 Appium session，释放 driver。所有测试完成后调用。"""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        _task_queue.put(None)
        _worker_thread.join(timeout=15)
        _worker_thread = None
    return "Appium session 已关闭"


# 所有工具列表，供 ToolNode 使用
ALL_APP_TOOLS = [
    app_launch,
    app_click,
    app_fill,
    app_assert_visible,
    app_screenshot,
    app_scroll,
    app_back,
    app_get_page_source,
    app_get_form_fields,
    app_human_intervention,
    app_close,
]
