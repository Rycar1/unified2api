"""Interactive MonkeyCode login. Never attaches to an existing browser profile."""
import ctypes
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

OFFICIAL = "https://monkeycode-ai.com"
PORTS = {8080, 8787, 7864, 18080}


class LoginError(Exception):
    pass


def parse_launch(uri):
    try:
        parsed = urlsplit(uri)
        if parsed.scheme != "unified2api-login" or parsed.netloc != "monkeycode" or parsed.path not in {"", "/"} or parsed.fragment:
            raise ValueError()
        q = parse_qs(parsed.query, strict_parsing=True)
        if set(q) != {"base", "id", "token"} or any(len(v) != 1 for v in q.values()):
            raise ValueError()
        base, fid, token = (q[k][0] for k in ("base", "id", "token"))
        url = urlsplit(base)
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"} or url.port not in PORTS or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32}", fid) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ValueError()
        return base.rstrip("/") + "/admin/api/unified/monkeycode/login/" + fid, token
    except (ValueError, KeyError):
        raise LoginError("登录链接无效，请从本机 Unified 控制台重新打开。")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def call(endpoint, token, action, method="GET", body=None):
    request = urllib.request.Request(endpoint + "/" + action,
        data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    # Local handoff must not use system proxies or follow redirects carrying credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=90 if action == "complete" else 10) as response:
            return json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404, 409}:
            raise LoginError("登录已取消、过期或窗口已经打开。请在控制台重新开始。") from None
        raise LoginError("账号验证失败，请检查网络并在控制台重新登录。") from None
    except (OSError, ValueError):
        raise LoginError("无法连接本机控制台，请确认服务已启动。") from None


def launch_browser(playwright, headless=False):
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(channel=channel, headless=headless)
        except Exception:
            continue
    raise LoginError("未能打开登录窗口。请安装或更新 Microsoft Edge / Google Chrome 后重试。")


def run_window(browser, endpoint, token, request=call, prepare=None):
    """The injectable request/prepare hooks support offline integration tests."""
    claim = request(endpoint, token, "helper", "POST", {})
    deadline = time.monotonic() + min(claim.get("expires_in", 600), 600)
    context = browser.new_context(no_viewport=True)
    success = False
    try:
        if prepare:
            prepare(context)
        page = context.new_page()
        page.goto(OFFICIAL + "/login", wait_until="domcontentloaded", timeout=60000)
        while time.monotonic() < deadline and browser.is_connected() and context.pages:
            status = request(endpoint, token, "helper")
            if status["state"] != "waiting":
                return
            cookies = context.cookies(OFFICIAL + "/api/v1/users/status")
            if any(c["name"] == "monkeycode_ai_session" for c in cookies):
                try:
                    response = context.request.get(OFFICIAL + "/api/v1/users/status", timeout=10000, max_redirects=0)
                    data = response.json() if response.status == 200 else {}
                    payload = data.get("data")
                    user = payload.get("user") if isinstance(payload, dict) else None
                    authenticated = data.get("code", 0) == 0 and isinstance(user, dict) and bool(user.get("id"))
                except Exception:
                    authenticated = False
                if authenticated:
                    cookies = context.cookies(OFFICIAL + "/api/v1/users/status")
                    cookie = "; ".join(c["name"] + "=" + c["value"] for c in cookies)
                    request(endpoint, token, "complete", "POST", {"cookie": cookie})
                    success = True
                    return
            # Playwright processes window-close events during this wait.
            if context.pages:
                context.pages[0].wait_for_timeout(2000)
        if time.monotonic() >= deadline:
            raise LoginError("登录已超过 10 分钟，请重新打开登录窗口。")
    except LoginError:
        raise
    except Exception:
        if browser.is_connected() and context.pages:
            raise LoginError("登录页面未能正常加载，请检查网络后重试。") from None
    finally:
        try:
            context.close()
        except Exception:
            pass
        if not success:
            try:
                request(endpoint, token, "cancel", "POST", {})
            except Exception:
                pass


def message(text, error=False):
    ctypes.windll.user32.MessageBoxW(None, text, "Unified 登录助手", 0x10 if error else 0x40)


def install():
    import winreg
    if not getattr(sys, "frozen", False):
        raise LoginError("请使用打包后的 UnifiedLoginHelper.exe 安装。")
    destination = Path(os.environ["LOCALAPPDATA"]) / "Unified2API" / "LoginHelper"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "UnifiedLoginHelper.exe"
    if Path(sys.executable).resolve() != target.resolve():
        shutil.copy2(sys.executable, target)
    key_path = r"Software\Classes\unified2api-login"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Unified2API Login")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, '"' + str(target) + '" "%1"')


def main():
    try:
        if len(sys.argv) == 1 or sys.argv[1] in {"--install", "--install-silent"}:
            install()
            if "--install-silent" not in sys.argv:
                message("安装完成。\n回到控制台，点击 MonkeyCode 的「打开登录窗口」。\n浏览器首次询问时请选择打开 Unified 登录助手。")
            return 0
        from playwright.sync_api import sync_playwright
        if sys.argv[1] == "--self-test":
            with sync_playwright() as p:
                browser = launch_browser(p, headless=True)
                try:
                    page = browser.new_page()
                    page.set_content("<title>Unified helper ready</title>")
                    return 0 if page.title() == "Unified helper ready" else 1
                finally:
                    browser.close()
        if len(sys.argv) != 2:
            raise LoginError("请从控制台打开登录窗口。")
        endpoint, token = parse_launch(sys.argv[1])
        # Validate the handoff before showing a browser window.
        call(endpoint, token, "helper")
        with sync_playwright() as p:
            browser = launch_browser(p)
            try:
                run_window(browser, endpoint, token)
            except LoginError:
                raise
            except Exception:
                # Manual window closure is a normal cancellation.
                if browser.is_connected() and browser.contexts and browser.contexts[0].pages:
                    raise LoginError("登录页面未能正常加载，请检查网络后重新打开。") from None
            finally:
                browser.close()
        return 0
    except LoginError as exc:
        if "--self-test" not in sys.argv:
            message(str(exc), error=True)
        return 1
    except Exception:
        if "--self-test" not in sys.argv:
            message("登录助手启动失败，请重新安装助手或更新 Edge 浏览器。", error=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
