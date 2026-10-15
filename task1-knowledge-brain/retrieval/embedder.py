"""Free local sentence embeddings from a model already in the Hugging Face cache.

The cached checkpoint is a PyTorch safetensors file and PyTorch is not installed, so
the BERT forward pass runs in NumPy straight from the weight file (memory-mapped).
Nothing is downloaded. Default model: BAAI/bge-small-en-v1.5 (384 dims, CLS pooling).
"""
import json
import math
import os
import struct
from pathlib import Path

import numpy as np

# bge-small-en-v1.5 expects this prefix on queries (not on passages).
QUERY_PREFIX = {"BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: "}


def snapshot(repo: str) -> Path | None:
    base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    for snap in sorted((base / ("models--" + repo.replace("/", "--")) / "snapshots").glob("*")):
        if (snap / "model.safetensors").exists() and (snap / "tokenizer.json").exists():
            return snap
    return None


def _read_safetensors(path: Path) -> dict:
    with open(path, "rb") as fh:
        (size,) = struct.unpack("<Q", fh.read(8))
        header = json.loads(fh.read(size))
    data = np.memmap(path, dtype=np.uint8, mode="r", offset=8 + size)
    out = {}
    for name, meta in header.items():
        if name == "__metadata__" or meta["dtype"] != "F32":
            continue
        a, b = meta["data_offsets"]
        out[name.removeprefix("bert.")] = np.frombuffer(data[a:b], dtype=np.float32).reshape(meta["shape"])
    return out


def _gelu(x):
    # tanh approximation of GELU: vectorised and within ~1e-3 of the exact form
    return 0.5 * x * (1.0 + np.tanh(0.7978845608 * (x + 0.044715 * x ** 3)))


class Embedder:
    def __init__(self, repo: str, max_length: int = 512):
        from tokenizers import Tokenizer
        snap = snapshot(repo)
        if snap is None:
            raise FileNotFoundError(f"{repo} is not in the Hugging Face cache")
        self.repo = repo
        cfg = json.loads((snap / "config.json").read_text())
        self.heads, self.layers, self.eps = cfg["num_attention_heads"], cfg["num_hidden_layers"], cfg["layer_norm_eps"]
        self.dim = cfg["hidden_size"]
        pool = snap / "1_Pooling" / "config.json"
        self.cls = pool.exists() and json.loads(pool.read_text()).get("pooling_mode_cls_token", False)
        self.w = _read_safetensors(snap / "model.safetensors")
        self.tok = Tokenizer.from_file(str(snap / "tokenizer.json"))
        self.tok.enable_truncation(max_length=max_length)
        self.tok.enable_padding(pad_id=cfg.get("pad_token_id", 0))

    def _norm(self, x, prefix):
        mean = x.mean(-1, keepdims=True)
        var = ((x - mean) ** 2).mean(-1, keepdims=True)
        return (x - mean) / np.sqrt(var + self.eps) * self.w[prefix + ".weight"] + self.w[prefix + ".bias"]

    def _dense(self, x, prefix):
        return x @ self.w[prefix + ".weight"].T + self.w[prefix + ".bias"]

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        enc = self.tok.encode_batch(texts)
        ids = np.array([e.ids for e in enc])
        mask = np.array([e.attention_mask for e in enc], dtype=np.float32)
        n, length = ids.shape
        x = (self.w["embeddings.word_embeddings.weight"][ids]
             + self.w["embeddings.position_embeddings.weight"][:length]
             + self.w["embeddings.token_type_embeddings.weight"][0])
        x = self._norm(x, "embeddings.LayerNorm")
        bias = (1.0 - mask)[:, None, None, :] * -1e9
        hd = x.shape[-1] // self.heads

        def split(t):
            return t.reshape(n, length, self.heads, hd).transpose(0, 2, 1, 3)

        for i in range(self.layers):
            p = f"encoder.layer.{i}."
            q, k, v = (split(self._dense(x, p + "attention.self." + s)) for s in ("query", "key", "value"))
            scores = q @ k.transpose(0, 1, 3, 2) / math.sqrt(hd) + bias
            scores = np.exp(scores - scores.max(-1, keepdims=True))
            ctx = (scores / scores.sum(-1, keepdims=True)) @ v
            ctx = ctx.transpose(0, 2, 1, 3).reshape(n, length, -1)
            x = self._norm(x + self._dense(ctx, p + "attention.output.dense"), p + "attention.output.LayerNorm")
            h = _gelu(self._dense(x, p + "intermediate.dense"))
            x = self._norm(x + self._dense(h, p + "output.dense"), p + "output.LayerNorm")
        out = x[:, 0] if self.cls else (x * mask[:, :, None]).sum(1) / mask.sum(1, keepdims=True)
        return (out / np.linalg.norm(out, axis=1, keepdims=True)).astype(np.float32)

    def encode(self, texts: list[str], batch_size: int = 8) -> np.ndarray:
        """L2-normalised passage embeddings, shape (len(texts), dim).

        Texts are sorted by length before batching so each batch pads to a similar length.
        """
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for s in range(0, len(order), batch_size):
            idx = order[s:s + batch_size]
            out[idx] = self._encode_batch([texts[i] for i in idx])
        return out

    def encode_query(self, query: str) -> np.ndarray:
        return self._encode_batch([QUERY_PREFIX.get(self.repo, "") + query])[0]


_cache: dict[str, Embedder] = {}


def get(repo: str) -> Embedder:
    if repo not in _cache:
        _cache[repo] = Embedder(repo)
    return _cache[repo]
