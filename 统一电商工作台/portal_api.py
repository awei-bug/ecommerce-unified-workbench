"""Session bridge for the workbench product view.

The only login point is the merchant admin (云购 /admin/login), which writes the
`dsh_merchant_session` cookie on 127.0.0.1. Because cookies are shared across
ports on the same host, this workbench reads that cookie directly instead of
asking for credentials a second time.
"""
import http.cookies
import json
import urllib.error
import urllib.parse
import urllib.request

UNIFIED_COOKIE = "dsh_merchant_session"
SESSION_PATH = "/user/session/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PortalAPI:
    def _reply(self, handler, code, payload, cookie=None):
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(raw)))
        if cookie is not None:
            handler.send_header("Set-Cookie", cookie)
        handler.end_headers()
        handler.wfile.write(raw)

    def _unified_token(self, handler):
        """Read the merchant session cookie issued by the shop admin on this host."""
        try:
            cookie = http.cookies.SimpleCookie()
            cookie.load(handler.headers.get("Cookie", ""))
            if UNIFIED_COOKIE in cookie:
                return cookie[UNIFIED_COOKIE].value or ""
        except http.cookies.CookieError:
            pass
        return ""

    def _get_session(self, handler):
        """Resolve the caller from the shared merchant cookie.

        The shop's /user/session/ endpoint is the authority: it re-validates the
        token on every call, so a revoked or expired session stops working here
        without this process keeping any state.
        """
        token = self._unified_token(handler)
        if not token:
            return None
        status, payload = self._upstream(
            urllib.request.Request(
                "http://127.0.0.1:5000" + SESSION_PATH,
                headers={"Authorization": "Bearer " + token},
            )
        )
        if status != 200 or not isinstance(payload, dict) or payload.get("status") != 200:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        user, tenant = data.get("user"), data.get("tenant")
        if not isinstance(user, dict) or not isinstance(tenant, dict):
            return None
        if not isinstance(user.get("name"), str) or not isinstance(tenant.get("slug"), str):
            return None
        return {"token": token, "user": user["name"], "tenant": tenant["slug"]}

    def _upstream(self, request):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=5) as response:
                return response.status, json.loads(response.read(1024 * 1024 + 1))
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except (OSError, ValueError):
            return 503, {}

    def get(self, handler):
        url = urllib.parse.urlsplit(handler.path)
        if url.path not in ("/portal/session", "/portal/products", "/portal/shops"):
            return False
        session = self._get_session(handler)
        if session is None:
            self._reply(handler, 401, {"message": "未检测到商家会话，请先在商家后台登录"})
            return True
        if url.path == "/portal/session":
            self._reply(handler, 200, {"user": session["user"], "tenant": session["tenant"]})
            return True
        if url.path == "/portal/shops":
            params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
            values = params.get("after", [""])
            if len(values) != 1 or len(values[0]) > 64:
                self._reply(handler, 400, {"message": "店铺列表参数无效"})
                return True
            target = "http://127.0.0.1:8000/api/v1/merchant-products/shops/list?" + urllib.parse.urlencode({"after": values[0]})
            status, payload = self._upstream(urllib.request.Request(target, headers={"Authorization": "Bearer " + session["token"]}))
            if status == 200 and isinstance(payload, dict):
                self._reply(handler, 200, payload)
            elif status == 401:
                self._reply(handler, 401, {"message": "商家会话已失效，请重新登录商家后台"})
            elif status == 403:
                self._reply(handler, 403, {"message": "当前账号没有商品查看权限"})
            else:
                self._reply(handler, 503, {"message": "店铺列表暂不可用，请检查服务和功能配置"})
            return True
        params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        shop = params.get("shop_id", [""])[0]
        page = params.get("page", ["1"])[0]
        if len(params.get("shop_id", [])) != 1 or not shop or len(shop) > 64 or "/" in shop or "\\" in shop or shop in (".", "..") or not page.isascii() or not page.isdigit() or not 1 <= int(page) <= 10000:
            self._reply(handler, 400, {"message": "请输入有效的店铺 ID"})
            return True
        target = "http://127.0.0.1:8000/api/v1/merchant-products/" + urllib.parse.quote(shop, safe="") + "?" + urllib.parse.urlencode({"page": page})
        status, payload = self._upstream(urllib.request.Request(target, headers={"Authorization": "Bearer " + session["token"]}))
        if status == 200 and isinstance(payload, dict):
            self._reply(handler, 200, payload)
        elif status == 401:
            self._reply(handler, 401, {"message": "商家会话已失效，请重新登录商家后台"})
        elif status in (403, 404):
            self._reply(handler, status, {"message": "店铺不可用或尚未授予商品查看权限"})
        else:
            self._reply(handler, 503, {"message": "商品服务暂不可用，请检查服务和功能配置"})
        return True

    def post(self, handler):
        """Logout only: login now happens exclusively in the merchant admin."""
        if handler.path != "/portal/logout":
            return False
        origin = handler.headers.get("Origin")
        if origin is not None and origin != "http://127.0.0.1:8090":
            self._reply(handler, 403, {"message": "请求来源无效"})
            return True
        token = self._unified_token(handler)
        if token:
            self._upstream(
                urllib.request.Request(
                    "http://127.0.0.1:5000/user/logout/",
                    data=b"{}",
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
            )
        self._reply(handler, 200, {"message": "已退出"}, f"{UNIFIED_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        return True
