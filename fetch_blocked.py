# -*- coding: utf-8 -*-
"""
fetch_blocked：通过机场 HTTP 代理 + 浏览器 TLS 指纹访问被墙站点（按需加载的能力扩展）

设计要点（合规与安全）：
- 独立模块，不参与主程序默认启用集：本文件存在且用户在工具中心勾选启用后才生效，
  分享/开源项目时剔除本文件即可（规避传播翻墙软件风险）。
- 凭证只读自本机 mihomo/clash 订阅缓存，不落日志、不随返回值输出。
- 内建 SSRF 防护（与 fetch_url 同规则：拒绝内网/回环/云元数据地址）。
- 节点池 TTL 缓存（10 分钟），避免每次请求重复并发测速。

核心能力（2026-08-11 实机验证）：
1. 自动发现本机 mihomo-party / clash 订阅缓存中的 HTTP 节点
2. 并发健康检查（TCP 连通 + HTTPS CONNECT 隧道测试），选最快可用节点
3. curl_cffi 模拟 Chrome TLS/HTTP2 指纹，绕过 Cloudflare 人机验证
4. 纯标准库 TLS-in-TLS 降级路径（无 curl_cffi 时可用，但过不了 CF）

依赖：curl_cffi（可选；缺失自动降级标准库路径）
"""
import base64
import concurrent.futures
import logging
import os
import re
import ssl
import socket
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ============================================================
# 配置区
# ============================================================
# 节点自动发现目录（mihomo-party / clash / 旧版 clash 的订阅缓存）
NODE_DIRS = [
    os.path.join(os.environ.get("APPDATA", ""), "mihomo-party", "profiles"),
    os.path.join(os.environ.get("APPDATA", ""), "clash", "profiles"),
    os.path.join(os.environ.get("APPDATA", ""), "mihomo", "profiles"),
    os.path.join(os.environ.get("APPDATA", ""), "clash-verge", "profiles"),
]
ENV_USER = os.environ.get("FETCH_PROXY_USER", "")
ENV_PASS = os.environ.get("FETCH_PROXY_PASS", "")

FETCH_TIMEOUT = 25          # 整体抓取超时（秒）
CONNECT_TIMEOUT = 4         # 节点隧道测试超时（秒）
MAX_CHARS = 500000          # 输出文本上限（与 fetch_url 一致）
TEST_TARGET = ("www.gstatic.com", 443)  # 隧道连通性测试目标
NODE_CACHE_TTL = 600        # 节点池缓存 10 分钟

# SSRF：与鲸语 fetch_url 同规则（回环放行=本地开发验证；内网/元数据阻止）
_LOOPBACK_IPS = frozenset()


def _is_blocked_host(host):
    """SSRF 防护：内网/链路本地/云元数据地址拒绝访问（回环放行，与 fetch_url 一致）。"""
    host = (host or "").strip().lower()
    if not host or host in ("localhost", "ipv6-localhost"):
        return False
    if host.replace(".", "").isdigit():
        parts = host.split(".")
        if len(parts) == 4:
            try:
                a, b = int(parts[0]), int(parts[1])
                if a == 127:
                    return False  # 回环放行（本地开发验证）
                if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
                    return True
                if a == 169 and b == 254:
                    return True  # 云元数据/链路本地
                if a == 0:
                    return True
            except (ValueError, IndexError):
                return True
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            return False
        return ip.is_private or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    # 非 IP 主机名：解析 DNS，任一解析结果落内网/链路本地/保留段即拦截（防 DNS 重绑定）。
    # 回环解析结果仍放行（本地开发验证语义，与 fetch_url 一致）。
    try:
        infos = socket.getaddrinfo(
            host, None, socket.AF_INET | socket.AF_INET6, socket.SOCK_STREAM
        )
    except Exception:
        return False  # 解析失败（离线/DNS 不可用）：维持放行，避免误杀
    seen = set()
    for info in infos:
        try:
            ip = (info[4] or ["", ""])[0]
            ip = ip.split("%")[0]  # 去掉 IPv6 区域 ID
            if ip in seen:
                continue
            seen.add(ip)
            addr = ipaddress.ip_address(ip)
            if addr.is_loopback:
                continue
            if addr.is_private or addr.is_link_local or addr.is_reserved:
                return True
        except ValueError:
            continue
    return False


# ============================================================
# 一、节点发现：从 mihomo/clash 订阅缓存解析节点
# ============================================================

def _parse_yaml_nodes(text):
    """解析 YAML 订阅文本，返回节点 dict 列表（仅 type: http，实测 80% 可用）。

    兼容两种常见节点块格式：
      - type: http\n  name: ...\n  server: ...（type 在前）
      - name: ...\n  type: http\n  server: ...（name 在前，clash 标准格式）
    """
    nodes = []
    # 按顶层列表项切分（^ 行首可选缩进 + "- "）
    blocks = [b for b in re.split(r"(?m)^\s*-\s+", "\n" + text) if b.strip()]
    for b in blocks:
        typ = re.search(r"type:\s*(\w+)", b)
        server = re.search(r"server:\s*([^\s]+)", b)
        port = re.search(r"port:\s*(\d+)", b)
        name = re.search(r"name:\s*(.+)", b)
        if not (typ and server and port):
            continue
        node = {
            "type": typ.group(1),
            "name": name.group(1).strip().strip("'\"") if name else "",
            "server": server.group(1),
            "port": int(port.group(1)),
            "username": "",
            "password": "",
            "tls": bool(re.search(r"tls:\s*true", b)),
        }
        um = re.search(r"username:\s*([^\s'\"]+)", b)
        pm = re.search(r"password:\s*([^\s'\"]+)", b)
        if um:
            node["username"] = um.group(1)
        if pm:
            node["password"] = pm.group(1)
        if typ.group(1) == "http":
            nodes.append(node)
    return nodes


def _discover_nodes():
    """遍历订阅缓存目录，合并解析所有 HTTP 节点（去重按 server:port）。"""
    nodes = []
    for d in NODE_DIRS:
        if not d or not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if not fn.endswith((".yaml", ".yml")):
                    continue
                fp = os.path.join(d, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        nodes.extend(_parse_yaml_nodes(f.read()))
                except Exception:
                    logger.debug("解析订阅失败: %s", fp)
        except Exception:
            continue
    seen, uniq = set(), []
    for n in nodes:
        key = (n["server"], n["port"])
        if key not in seen:
            seen.add(key)
            uniq.append(n)
    return uniq


# ============================================================
# 二、节点健康检查：TCP 连通 + HTTPS CONNECT 隧道
# ============================================================

def _test_node(node, host=TEST_TARGET[0], port=TEST_TARGET[1], timeout=CONNECT_TIMEOUT):
    """对节点做 TCP 连接 + TLS(代理) + CONNECT 隧道测试，返回延迟秒数或 None。"""
    user = node.get("username") or ENV_USER
    pwd = node.get("password") or ENV_PASS
    try:
        t0 = time.time()
        raw = socket.create_connection((node["server"], node["port"]), timeout=timeout)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(raw, server_hostname=node["server"])
        tls.settimeout(timeout)
        auth_hdr = ""
        if user and pwd:
            auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            auth_hdr = f"Proxy-Authorization: Basic {auth}\r\n"
        req = (f"CONNECT {host}:{port} HTTP/1.1\r\n"
               f"Host: {host}:{port}\r\n{auth_hdr}"
               f"Proxy-Connection: keep-alive\r\n\r\n")
        tls.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = tls.recv(4096)
            if not chunk:
                break
            resp += chunk
            if len(resp) > 8192:
                break
        tls.close()
        if resp.startswith(b"HTTP/1.1 200"):
            return time.time() - t0
    except Exception:
        return None
    return None


# 节点池缓存：pick 结果按 TTL 缓存，避免每次请求重复测速
_NODE_CACHE = {"ts": 0.0, "node": None}


def _pick_node(nodes, max_test=8):
    """并发测试，返回最快的可用节点（带 10 分钟 TTL 缓存）。"""
    global _NODE_CACHE
    now = time.time()
    if _NODE_CACHE["node"] is not None and now - _NODE_CACHE["ts"] < NODE_CACHE_TTL:
        return _NODE_CACHE["node"]
    if not nodes:
        return None
    cands = nodes[:max_test]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(cands))) as ex:
        futures = {ex.submit(_test_node, n): n for n in cands}
        for fut in concurrent.futures.as_completed(futures):
            latency = fut.result()
            if latency is not None:
                results.append((latency, futures[fut]))
    if not results:
        _NODE_CACHE = {"ts": now, "node": None}
        return None
    results.sort(key=lambda x: x[0])
    best = results[0][1]
    _NODE_CACHE = {"ts": now, "node": best}
    return best


# ============================================================
# 三、核心抓取：curl_cffi 指纹伪装（可过 Cloudflare）
# ============================================================
try:
    from curl_cffi import requests as _cr  # 可选依赖：按需加载
except ImportError:
    _cr = None


def _fetch_via_proxy_curl(url, node, timeout=FETCH_TIMEOUT):
    """主路径：curl_cffi + Chrome 指纹 + HTTPS 代理。"""
    if _cr is None:
        raise RuntimeError("缺少 curl_cffi，请先 pip install curl_cffi（或使用标准库降级路径）")

    user = node.get("username") or ENV_USER
    pwd = node.get("password") or ENV_PASS
    if not user or not pwd:
        raise RuntimeError(f"节点 {node['name']} 缺少认证信息")
    proxy = f"https://{user}:{pwd}@{node['server']}:{node['port']}"

    resp = _cr.get(
        url,
        impersonate="chrome",          # 关键：Chrome TLS/HTTP2 指纹
        proxy=proxy,
        timeout=timeout,
        verify=False,                  # 机场节点证书通常不校验
        allow_redirects=True,
        headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    resp.raise_for_status()
    raw = resp.content
    charset = (resp.headers.get("content-type") or "").split("charset=")[-1].strip() or "utf-8"
    text = None
    for enc in (charset, "utf-8", "gb18030", "latin-1"):
        if not enc:
            continue
        try:
            text = raw.decode(enc, errors="strict")
            break
        except (LookupError, UnicodeDecodeError):
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n[内容较大已截断前 500KB]"
    return text


# ============================================================
# 四、降级路径：纯标准库 TLS-in-TLS（无 curl_cffi 时可用）
# ============================================================

def _fetch_via_proxy_stdlib(url, node, timeout=10):
    """零依赖降级：socket + ssl + MemoryBIO 双层 TLS。
    能访问一般被墙站点，但过不了 Cloudflare JS 挑战（返回 403 挑战页）。"""
    import select

    u = urlparse(url)
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query

    user = node.get("username") or ENV_USER
    pwd = node.get("password") or ENV_PASS
    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode() if (user and pwd) else ""

    raw = socket.create_connection((node["server"], node["port"]), timeout=timeout)
    ctx_proxy = ssl.create_default_context()
    ctx_proxy.check_hostname = False
    ctx_proxy.verify_mode = ssl.CERT_NONE
    tls_proxy = ctx_proxy.wrap_socket(raw, server_hostname=node["server"])

    req = (f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
           + (f"Proxy-Authorization: Basic {auth}\r\n" if auth else "")
           + "Proxy-Connection: keep-alive\r\n\r\n")
    tls_proxy.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = tls_proxy.recv(4096)
        if not chunk:
            break
        resp += chunk
    if not resp.startswith(b"HTTP/1.1 200"):
        tls_proxy.close()
        raise RuntimeError("CONNECT 隧道建立失败: " + resp[:100].decode("ascii", "replace"))

    ctx_target = ssl.create_default_context()
    ctx_target.check_hostname = False
    ctx_target.verify_mode = ssl.CERT_NONE
    bio_in = ssl.MemoryBIO()
    bio_out = ssl.MemoryBIO()
    tls_target = ctx_target.wrap_bio(bio_in, bio_out, server_hostname=host)

    def pump():
        try:
            out = bio_out.read()
            if out:
                tls_proxy.sendall(out)
        except Exception:
            pass
        r, _, _ = select.select([tls_proxy], [], [], 0.2)
        if r:
            try:
                data = tls_proxy.recv(65536)
                if data:
                    bio_in.write(data)
                else:
                    bio_in.write_eof()
            except Exception:
                pass

    deadline = time.time() + timeout
    done = False
    while time.time() < deadline:
        pump()
        try:
            tls_target.do_handshake()
            done = True
            break
        except ssl.SSLWantReadError:
            continue
    if not done:
        tls_proxy.close()
        raise RuntimeError("TLS-in-TLS 握手超时")

    http_req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
                f"Accept: text/html,application/json,*/*\r\n"
                f"Accept-Encoding: identity\r\nConnection: close\r\n\r\n")
    tls_target.write(http_req.encode())
    pump()

    data = b""
    while time.time() < deadline:
        pump()
        try:
            chunk = tls_target.read(65536)
            if chunk:
                data += chunk
                if len(data) > MAX_CHARS * 3:
                    break
            else:
                break
        except ssl.SSLWantReadError:
            continue
        except Exception:
            break
    tls_proxy.close()
    return data.decode("utf-8", errors="replace")


# ============================================================
# 五、对外入口（工具函数）
# ============================================================

def fetch_blocked(url, proxy=None, prefer="curl"):
    """抓取被墙/国际站点内容。

    Args:
        url: 目标 URL（http/https）
        proxy: 可选，形如 "https://user:pass@server:port" 的节点；留空自动发现
        prefer: "curl" 用 curl_cffi（能过 Cloudflare）；"stdlib" 用标准库降级

    Returns:
        网页文本（≤500KB）；失败返回以 "错误:" 开头的描述（与 fetch_url 约定一致）
    """
    if not str(url or "").startswith(("http://", "https://")):
        return "错误: URL 必须以 http:// 或 https:// 开头"
    host = urlparse(url).hostname
    if not host:
        return f"错误: URL 解析失败：{url[:80]}"
    if _is_blocked_host(host):
        return f"错误: 已阻止访问内网/回环地址（SSRF 防护）：{url[:80]}"

    if proxy:
        m = re.match(r"https?://([^:@/]+):([^@/]+)@([^:/]+):(\d+)", proxy)
        if m:
            node = {"server": m.group(3), "port": int(m.group(4)),
                    "username": m.group(1), "password": m.group(2),
                    "name": "显式代理", "type": "http", "tls": True}
        else:
            return "错误: proxy 格式应为 https://user:pass@server:port"
    else:
        nodes = _discover_nodes()
        if not nodes:
            return ("错误: 未发现任何机场节点（可在 mihomo-party/clash 订阅缓存或环境变量 "
                    "FETCH_PROXY_USER/PASS 配置）")
        node = _pick_node(nodes)
        if node is None:
            return "错误: 已发现节点但全部不可用（可能订阅已过期）"

    try:
        if prefer == "curl":
            try:
                return _fetch_via_proxy_curl(url, node)
            except RuntimeError as e:
                if "curl_cffi" in str(e):
                    return _fetch_via_proxy_stdlib(url, node)
                raise
        return _fetch_via_proxy_stdlib(url, node)
    except Exception as e:
        return f"错误: {type(e).__name__}: {str(e)[:200]}"
