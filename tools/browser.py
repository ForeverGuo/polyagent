"""
Playwright 浏览器工具集。

由于 sync_playwright 绑定创建它的线程，而 LangGraph ToolNode 可能在不同线程调用工具，
因此用一个专用后台线程独占 Playwright，所有操作通过队列派发到该线程执行。
"""
import os
import queue
import threading
from urllib.parse import urlparse, parse_qs
from langchain_core.tools import tool

_OUT = os.environ.get("POLYAGENT_OUTPUT_DIR", "user")
_SCREENSHOT_DIR = os.path.join(_OUT, "screenshots")

# ─── 网络请求捕获状态 ──────────────────────────────────────────────────────────

_capture_lock = threading.Lock()
_capture_state: dict = {
    "active": False,
    "filter": "",
    "requests": [],
}

_SESSION_DIR = os.path.join(_OUT, "session")
_browser_state: dict = {}  # 持有 pw / browser / context / page 引用，供 save/load state 使用

def _on_response(response):
    """Playwright response 事件回调，在 Playwright 工作线程中执行"""
    request = response.request
    if request.resource_type not in ("xhr", "fetch"):
        return
    url = request.url
    with _capture_lock:
        if not _capture_state["active"]:
            return
        if _capture_state["filter"] and _capture_state["filter"] not in url:
            return
        try:
            body = response.text()
            if len(body) > 800:
                body = body[:800] + "...(截断)"
        except Exception:
            body = "(无法读取响应体)"
        entry = {
            "url": url,
            "method": request.method,
            "post_data": request.post_data,
            "status": response.status,
            "response_body": body,
        }
        _capture_state["requests"].append(entry)


# ─── 专用 Playwright 线程 ──────────────────────────────────────────────────────

_task_queue: queue.Queue = queue.Queue()
_worker_thread: threading.Thread | None = None
_worker_started = threading.Event()


_CLOSED_KEYWORDS = (
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "Target closed",
    "has been closed",
    "Connection closed",
    "Execution context was destroyed",
)


def _recreate_page_in_worker():
    """
    在 Playwright 工作线程内重建 context + page。
    当页面/context 崩溃或被关闭后调用，尽量复用已有 browser；
    若 browser 也断开则完整重建。
    返回新 page 对象。
    """
    browser = _browser_state.get("browser")
    pw = _browser_state.get("pw")
    try:
        # 先尝试用现有 browser 创建新 context
        if not browser or not browser.is_connected():
            raise RuntimeError("browser disconnected")
        old_ctx = _browser_state.get("context")
        new_ctx = browser.new_context()
        new_page = new_ctx.new_page()
        new_page.set_viewport_size({"width": 1280, "height": 800})
        new_page.on("response", _on_response)
        _browser_state["context"] = new_ctx
        _browser_state["page"] = new_page
        try:
            old_ctx and old_ctx.close()
        except Exception:
            pass
        return new_page
    except Exception:
        # browser 也坏了，完整重建 browser + context + page
        try:
            browser and browser.close()
        except Exception:
            pass
        new_browser = pw.chromium.launch(headless=False)
        new_ctx = new_browser.new_context()
        new_page = new_ctx.new_page()
        new_page.set_viewport_size({"width": 1280, "height": 800})
        new_page.on("response", _on_response)
        _browser_state["browser"] = new_browser
        _browser_state["context"] = new_ctx
        _browser_state["page"] = new_page
        return new_page


def _playwright_worker():
    """后台线程：独占 Playwright 实例，循环处理任务队列"""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    _browser_state["pw"] = pw
    browser = pw.chromium.launch(headless=False)
    _browser_state["browser"] = browser
    context = browser.new_context()
    _browser_state["context"] = context
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 800})
    page.on("response", _on_response)  # 注册持久网络监听器（含响应体）
    _browser_state["page"] = page

    _worker_started.set()  # 通知主线程浏览器已就绪

    while True:
        task = _task_queue.get()
        if task is None:  # 退出信号
            break
        fn, result_holder = task
        try:
            result_holder["result"] = fn(_browser_state["page"])
        except Exception as e:
            err_str = str(e)
            if any(kw in err_str for kw in _CLOSED_KEYWORDS):
                # page/browser 已关闭，自动恢复后重试一次
                try:
                    new_page = _recreate_page_in_worker()
                    result_holder["result"] = fn(new_page)
                except Exception as e2:
                    result_holder["result"] = f"错误（浏览器恢复失败）：{e2}"
            else:
                result_holder["result"] = f"错误：{e}"
        finally:
            result_holder["done"].set()

    try:
        _browser_state.get("page", None) and _browser_state["page"].close()
        _browser_state.get("context", None) and _browser_state["context"].close()
        _browser_state.get("browser", None) and _browser_state["browser"].close()
        pw.stop()
    except Exception:
        pass
    # 清理状态，避免下次 _ensure_worker 拿到失效引用
    _browser_state.clear()


def _ensure_worker():
    """确保后台线程已启动"""
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_started.clear()
        _worker_thread = threading.Thread(target=_playwright_worker, daemon=True)
        _worker_thread.start()
        _worker_started.wait(timeout=15)  # 等待浏览器启动


def _run_in_browser(fn, timeout: int = 35) -> str:
    """将一个 fn(page) -> str 派发到 Playwright 线程执行，阻塞等待结果"""
    _ensure_worker()
    result_holder = {"result": None, "done": threading.Event()}
    _task_queue.put((fn, result_holder))
    finished = result_holder["done"].wait(timeout=timeout)
    if not finished:
        return f"错误：操作超时（>{timeout}s）"
    return result_holder["result"] or "错误：无返回结果"


# ─── 工具定义 ──────────────────────────────────────────────────────────────────

@tool
def browser_navigate(url: str) -> str:
    """
    打开指定 URL，等待页面加载完成。
    返回页面标题和当前 URL。
    """
    def _fn(page):
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"已导航至：{page.url}\n页面标题：{page.title()}"
    return _run_in_browser(_fn)


@tool
def browser_assert_visible(selector: str, timeout_ms: int = 5000) -> str:
    """
    断言某个元素在页面上可见，用于验证 UI 状态。
    - 元素存在且可见 → 返回元素文本，前缀 "✅ 断言通过"
    - 元素不存在或不可见 → 返回 "❌ 断言失败"，不抛异常，让 LLM 记录 FAIL

    selector: CSS 选择器或 text=文本
    timeout_ms: 等待元素出现的最长时间（默认 5000ms）
    """
    def _fn(page):
        try:
            page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
            el = page.query_selector(selector)
            text = (el.inner_text() if el else "").strip()[:200]
            return f"✅ 断言通过：{selector} 可见\n内容：{text}"
        except Exception:
            # 尝试截图帮助定位问题
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/assert_fail.png", full_page=False)
            except Exception:
                pass
            return f"❌ 断言失败：{selector} 在 {timeout_ms}ms 内未找到或不可见（截图已保存 assert_fail.png）"
    return _run_in_browser(_fn)


@tool
def browser_click(selector: str) -> str:
    """
    点击页面元素，点击前自动检查元素是否存在。selector 支持：
    - CSS 选择器（如 button.submit、#login-btn）
    - 文本内容（如 text=登录、text=Submit）
    - 组合（如 role=button[name=确认]）

    若元素不存在，返回错误信息而不是抛出异常，避免整个测试流程崩溃。
    """
    def _fn(page):
        # 点击前先确认元素存在
        el = page.query_selector(selector)
        if el is None:
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/click_fail.png", full_page=False)
            except Exception:
                pass
            return f"❌ 点击失败：未找到元素 {selector}（截图已保存 click_fail.png）\n当前页面：{page.url}"
        page.click(selector, timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        return f"已点击：{selector}\n当前页面：{page.url}"
    return _run_in_browser(_fn)


@tool
def browser_fill(selector: str, value: str) -> str:
    """
    直接设置输入框的值（不触发键盘事件）。适合表单自动填充场景。
    如果需要模拟真实用户逐字输入并触发联想词，请用 browser_type。
    """
    def _fn(page):
        page.fill(selector, value, timeout=10000)
        return f"已在 {selector} 填写：{value}"
    return _run_in_browser(_fn)


@tool
def browser_type(selector: str, text: str, delay_ms: int = 80) -> str:
    """
    模拟真实用户逐字符输入，触发 keydown/keypress/keyup/input 等所有键盘事件。
    适合测试搜索框联想词、实时校验、autocomplete 等交互功能。

    selector：输入框选择器（如 input[name=wd]、#kw）
    text：要输入的文字
    delay_ms：每个字符之间的延迟毫秒数（默认 80ms，模拟正常打字速度）
    """
    def _fn(page):
        page.click(selector, timeout=10000)          # 先点击聚焦
        page.type(selector, text, delay=delay_ms)    # 逐字符输入
        return f"已逐字输入：{text}（{len(text)} 个字符，延迟 {delay_ms}ms/字）"
    return _run_in_browser(_fn)


@tool
def browser_wait_for(selector: str, timeout_ms: int = 5000) -> str:
    """
    等待某个元素出现在页面上（如联想词下拉框、加载完成标志）。
    selector：CSS 选择器或 text=文本
    timeout_ms：最长等待毫秒数（默认 5000ms）
    返回元素出现后的文本内容，超时则返回提示信息。
    """
    def _fn(page):
        try:
            page.wait_for_selector(selector, timeout=timeout_ms)
            el = page.query_selector(selector)
            text = el.inner_text() if el else ""
            text = text.strip()[:500]
            return f"元素已出现：{selector}\n内容：{text}"
        except Exception:
            return f"等待超时（{timeout_ms}ms）：未找到 {selector}"
    return _run_in_browser(_fn)


@tool
def browser_get_content() -> str:
    """
    获取当前页面的可见文本内容（截取前 3000 字符避免超 token）。
    用于分析页面状态、验证文字是否存在、读取错误提示等。
    """
    def _fn(page):
        text = page.inner_text("body")
        text = " ".join(text.split())
        if len(text) > 3000:
            text = text[:3000] + "\n...(内容已截断)"
        return f"[页面：{page.url}]\n\n{text}"
    return _run_in_browser(_fn)


@tool
def browser_screenshot(filename: str = "screenshot.png") -> str:
    """
    对当前页面截图，保存到 user/screenshots/ 目录。
    filename：文件名（如 homepage.png、login_result.png）
    返回截图保存路径。
    """
    def _fn(page):
        os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
        name = filename if filename.endswith(".png") else filename + ".png"
        path = os.path.join(_SCREENSHOT_DIR, name)
        page.screenshot(path=path, full_page=True)
        return f"截图已保存：{path}（页面：{page.url}）"
    return _run_in_browser(_fn)


@tool
def browser_press_key(key: str) -> str:
    """
    在当前焦点元素上按键。常用值：
    - "Enter" — 提交表单/搜索
    - "Tab"   — 切换焦点
    - "Escape"— 关闭弹窗
    """
    def _fn(page):
        page.keyboard.press(key)
        page.wait_for_load_state("domcontentloaded")
        return f"已按键：{key}\n当前页面：{page.url}"
    return _run_in_browser(_fn)


@tool
def browser_get_url() -> str:
    """获取当前页面 URL，用于验证页面跳转是否符合预期。"""
    def _fn(page):
        return f"当前 URL：{page.url}\n页面标题：{page.title()}"
    return _run_in_browser(_fn)


@tool
def browser_scroll(direction: str = "down", distance: int = 500) -> str:
    """
    滚动页面，用于加载懒加载内容、触达页面底部元素。
    direction: "down"（向下）或 "up"（向上），默认 "down"
    distance: 滚动像素数，默认 500px（约一屏）
    """
    def _fn(page):
        delta = distance if direction == "down" else -distance
        page.evaluate(f"window.scrollBy(0, {delta})")
        page.wait_for_timeout(300)
        return f"已向{'下' if direction == 'down' else '上'}滚动 {distance}px"
    return _run_in_browser(_fn)


@tool
def browser_hover(selector: str) -> str:
    """
    鼠标悬停到指定元素上，触发 hover 菜单、tooltip 或下拉导航。
    selector: CSS 选择器或 text=文本
    """
    def _fn(page):
        page.hover(selector, timeout=10000)
        page.wait_for_timeout(400)
        return f"已悬停：{selector}"
    return _run_in_browser(_fn)


@tool
def browser_select_option(selector: str, value: str) -> str:
    """
    操作 <select> 下拉框，按选项的 value 或可见文本选择。
    selector: <select> 元素选择器
    value: 选项的 value 属性值 或 可见文字（两者都会尝试）
    """
    def _fn(page):
        try:
            page.select_option(selector, value=value, timeout=8000)
            return f"已选择 {selector} 的选项：{value}（按 value）"
        except Exception:
            page.select_option(selector, label=value, timeout=8000)
            return f"已选择 {selector} 的选项：{value}（按文字）"
    return _run_in_browser(_fn)


@tool
def browser_save_state(filename: str = "session") -> str:
    """
    保存当前浏览器的登录态（cookies + localStorage）到本地文件。
    后续子任务可通过 browser_load_state 直接恢复，无需重新登录。
    filename: 文件名（不含后缀），保存到 user/session/<filename>.json
    """
    def _fn(page):
        os.makedirs(_SESSION_DIR, exist_ok=True)
        path = os.path.join(_SESSION_DIR, f"{filename}.json")
        _browser_state["context"].storage_state(path=path)
        return f"登录态已保存：{path}"
    return _run_in_browser(_fn)


@tool
def browser_load_state(filename: str = "session") -> str:
    """
    从本地文件恢复登录态（cookies + localStorage），创建新的浏览器上下文。
    用于多子任务流程中，跳过重复登录直接进入已登录状态。
    filename: 文件名（不含后缀），读取 user/session/<filename>.json
    """
    def _fn(page):
        path = os.path.join(_SESSION_DIR, f"{filename}.json")
        if not os.path.exists(path):
            return f"错误：登录态文件不存在 {path}，请先调用 browser_save_state"
        # 关闭旧 context，用保存的 state 创建新 context
        old_ctx = _browser_state.get("context")
        browser = _browser_state["browser"]
        new_ctx = browser.new_context(storage_state=path)
        new_page = new_ctx.new_page()
        new_page.set_viewport_size({"width": 1280, "height": 800})
        new_page.on("response", _on_response)
        _browser_state["context"] = new_ctx
        _browser_state["page"] = new_page
        try:
            old_ctx.close()
        except Exception:
            pass
        return f"登录态已恢复：{path}，新 context 已就绪"
    return _run_in_browser(_fn)


@tool
def browser_get_form_fields() -> str:
    """
    扫描当前页面所有可见的输入控件（input、textarea、select），
    返回每个字段的：关联 label 文本、name/id/placeholder、输入类型。
    用于在填写表单前理解每个字段的语义，从而生成合适的测试数据。

    例如：
    - label="商品ID" type="text" → 应填数值，如 10001
    - label="商品名称" type="text" → 应填中文文案，如 "测试商品A"
    - label="价格" type="number" → 应填小数，如 99.9
    - label="创建时间" type="date" → 应填日期，如 2024-01-01
    """
    def _fn(page):
        fields = page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]),' +
                'textarea, select'
            );
            inputs.forEach(el => {
                if (!el.offsetParent && el.type !== 'hidden') return; // 跳过不可见元素
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return;

                // 查找关联 label
                let label = '';
                if (el.id) {
                    const lb = document.querySelector('label[for="' + el.id + '"]');
                    if (lb) label = lb.innerText.trim();
                }
                if (!label) {
                    const parent = el.closest('label, .el-form-item, .ant-form-item, .form-group, td, th');
                    if (parent) {
                        const lb = parent.querySelector('label, .el-form-item__label, .ant-form-item-label');
                        if (lb && lb !== el) label = lb.innerText.trim();
                        else if (!lb) {
                            const clone = parent.cloneNode(true);
                            clone.querySelectorAll('input,textarea,select,button').forEach(n => n.remove());
                            label = clone.innerText.trim().replace(/\\s+/g, ' ').substring(0, 30);
                        }
                    }
                }

                results.push({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || el.tagName.toLowerCase(),
                    label: label || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : ''),
                    options: el.tagName === 'SELECT'
                        ? Array.from(el.options).slice(0, 10).map(o => o.text.trim()).join(' / ')
                        : ''
                });
            });
            return results;
        }""")
        if not fields:
            return "未发现可见的表单字段"
        lines = ["当前页面表单字段："]
        for i, f in enumerate(fields, 1):
            label_str = f['label'] or f['placeholder'] or f['name'] or f['id'] or '(未知字段)'
            type_str = f['type']
            selector = f['selector'] or f['tag']
            opts = f"  可选值: {f['options']}" if f['options'] else ""
            placeholder = f"  placeholder: {f['placeholder']}" if f['placeholder'] and f['placeholder'] != label_str else ""
            lines.append(f"[{i}] 字段: {label_str}  类型: {type_str}  选择器: {selector}{placeholder}{opts}")
        return "\n".join(lines)
    return _run_in_browser(_fn)


@tool
def browser_start_network_capture(url_filter: str = "") -> str:
    """
    开始捕获页面发出的 XHR/Fetch 网络请求。
    调用后，后续页面操作（点击、搜索、提交等）触发的 API 请求都会被记录。

    url_filter：可选过滤条件，只捕获 URL 中包含该字符串的请求。
                例如 "/api/" 只捕获后端接口，"search" 只捕获搜索相关请求。
                留空则捕获所有 XHR/Fetch 请求。
    """
    def _fn(page):
        with _capture_lock:
            _capture_state["active"] = True
            _capture_state["filter"] = url_filter
            _capture_state["requests"] = []
        return f"已开始捕获网络请求（过滤器：{url_filter or '无，捕获全部 XHR/Fetch'}）"
    return _run_in_browser(_fn)


@tool
def browser_get_network_requests() -> str:
    """
    获取自上次 browser_start_network_capture 以来捕获的所有 XHR/Fetch 请求。
    返回每条请求的 URL、HTTP 方法、查询参数（GET）、请求体（POST）、响应状态码和响应数据。
    适合验证功能操作是否正确发起了 API 请求、参数是否正确、响应是否符合预期。
    """
    def _fn(page):
        with _capture_lock:
            reqs = list(_capture_state["requests"])
        if not reqs:
            return "未捕获到任何 XHR/Fetch 请求（确认已调用 browser_start_network_capture）"
        lines = []
        for i, r in enumerate(reqs, 1):
            lines.append(f"【请求 {i}】")
            lines.append(f"  方法: {r['method']}")
            parsed = urlparse(r["url"])
            base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            lines.append(f"  URL: {base_url}")
            if parsed.query:
                params = parse_qs(parsed.query)
                params_str = ", ".join(f"{k}={v[0]}" for k, v in params.items())
                lines.append(f"  查询参数: {params_str}")
            if r.get("post_data"):
                data = r["post_data"]
                if len(data) > 500:
                    data = data[:500] + "...(截断)"
                lines.append(f"  请求体: {data}")
            lines.append(f"  响应状态: {r.get('status', 'N/A')}")
            body = r.get("response_body", "")
            if body:
                lines.append(f"  响应数据: {body}")
            lines.append("")
        return "\n".join(lines)
    return _run_in_browser(_fn)


@tool
def browser_upload_file(selector: str, file_path: str) -> str:
    """
    处理文件上传按钮（如"上传海报"、"添加图片"等）。
    selector 可以是：
      - 上传触发按钮的选择器（如 text=上传海报、.upload-btn、button:has-text("添加")）
      - 直接是 input[type=file] 的选择器

    自动识别关联的隐藏 file input（向上最多遍历 6 层父元素查找）。
    若关联查找失败，自动触发文件选择器（expect_file_chooser）。
    file_path：本地文件的绝对或相对路径。
    """
    def _fn(page):
        if not os.path.exists(file_path):
            return f"❌ 文件不存在：{file_path}"

        # 策略1：selector 本身是 file input
        el = page.query_selector(selector)
        if el:
            try:
                tag = el.evaluate("el => el.tagName.toLowerCase()")
                input_type = el.evaluate("el => (el.getAttribute('type') || '').toLowerCase()")
                if tag == "input" and input_type == "file":
                    el.set_input_files(file_path)
                    return f"✅ 已上传文件（直接 file input）：{file_path}"
            except Exception:
                pass

        # 策略2：在 selector 的父容器内（向上最多 6 层）查找隐藏 file input
        file_input_sel = page.evaluate("""(sel) => {
            const btn = document.querySelector(sel);
            if (!btn) return null;
            let el = btn;
            for (let i = 0; i <= 6; i++) {
                const fi = el.querySelector('input[type=file]');
                if (fi) {
                    if (!fi.id) fi.id = '__fu_' + Date.now();
                    return '#' + fi.id;
                }
                el = el.parentElement;
                if (!el || el === document.body) break;
            }
            return null;
        }""", selector)

        if file_input_sel:
            page.set_input_files(file_input_sel, file_path)
            return f"✅ 已上传文件（关联 file input {file_input_sel}）：{file_path}"

        # 策略3：触发文件选择器弹窗
        try:
            with page.expect_file_chooser(timeout=4000) as fc_info:
                if el:
                    el.click()
                else:
                    page.click(selector, timeout=5000)
            fc_info.value.set_files(file_path)
            return f"✅ 已通过文件选择器上传：{file_path}"
        except Exception as e:
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/upload_fail.png", full_page=False)
            except Exception:
                pass
            return (
                f"❌ 文件上传失败（{e}）\n"
                f"  选择器：{selector}\n"
                f"  文件：{file_path}\n"
                f"  建议：调用 browser_human_intervention 让人工手动上传文件"
            )

    return _run_in_browser(_fn)


@tool
def browser_human_intervention(message: str) -> str:
    """
    暂停自动化，请求人工介入，完成后按 Enter 继续。适用于：
      - 不知道登录账号/密码，让人工输入
      - 需要手动完成滑动验证码 / 短信验证码 / 图形验证码
      - 需要手动上传真实图片/文件
      - 表单有需要真实账号才能获取的信息

    调用后会：
      1. 对当前页面截图（方便人工了解当前状态）
      2. 在终端打印醒目提示 + message
      3. 阻塞等待人工按 Enter（最多等待 10 分钟）
      4. 再次截图记录人工操作后的状态
      5. 自动读取人工操作后的页面 URL、标题和内容摘要，返回给 agent
         让 agent 知道人工做了什么、页面当前状态，从而继续测试
    """
    def _fn(page):
        try:
            os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
            page.screenshot(path=f"{_SCREENSHOT_DIR}/human_before.png", full_page=False)
        except Exception:
            pass

        border = "=" * 60
        print(f"\n{border}")
        print(f"🙋 需要人工介入")
        print(f"   {message}")
        print(f"   当前页面截图：{_SCREENSHOT_DIR}/human_before.png")
        print(f"   ⏎  完成操作后请按 Enter 键继续...")
        print(f"{border}\n", flush=True)

        input()  # 阻塞等待人工操作（worker 线程安全，timeout=600）

        # 人工完成后，自动采集当前页面状态供 agent 继续判断
        after_url = page.url
        after_title = ""
        after_content = ""
        after_screenshot = ""
        try:
            after_title = page.title()
            raw = page.inner_text("body")
            after_content = " ".join(raw.split())[:600]
            page.screenshot(path=f"{_SCREENSHOT_DIR}/human_after.png", full_page=False)
            after_screenshot = f"{_SCREENSHOT_DIR}/human_after.png"
        except Exception:
            pass

        return (
            f"✅ 人工介入完成，继续测试\n"
            f"━━ 操作前截图：{_SCREENSHOT_DIR}/human_before.png\n"
            f"━━ 操作后截图：{after_screenshot}\n"
            f"━━ 当前页面 URL：{after_url}\n"
            f"━━ 当前页面标题：{after_title}\n"
            f"━━ 页面内容摘要（前600字）：\n{after_content}"
        )

    return _run_in_browser(_fn, timeout=600)  # 最多等 10 分钟


def _scan_form_errors(page) -> list[dict]:
    """
    扫描页面上所有可见的表单验证错误（内联红字）。
    支持 Element UI / Ant Design / Vant / Bootstrap 等主流框架。
    返回 [{"field": "字段名", "error": "错误文案"}, ...]
    """
    return page.evaluate("""() => {
        const ERROR_SELECTORS = [
            '.el-form-item__error',
            '.ant-form-item-explain-error',
            '.ant-form-item-explain',
            '.van-field__error-message',
            '.ivu-form-item-error-tip',
            '.n-form-item-feedback--error',
            '.arco-form-item-message-help',
            '.invalid-feedback',
            '[class*="form-item-error"]',
            '[class*="field-error"]',
            '[class*="input-error"]',
            '[class*="error-message"]',
            '[class*="error-tip"]',
            '[role="alert"]',
        ];
        const FORM_ITEM_SELECTORS = [
            '.el-form-item', '.ant-form-item', '.van-field',
            '.ivu-form-item', '.n-form-item', '.arco-form-item',
            '.form-group', '[class*="form-item"]',
        ];

        const results = [];
        const seen = new Set();

        for (const sel of ERROR_SELECTORS) {
            document.querySelectorAll(sel).forEach(el => {
                const text = el.innerText?.trim();
                if (!text || seen.has(el)) return;
                seen.add(el);

                let fieldName = '';
                for (const itemSel of FORM_ITEM_SELECTORS) {
                    const formItem = el.closest(itemSel);
                    if (formItem) {
                        const label = formItem.querySelector(
                            'label, .el-form-item__label, .ant-form-item-label label, ' +
                            '.van-field__label, [class*="form-label"], [class*="item-label"]'
                        );
                        if (label) {
                            fieldName = label.innerText.trim().replace(/[\\s\\*：:]+$/, '');
                            break;
                        }
                    }
                }
                results.push({ field: fieldName || '(未知字段)', error: text });
            });
        }
        return results;
    }""")


@tool
def browser_get_form_errors() -> str:
    """
    扫描当前页面所有表单验证错误（字段旁边的红色提示文字）。
    支持 Element UI、Ant Design、Vant、Bootstrap 等主流 UI 框架。

    在 browser_submit_and_check 返回 ❌ 后调用，获取具体哪些字段报错、
    报什么错，然后修正对应字段的值后重新提交。

    返回：每个出错字段名 + 错误文案，以及修正建议。
    """
    def _fn(page):
        errors = _scan_form_errors(page)
        if not errors:
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/form_errors.png", full_page=False)
            except Exception:
                pass
            return "✅ 未检测到表单验证错误（截图已保存 form_errors.png 供人工确认）"

        lines = [f"⚠️ 检测到 {len(errors)} 个表单验证错误："]
        for i, err in enumerate(errors, 1):
            lines.append(f"  [{i}] 字段「{err['field']}」→ {err['error']}")
        lines.append("\n请修正以上字段的值，然后再次调用 browser_submit_and_check 重新提交。")
        return "\n".join(lines)

    return _run_in_browser(_fn)


@tool
def browser_submit_and_check(
    submit_selector: str,
    success_selector: str,
    fail_selector: str = "",
    timeout_ms: int = 8000,
) -> str:
    """
    点击提交按钮并验证提交结果，返回 ✅/❌ 结构化结论。
    提交失败时自动扫描表单内联错误，返回具体哪些字段报错。

    submit_selector：提交按钮的选择器（如 text=保存基本信息、button[type=submit]）
    success_selector：提交成功后预期出现的元素（如 text=保存成功、.success-toast）
    fail_selector：（可选）提交失败时预期出现的元素（如 .error-msg、text=请填写必填项）
    timeout_ms：等待成功标志的最长时间（默认 8000ms）

    返回：
      ✅ 提交成功  — 找到了 success_selector
      ❌ 表单校验失败 + 字段错误列表 — 检测到内联错误红字
      ❌ 提交失败  — 找到了 fail_selector
      ❌ 提交结果未知 — 两者都未出现（含截图路径）
    """
    def _fn(page):
        # 点击提交按钮
        submit_el = page.query_selector(submit_selector)
        if submit_el is None:
            return f"❌ 未找到提交按钮：{submit_selector}"
        submit_el.click()

        # 等待成功标志
        try:
            page.wait_for_selector(success_selector, state="visible", timeout=timeout_ms)
            el = page.query_selector(success_selector)
            text = (el.inner_text() if el else "").strip()[:200]
            return f"✅ 提交成功：{success_selector} 已出现\n内容：{text}"
        except Exception:
            pass

        # 短暂等待，让表单校验错误渲染完成（一般 < 300ms）
        page.wait_for_timeout(400)

        # 自动扫描表单内联错误（无需 LLM 额外调用工具）
        inline_errors = _scan_form_errors(page)
        if inline_errors:
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/submit_form_errors.png", full_page=False)
            except Exception:
                pass
            lines = [f"❌ 表单校验失败，检测到 {len(inline_errors)} 个字段错误："]
            for i, err in enumerate(inline_errors, 1):
                lines.append(f"  [{i}] 字段「{err['field']}」→ {err['error']}")
            lines.append(f"\n截图：{_SCREENSHOT_DIR}/submit_form_errors.png")
            lines.append("请修正以上字段的值后重新调用 browser_submit_and_check。")
            return "\n".join(lines)

        # 检查调用方指定的失败标志
        if fail_selector:
            try:
                page.wait_for_selector(fail_selector, state="visible", timeout=2000)
                el = page.query_selector(fail_selector)
                err_text = (el.inner_text() if el else "").strip()[:300]
                return f"❌ 提交失败：{fail_selector} 出现\n错误信息：{err_text}"
            except Exception:
                pass

        # 两者都没出现，截图留证
        try:
            page.screenshot(path=f"{_SCREENSHOT_DIR}/submit_unknown.png", full_page=False)
        except Exception:
            pass
        return (
            f"❌ 提交结果未知：{timeout_ms}ms 内未检测到成功或失败标志\n"
            f"  成功选择器：{success_selector}\n"
            f"  截图：{_SCREENSHOT_DIR}/submit_unknown.png\n"
            f"  当前页面：{page.url}"
        )

    return _run_in_browser(_fn)


@tool
def browser_iframe_click(frame_selector: str, element_selector: str) -> str:
    """
    在 iframe 内点击元素。用于第三方支付、地图、富文本编辑器等嵌套 iframe 场景。

    frame_selector：定位 iframe 本身的选择器，支持：
      - CSS 选择器（如 iframe#pay-frame、iframe[name=map]、.editor-iframe）
      - src 属性（如 iframe[src*="alipay"]）
    element_selector：iframe 内目标元素的选择器（CSS / text= / role=）

    工作原理：使用 Playwright frameLocator，自动处理跨域 iframe 权限。
    """
    def _fn(page):
        try:
            frame = page.frame_locator(frame_selector)
            frame.locator(element_selector).click(timeout=10000)
            return f"✅ 已在 iframe({frame_selector}) 内点击：{element_selector}"
        except Exception as e:
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/iframe_click_fail.png", full_page=False)
            except Exception:
                pass
            return f"❌ iframe 点击失败：{e}\n  frame={frame_selector}\n  element={element_selector}"
    return _run_in_browser(_fn)


@tool
def browser_iframe_fill(frame_selector: str, element_selector: str, value: str) -> str:
    """
    在 iframe 内填写输入框的值。适用于 iframe 内的表单字段（如富文本编辑器、
    第三方登录框、嵌入式表单）。

    frame_selector：iframe 的选择器（如 iframe.editor-frame、iframe[src*="embed"]）
    element_selector：iframe 内输入框的选择器
    value：要填入的值
    """
    def _fn(page):
        try:
            frame = page.frame_locator(frame_selector)
            frame.locator(element_selector).fill(value, timeout=10000)
            return f"✅ 已在 iframe({frame_selector}) 内填写 {element_selector}：{value}"
        except Exception as e:
            return f"❌ iframe 填写失败：{e}\n  frame={frame_selector}\n  element={element_selector}"
    return _run_in_browser(_fn)


@tool
def browser_iframe_assert_visible(
    frame_selector: str, element_selector: str, timeout_ms: int = 5000
) -> str:
    """
    断言 iframe 内某个元素可见。用于验证 iframe 内的内容是否正确加载。

    frame_selector：iframe 的选择器
    element_selector：iframe 内目标元素的选择器
    timeout_ms：等待元素出现的最长时间（默认 5000ms）

    返回 ✅/❌，不抛异常。
    """
    def _fn(page):
        try:
            frame = page.frame_locator(frame_selector)
            loc = frame.locator(element_selector)
            loc.wait_for(state="visible", timeout=timeout_ms)
            text = loc.inner_text()[:200]
            return f"✅ iframe 断言通过：{element_selector} 可见\n内容：{text}"
        except Exception:
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/iframe_assert_fail.png", full_page=False)
            except Exception:
                pass
            return (
                f"❌ iframe 断言失败：{element_selector} 在 {timeout_ms}ms 内未出现\n"
                f"  frame={frame_selector}\n  截图：{_SCREENSHOT_DIR}/iframe_assert_fail.png"
            )
    return _run_in_browser(_fn)


@tool
def browser_iframe_get_content(frame_selector: str) -> str:
    """
    获取 iframe 内的文本内容（前 2000 字符）。用于验证 iframe 内渲染的数据、
    错误提示、或任意文字内容。

    frame_selector：iframe 的选择器（如 iframe#result-frame、iframe[src*="preview"]）
    """
    def _fn(page):
        try:
            # 方式1：frameLocator（适合大多数场景）
            frame = page.frame_locator(frame_selector)
            text = frame.locator("body").inner_text(timeout=8000)
            text = " ".join(text.split())[:2000]
            return f"[iframe: {frame_selector}]\n{text}"
        except Exception:
            # 方式2：frames() 列表（适合 name/src 已知的 iframe）
            try:
                for f in page.frames:
                    el = page.query_selector(frame_selector)
                    if el and f.name and f.name in (el.get_attribute("name") or ""):
                        text = " ".join(f.inner_text("body").split())[:2000]
                        return f"[iframe name={f.name}]\n{text}"
            except Exception:
                pass
            try:
                page.screenshot(path=f"{_SCREENSHOT_DIR}/iframe_content_fail.png", full_page=False)
            except Exception:
                pass
            return f"❌ 无法读取 iframe 内容：{frame_selector}（截图已保存）"
    return _run_in_browser(_fn)


@tool
def browser_list_iframes() -> str:
    """
    列出当前页面中所有 iframe 的信息（src、name、id、尺寸）。
    在不知道 iframe 选择器时先调用此工具探查，再用其他 iframe_* 工具操作。
    """
    def _fn(page):
        result = page.evaluate("""() => {
            const frames = document.querySelectorAll('iframe');
            return Array.from(frames).map((f, i) => ({
                index: i,
                id: f.id || '',
                name: f.name || '',
                src: (f.src || '').substring(0, 100),
                width: f.offsetWidth,
                height: f.offsetHeight,
                visible: f.offsetParent !== null,
            }));
        }""")
        if not result:
            return "当前页面未检测到 iframe"
        lines = [f"检测到 {len(result)} 个 iframe："]
        for f in result:
            selector_hint = (
                f"iframe#{f['id']}" if f['id'] else
                f"iframe[name={f['name']}]" if f['name'] else
                f"iframe[src*=\"{f['src'].split('/')[-1][:30]}\"]" if f['src'] else
                f"iframe:nth-of-type({f['index']+1})"
            )
            lines.append(
                f"  [{f['index']}] {selector_hint}  {f['width']}x{f['height']}"
                f"  visible={f['visible']}"
                + (f"  src={f['src']}" if f['src'] else "")
            )
        lines.append("\n用以上选择器调用 browser_iframe_click / browser_iframe_assert_visible 等工具。")
        return "\n".join(lines)
    return _run_in_browser(_fn)


@tool
def browser_close() -> str:
    """
    关闭浏览器，释放资源。测试全部完成后调用。
    """
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        _task_queue.put(None)  # 发送退出信号
        _worker_thread.join(timeout=10)
        _worker_thread = None
    return "浏览器已关闭"


# 所有工具列表，供 ToolNode 使用
ALL_BROWSER_TOOLS = [
    browser_navigate,
    browser_assert_visible,
    browser_click,
    browser_fill,
    browser_type,
    browser_press_key,
    browser_scroll,
    browser_hover,
    browser_select_option,
    browser_wait_for,
    browser_get_content,
    browser_get_url,
    browser_screenshot,
    browser_get_form_fields,
    browser_get_form_errors,
    browser_upload_file,
    browser_human_intervention,
    browser_submit_and_check,
    browser_save_state,
    browser_load_state,
    browser_start_network_capture,
    browser_get_network_requests,
    # iframe 系列
    browser_list_iframes,
    browser_iframe_click,
    browser_iframe_fill,
    browser_iframe_assert_visible,
    browser_iframe_get_content,
    browser_close,
]
