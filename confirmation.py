"""一次性确认码/授权码状态机，与 N.E.K.O 宿主推送通道解耦。"""

from __future__ import annotations

import secrets
import time


class ConfirmationManager:
    """管理绑定操作参数的一次性确认；连续三次同类错误会使确认作废。"""

    def __init__(self, pending: dict[str, dict] | None = None) -> None:
        self.pending = pending if pending is not None else {}

    def issue(self, kind: str, **payload) -> tuple[str, str]:
        """签发内部令牌与 6 位用户确认码；调用方负责带外推送确认码。"""
        now = time.monotonic()
        for token in [
            token for token, item in self.pending.items() if item.get("expires", 0) < now
        ]:
            self.pending.pop(token, None)
        token = secrets.token_urlsafe(12)
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.pending[token] = {
            "kind": kind,
            "expires": now + 300,
            "code": code,
            **payload,
        }
        return token, code

    def consume(
        self, token_or_code: str, kind: str, *, consume: bool = True, **expected
    ) -> dict | None:
        """校验确认码/内部令牌；``consume=False`` 只校验、不消费。

        真正消费时，即使绑定的操作参数不符也作废旧码，防止同一确认码被反复
        用于探测其他对象。调用方可在所有外部检查通过后再把 ``consume`` 设为
        True；若最终消费失败，调用方必须回滚已变更状态。
        """
        value = str(token_or_code or "").strip()
        if not value:
            return None
        found = None
        if value in self.pending:
            if self.pending[value].get("kind") == kind:
                found = value
        else:
            for token, item in self.pending.items():
                if item.get("code") == value and item.get("kind") == kind:
                    found = token
                    break
        if found is None:
            for token, item in list(self.pending.items()):
                if item.get("kind") != kind:
                    continue
                item["attempts"] = int(item.get("attempts") or 0) + 1
                if item["attempts"] >= 3:
                    self.pending.pop(token, None)
            return None
        item = self.pending.get(found)
        if consume:
            self.pending.pop(found, None)
        if not item or item.get("expires", 0) < time.monotonic():
            return None
        for key, expected_value in expected.items():
            if item.get(key) != expected_value:
                return None
        return item
