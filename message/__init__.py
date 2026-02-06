"""
消息模块导出。

集中导出 Msg 构造与解析方法，供编排与运行时复用。
"""

from message.msg_protocol import make_msg
from message.parser import extract_first_json_obj, extract_first_topic_tag, is_valid_topic_tag, msg_to_text

__all__ = [
    "make_msg",
    "extract_first_json_obj",
    "extract_first_topic_tag",
    "is_valid_topic_tag",
    "msg_to_text",
]
