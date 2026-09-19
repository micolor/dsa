import hashlib
import random
import secrets
import threading
import time
import requests
import json
import uuid
import logging
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)

original_request = requests.Session.request

ua = UserAgent()


class AuthCache:
    def __init__(self):
        self.data = None
        self.expire_at = 0
        # 失败退避的到期时间。必须独立于 data：拿不到令牌时 data 一直是 None，
        # 用 data 判断会把「失败后先别再来」的意图短路掉。
        self.retry_after = 0
        self.lock = threading.Lock()
        self.ttl = 20
        self.failure_ttl = 5 * 60


_cache = AuthCache()


class PatchSign:
    def __init__(self):
        self.patched = False

    def set_patch(self, patched):
        self.patched = patched

    def is_patched(self):
        return self.patched


_patch_sign = PatchSign()


def _record_failure(reason):
    """记录一次取不到 NID 的失败，并进入退避窗口。"""
    logger.warning(reason)
    _cache.data = None
    _cache.expire_at = 0
    # 该接口失败通常意味着方案已失效、后续大概率继续失败；退避一段时间可以避免
    # 每个数据请求都先去打一次这个接口。
    _cache.retry_after = time.time() + _cache.failure_ttl
    return None


def _extract_nid(data):
    """从授权接口响应里取出 nid；形状不符合预期时返回 None。"""
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    if not isinstance(inner, dict):
        return None
    nid = inner.get("nid")
    return nid if isinstance(nid, str) and nid else None


def _get_nid(user_agent):
    """
    获取东方财富的 NID 授权令牌

    Args:
        user_agent (str): 用户代理字符串，用于模拟不同的浏览器访问

    Returns:
        str: 返回获取到的 NID 授权令牌，如果获取失败则返回 None

    功能说明:
        该函数通过向东方财富的授权接口发送请求来获取 NID 令牌，
        用于后续的数据访问授权。函数实现了缓存机制来避免频繁请求，
        失败时按 _cache.failure_ttl 退避。
    """
    now = time.time()
    # 检查缓存是否有效，避免重复请求
    if _cache.data and now < _cache.expire_at:
        return _cache.data
    # 上次失败后的退避窗口内不再重试：按「没有 NID」降级，让数据请求继续走
    if now < _cache.retry_after:
        return None
    # 使用线程锁确保并发安全
    with _cache.lock:
        # 等锁期间可能有别的线程刚刷新过缓存或刚失败过，取锁后再判一次
        now = time.time()
        if _cache.data and now < _cache.expire_at:
            return _cache.data
        if now < _cache.retry_after:
            return None
        try:
            def generate_uuid_md5():
                """
                生成 UUID 并对其进行 MD5 哈希处理
                :return: MD5 哈希值（32位十六进制字符串）
                """
                # 生成 UUID
                unique_id = str(uuid.uuid4())
                # 对 UUID 进行 MD5 哈希
                md5_hash = hashlib.md5(unique_id.encode('utf-8')).hexdigest()
                return md5_hash

            def generate_st_nvi():
                """
                生成 st_nvi 值的方法
                :return: 返回生成的 st_nvi 值
                """
                HASH_LENGTH = 4  # 截取哈希值的前几位

                def generate_random_string(length=21):
                    """
                    生成指定长度的随机字符串
                    :param length: 字符串长度，默认为 21
                    :return: 随机字符串
                    """
                    charset = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"
                    return ''.join(secrets.choice(charset) for _ in range(length))

                def sha256(input_str):
                    """
                    计算 SHA-256 哈希值
                    :param input_str: 输入字符串
                    :return: 哈希值（十六进制）
                    """
                    return hashlib.sha256(input_str.encode('utf-8')).hexdigest()

                random_str = generate_random_string()
                hash_prefix = sha256(random_str)[:HASH_LENGTH]
                return random_str + hash_prefix

            url = "https://anonflow2.eastmoney.com/backend/api/webreport"
            # 随机选择屏幕分辨率，增加请求的真实性
            screen_resolution = random.choice(['1920X1080', '2560X1440', '3840X2160'])
            payload = json.dumps({
                "osPlatform": "Windows",
                "sourceType": "WEB",
                "osversion": "Windows 10.0",
                "language": "zh-CN",
                "timezone": "Asia/Shanghai",
                "webDeviceInfo": {
                    "screenResolution": screen_resolution,
                    "userAgent": user_agent,
                    "canvasKey": generate_uuid_md5(),
                    "webglKey": generate_uuid_md5(),
                    "fontKey": generate_uuid_md5(),
                    "audioKey": generate_uuid_md5()
                }
            })
            headers = {
                'Cookie': f'st_nvi={generate_st_nvi()}',
                'Content-Type': 'application/json'
            }
            # 增加超时，防止无限等待
            response = requests.request("POST", url, headers=headers, data=payload, timeout=30)
            response.raise_for_status()  # 对 4xx/5xx 响应抛出 HTTPError

            data = response.json()
            nid = _extract_nid(data)
            if nid is None:
                # 形状不对（数组 / 字符串 / data 为 null / 没有 nid）同样是失败：
                # 直接按取不到处理，不能把 TypeError / KeyError 抛进数据抓取路径。
                return _record_failure("解析东方财富授权接口响应失败: 响应中没有可用的 nid")

            _cache.data = nid
            _cache.expire_at = now + _cache.ttl
            _cache.retry_after = 0
            return nid
        except requests.exceptions.RequestException as e:
            return _record_failure(f"请求东方财富授权接口失败: {e}")
        except json.JSONDecodeError as e:
            return _record_failure(f"解析东方财富授权接口响应失败: {e}")


def eastmoney_patch():
    if _patch_sign.is_patched():
        return

    def patched_request(self, method, url, **kwargs):
        # 排除非目标域名
        is_target = any(
            d in (url or "")
            for d in [
                "fund.eastmoney.com",
                "push2.eastmoney.com",
                "push2his.eastmoney.com",
            ]
        )
        if not is_target:
            return original_request(self, method, url, **kwargs)
        # 获取一个随机的 User-Agent
        user_agent = ua.random
        # 处理 Headers：确保不破坏业务代码传入的 headers。必须复制一份再改——
        # efinance 把自己的模块级常量 headers 按引用传进来（28 处调用点），就地写入
        # 会把它的 UA / Cookie 永久改掉，也会影响复用同一个 dict 的其它请求。
        headers = kwargs.get("headers", {})
        headers["User-Agent"] = user_agent
        nid = _get_nid(user_agent)
        if nid:
            headers["Cookie"] = f"nid18={nid}"
        kwargs["headers"] = headers
        # 随机休眠，降低被封风险
        sleep_time = random.uniform(1, 4)
        time.sleep(sleep_time)
        return original_request(self, method, url, **kwargs)

    # 全局替换 Session 的 request 入口
    requests.Session.request = patched_request
    _patch_sign.set_patch(True)
