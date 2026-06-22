"""Embedding Provider — Loop 4b

把文本转成向量,给 QdrantStore 存 / 搜。

设计:
- EmbeddingProvider Protocol(类似 LLMProvider)
- SentenceTransformerProvider 默认实现(本地,无外部调用)
- 同步 API 包装成 async(asyncio.to_thread)

为什么本地默认:
- 不需要 API key / 网络
- 中文 bge 模型质量已够用
- 部署简单(就一个 python 包 + 模型文件)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


# ===== Protocol =====


class EmbeddingProvider(Protocol):
    """Embedding Provider 接口契约

    所有实现必须:
    - 暴露 dim 属性(向量维度)
    - async embed(text) → list[float]
    - async embed_batch(texts) → list[list[float]]
    """

    dim: int

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


# ===== SentenceTransformer 实现 =====


class SentenceTransformerProvider:
    """本地 sentence-transformers Provider

    默认模型: BAAI/bge-small-zh-v1.5 (512 维,中文 SOTA)
    备选: shibing624/text2vec-base-chinese, BAAI/bge-base-zh-v1.5

    用法:
        provider = SentenceTransformerProvider()  # 默认模型
        provider = SentenceTransformerProvider(model_name="...")
        vec = await provider.embed("用户叫小明")
    """

    DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"

    def __init__(
        self,
        model_name: str | None = None,
        device: str = "cpu",
    ) -> None:
        from sentence_transformers import SentenceTransformer

        model_name = model_name or self.DEFAULT_MODEL
        # 延迟加载 — 第一次 embed() 时才真正加载,这样 import 不会卡
        self._model_name = model_name
        self._device = device
        self._model: SentenceTransformer | None = None
        # 临时用 None,初始化 dim 时会加载
        self.dim = 0
        logger.info("embedding provider init: model=%s device=%s", model_name, device)

    def _ensure_loaded(self) -> None:
        """懒加载 — 第一次使用时才下载/加载模型"""
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        logger.info("loading embedding model: %s", self._model_name)
        self._model = SentenceTransformer(self._model_name, device=self._device)
        # bge-small-zh-v1.5 是 512 维
        self.dim = self._model.get_embedding_dimension()
        logger.info("embedding model loaded: dim=%d", self.dim)

    async def embed(self, text: str) -> list[float]:
        """单条 embed — 异步接口(底层同步,用 to_thread 包装)"""
        self._ensure_loaded()
        assert self._model is not None
        # bge 模型对 instruction-tuned 任务(检索)有 prompt prefix
        # 这里只做语义检索,加 prompt 让检索质量更好
        text_with_prefix = self._maybe_add_prefix(text)
        vec = await asyncio.to_thread(self._model.encode, text_with_prefix)
        return vec.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量 embed — 同步调用编码,异步包装"""
        self._ensure_loaded()
        assert self._model is not None
        texts_with_prefix = [self._maybe_add_prefix(t) for t in texts]
        vecs = await asyncio.to_thread(self._model.encode, texts_with_prefix)
        return [v.tolist() for v in vecs]

    @staticmethod
    def _maybe_add_prefix(text: str) -> str:
        """bge 模型对"检索 query"加 prompt 能提升召回

        参考: https://huggingface.co/BAAI/bge-small-zh-v1.5
        """
        # 简单的启发式:中文短句当 query,长文本当 document
        # 生产环境应该让调用方显式指定 mode,这里简化
        if len(text) <= 20:
            return f"为这个句子生成表示以用于检索相关文章: {text}"
        return text


# ===== Factory =====


_default_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """获取默认 Embedding Provider 单例"""
    global _default_provider
    if _default_provider is None:
        _default_provider = SentenceTransformerProvider()
        logger.info("default embedding provider initialized")
    return _default_provider


def reset_embedding_provider() -> None:
    """测试用:重置单例"""
    global _default_provider
    _default_provider = None