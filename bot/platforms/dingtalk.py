# -*- coding: utf-8 -*-
"""
===================================
钉钉平台适配器
===================================

处理钉钉机器人的 Webhook 回调。

钉钉机器人文档：
https://open.dingtalk.com/document/robots/robot-overview
"""

import hashlib
import hmac
import base64
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from urllib.parse import quote_plus

from bot.platforms.base import BotPlatform
from bot.models import BotMessage, BotResponse, WebhookResponse, ChatType

logger = logging.getLogger(__name__)


class DingtalkPlatform(BotPlatform):
    """
    钉钉平台适配器
    
    支持：
    - 企业内部机器人回调
    - 群机器人 Outgoing 回调
    - 消息签名验证
    
    配置要求：
    - DINGTALK_APP_KEY: 应用 AppKey
    - DINGTALK_APP_SECRET: 应用 AppSecret（用于签名验证）
    """
    
    def __init__(self):
        from src.config import get_config
        config = get_config()

        self._app_key = getattr(config, 'dingtalk_app_key', None)
        self._app_secret = getattr(config, 'dingtalk_app_secret', None)
        # 已按原因记录过的「配置类拒绝」，见 _log_config_rejection。
        self._logged_config_rejections: set = set()

    def _log_config_rejection(self, reason: str, message: str) -> None:
        """
        记录一次由**配置缺失**（而非伪造请求）导致的拒绝，同一原因只记一次。

        回调地址是公开的，verify_request 在鉴权之前执行：每个未鉴权请求都会走到
        这两个分支，若每次都记日志，任何知道回调地址的人都能刷满 ERROR 日志。
        配置类失败是「一次配置错、之后每次都错」，记一次足够让运维在 ERROR 级别
        看到可执行的修复说明；请求类失败（签名不符、时间戳过期）仍是逐条 warning。
        """
        if reason in self._logged_config_rejections:
            return
        # set.add 在 GIL 下是原子的；并发下最坏情况是同一原因多记一行，无需加锁。
        self._logged_config_rejections.add(reason)
        logger.error(message)

    @property
    def platform_name(self) -> str:
        return "dingtalk"
    
    def verify_request(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        验证钉钉请求签名

        钉钉签名算法：
        1. 获取 timestamp 和 sign
        2. 计算：base64(hmac_sha256(timestamp + "\n" + app_secret))
        3. 比对签名

        验证失败一律拒绝（fail-closed）：未配置 app_secret、或请求缺少
        timestamp/sign 都返回 False，与 Discord 适配器一致。此前这两种情况
        直接放行，等于「没有密钥就不校验」——任何知道回调地址的人都能驱动
        机器人。钉钉的 `handle_challenge` 恒返回 None，URL 验证不经过这里，
        因此收紧不影响回调地址的首次校验。

        代价是配置错一次、之后每次回调都 403，而响应体不会说明原因。这两个
        分支（配置缺失）因此用 `_log_config_rejection` 在 **ERROR** 级别记一次
        可执行的修复说明；签名不符 / 时间戳过期仍逐条 warning——那是请求问题，
        不是配置问题。
        """
        if not self._app_secret:
            self._log_config_rejection(
                "missing_app_secret",
                "[DingTalk] 未配置 DINGTALK_APP_SECRET：钉钉回调一律拒绝（403）。"
                "请配置 DINGTALK_APP_SECRET，并在钉钉机器人后台开启「加签」，"
                "否则机器人不会收到任何命令。",
            )
            return False

        # HTTP 头名大小写不敏感，先归一化，避免合法的签名请求被误判为缺少签名
        normalized_headers = {str(key).lower(): value for key, value in headers.items()}
        timestamp = normalized_headers.get('timestamp', '')
        sign = normalized_headers.get('sign', '')

        if not timestamp or not sign:
            self._log_config_rejection(
                "missing_signature_headers",
                "[DingTalk] 回调缺少 timestamp/sign 头：一律拒绝（403）。"
                "若钉钉机器人后台未开启「加签」，请开启后再触发回调。",
            )
            return False

        # 验证时间戳（1小时内有效）
        try:
            request_time = int(timestamp)
            current_time = int(time.time() * 1000)
            if abs(current_time - request_time) > 3600 * 1000:
                logger.warning("[DingTalk] 时间戳过期")
                return False
        except ValueError:
            logger.warning("[DingTalk] 无效的时间戳")
            return False
        
        # 计算签名
        string_to_sign = f"{timestamp}\n{self._app_secret}"
        hmac_code = hmac.new(
            self._app_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        expected_sign = base64.b64encode(hmac_code).decode('utf-8')
        
        if sign != expected_sign:
            logger.warning(f"[DingTalk] 签名验证失败")
            return False
        
        return True
    
    def handle_challenge(self, data: Dict[str, Any]) -> Optional[WebhookResponse]:
        """钉钉不需要 URL 验证"""
        return None
    
    def parse_message(self, data: Dict[str, Any]) -> Optional[BotMessage]:
        """
        解析钉钉消息
        
        钉钉 Outgoing 机器人消息格式：
        {
            "msgtype": "text",
            "text": {
                "content": "@机器人 /analyze 600519"
            },
            "msgId": "xxx",
            "createAt": "1234567890",
            "conversationType": "2",  # 1=单聊, 2=群聊
            "conversationId": "xxx",
            "conversationTitle": "群名",
            "senderId": "xxx",
            "senderNick": "用户昵称",
            "senderCorpId": "xxx",
            "senderStaffId": "xxx",
            "chatbotUserId": "xxx",
            "atUsers": [{"dingtalkId": "xxx", "staffId": "xxx"}],
            "isAdmin": false,
            "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=xxx",
            "sessionWebhookExpiredTime": 1234567890
        }
        """
        # 检查消息类型
        msg_type = data.get('msgtype', '')
        if msg_type != 'text':
            logger.debug(f"[DingTalk] 忽略非文本消息: {msg_type}")
            return None
        
        # 获取消息内容
        text_content = data.get('text', {})
        raw_content = text_content.get('content', '')
        
        # 提取命令（去除 @机器人）
        content = self._extract_command(raw_content)
        
        # 检查是否 @了机器人
        at_users = data.get('atUsers', [])
        mentioned = len(at_users) > 0
        
        # 会话类型
        conversation_type = data.get('conversationType', '')
        if conversation_type == '1':
            chat_type = ChatType.PRIVATE
        elif conversation_type == '2':
            chat_type = ChatType.GROUP
        else:
            chat_type = ChatType.UNKNOWN
        
        # 创建时间
        create_at = data.get('createAt', '')
        try:
            timestamp = datetime.fromtimestamp(int(create_at) / 1000)
        except (ValueError, TypeError):
            timestamp = datetime.now()
        
        # 保存 session webhook 用于回复
        session_webhook = data.get('sessionWebhook', '')
        
        return BotMessage(
            platform=self.platform_name,
            message_id=data.get('msgId', ''),
            user_id=data.get('senderId', ''),
            user_name=data.get('senderNick', ''),
            chat_id=data.get('conversationId', ''),
            chat_type=chat_type,
            content=content,
            raw_content=raw_content,
            mentioned=mentioned,
            mentions=[u.get('dingtalkId', '') for u in at_users],
            timestamp=timestamp,
            raw_data={
                **data,
                '_session_webhook': session_webhook,
            },
        )
    
    def _extract_command(self, text: str) -> str:
        """
        提取命令内容（去除 @机器人）
        
        钉钉的 @用户 格式通常是 @昵称 后跟空格
        """
        # 简单处理：移除开头的 @xxx 部分
        import re
        # 匹配开头的 @xxx（中英文都可能）
        text = re.sub(r'^@[\S]+\s*', '', text.strip())
        return text.strip()
    
    def format_response(
        self, 
        response: BotResponse, 
        message: BotMessage
    ) -> WebhookResponse:
        """
        格式化钉钉响应
        
        钉钉 Outgoing 机器人可以直接在响应中返回消息。
        也可以使用 sessionWebhook 异步发送。
        
        响应格式：
        {
            "msgtype": "text" | "markdown",
            "text": {"content": "xxx"},
            "markdown": {"title": "xxx", "text": "xxx"},
            "at": {"atUserIds": ["xxx"], "isAtAll": false}
        }
        """
        if not response.text:
            return WebhookResponse.success()
        
        # 构建响应
        if response.markdown:
            body = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "股票分析助手",
                    "text": response.text,
                }
            }
        else:
            body = {
                "msgtype": "text",
                "text": {
                    "content": response.text,
                }
            }
        
        # @发送者
        if response.at_user and message.user_id:
            body["at"] = {
                "atUserIds": [message.user_id],
                "isAtAll": False,
            }
        
        return WebhookResponse.success(body)
    
    def send_by_session_webhook(
        self, 
        session_webhook: str, 
        response: BotResponse,
        message: BotMessage
    ) -> bool:
        """
        通过 sessionWebhook 发送消息
        
        适用于需要异步发送或多条消息的场景。
        
        Args:
            session_webhook: 钉钉提供的会话 Webhook URL
            response: 响应对象
            message: 原始消息对象
            
        Returns:
            是否发送成功
        """
        if not session_webhook:
            logger.warning("[DingTalk] 没有可用的 sessionWebhook")
            return False
        
        import requests
        
        try:
            # 构建消息
            if response.markdown:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": "股票分析助手",
                        "text": response.text,
                    }
                }
            else:
                payload = {
                    "msgtype": "text",
                    "text": {
                        "content": response.text,
                    }
                }
            
            # @发送者
            if response.at_user and message.user_id:
                payload["at"] = {
                    "atUserIds": [message.user_id],
                    "isAtAll": False,
                }
            
            # 发送请求
            resp = requests.post(
                session_webhook,
                json=payload,
                timeout=10
            )
            
            if resp.status_code == 200:
                result = resp.json()
                if result.get('errcode') == 0:
                    logger.info("[DingTalk] sessionWebhook 发送成功")
                    return True
                else:
                    logger.error(f"[DingTalk] sessionWebhook 发送失败: {result}")
                    return False
            else:
                logger.error(f"[DingTalk] sessionWebhook 请求失败: {resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"[DingTalk] sessionWebhook 发送异常: {e}")
            return False
