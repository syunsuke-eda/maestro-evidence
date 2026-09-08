"""maestro-evidence 共通の例外。"""

from __future__ import annotations


class MeError(RuntimeError):
    """利用者の入力・環境・外部コマンド結果が要件を満たさない。"""
