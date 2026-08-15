# -*- coding: utf-8 -*-
"""SSRF / URL 安全校验工具。

从 deepseek_client.py 中拆出的纯安全函数，供客户端、测试与后续模块复用。
云元数据地址（169.254.0.0/16）永远不可豁免。
"""
import socket

SSRF_TRUSTED = []


def _url_host(url):
    """解析 URL 主机名；非法返回 None。"""
    try:
        from urllib.parse import urlparse

        host = urlparse(str(url)).hostname
        return (host or "").lower()
    except Exception:
        return None


def set_ssrf_trusted(hosts):
    """设置 SSRF 信任主机白名单（内网/保留网段经用户显式信任后放行）。

    支持：主机名精确匹配、IP 精确匹配、CIDR 网段（192.168.1.0/24）、
    域后缀（example.com. 通配 *.example.com）。云元数据地址永远不可豁免。
    """
    # 原地替换保持同一列表对象：deepseek_client/main 中 `from security import SSRF_TRUSTED`
    # 绑定的是同一对象，重新赋值会导致外部引用看不到更新。
    SSRF_TRUSTED[:] = [str(h).strip().lower() for h in (hosts or []) if str(h).strip()]


def _trusted_host(host, trusted):
    """信任白名单匹配：主机名/IP 精确、CIDR 网段、域后缀。"""
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for item in trusted or []:
        if not item:
            continue
        if item == host:
            return True
        if ip is not None and "/" in item:
            try:
                if ip in ipaddress.ip_network(item, strict=False):
                    return True
            except ValueError:
                pass
        elif item.startswith("*.") and host.endswith(item[1:]):
            return True
    return False


def _is_private_host(host, allow_loopback=True):
    """SSRF 防护：主机是否为回环/内网/链路本地地址（模型可控 URL 禁止访问）。

    安全分层（桌面单用户智能体：模型指令均来自用户）：
    - 回环（localhost / 127.0.0.0/8 / ::1）：默认放行——本地开发服务器验证
      （localhost:3000 等）是最高频正当场景；严格场景（如搜索结果过滤）传
      allow_loopback=False。
    - 内网 / 链路本地 / 保留网段：默认阻止，SSRF_TRUSTED 白名单可显式信任
      （内网服务 / NAS 等）。
    - 云元数据地址（169.254.169.254 等 169.254.0.0/16）：永远阻止，白名单
      不可豁免（云环境 SSRF 的最终攻击面）。

    DNS 重绑定防护：域名先解析，只要任一解析结果落在内网即拦截——
    模型可控的域名指向 127.0.0.1 时（恶意/失陷 DNS）不再放行。解析失败
    时放行并保持原行为（避免离线环境误杀可用功能）。
    """
    host = (host or "").strip().lower()
    if not host:
        return True
    if _trusted_host(host, SSRF_TRUSTED):
        return False
    if host in ("localhost", "ipv6-localhost"):
        return not allow_loopback
    # 形如 127.0.0.1 的纯数字点分式
    if host.replace(".", "").isdigit():
        parts = host.split(".")
        if len(parts) == 4:
            try:
                a, b = int(parts[0]), int(parts[1])
                if a == 127:
                    return not allow_loopback
                if a == 10:
                    return True
                if a == 172 and 16 <= b <= 31:
                    return True
                if a == 192 and b == 168:
                    return True
                if a == 169 and b == 254:
                    return True  # 云元数据 / 链路本地：永远阻止
                if a == 0:
                    return True
            except (ValueError, IndexError):
                return True
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            return not allow_loopback
        # 链路本地（含 169.254.169.254 云元数据）与保留地址永远阻止
        if ip.is_link_local or ip.is_reserved:
            return True
        if ip.is_private:
            return True
        return False
    except ValueError:
        pass
    # 非 IP 主机名：解析 DNS，任一解析结果落内网即拦截（防 DNS 重绑定）
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
                return not allow_loopback
            if addr.is_link_local or addr.is_reserved or addr.is_private:
                return True
        except ValueError:
            continue
    return False


def _safe_url(url, allow_loopback=True):
    """URL 安全校验。

    v2.13+ 权限哲学：默认放行，只按用户黑名单拦截。
    - permissions.security_mode() == "blacklist"（默认）：只拦 network.blocklist。
    - permissions.security_mode() == "whitelist"（旧模式）：保持旧 SSRF 严格判断。
    """
    if not url or not str(url).startswith(("http://", "https://")):
        return "URL 必须以 http:// 或 https:// 开头"
    host = _url_host(url)
    if not host:
        return f"URL 主机名解析失败：{url[:80]}"
    try:
        import permissions
        if permissions.security_mode() == "blacklist":
            ok, reason = permissions.check_network_host(host)
            if not ok:
                return f"{reason}：{url[:80]}"
            return ""
    except Exception:
        pass
    # 旧 whitelist 模式 / 权限模块未初始化：保留严格 SSRF 判断
    if _is_private_host(host, allow_loopback=allow_loopback):
        return f"已阻止访问内网/回环地址（SSRF 防护）：{url[:80]}"
    return ""
