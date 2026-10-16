"""Vectores multilingües locales, con pesos y revisión reproducibles."""
from functools import lru_cache
from pathlib import Path
from threading import RLock

import numpy as np

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_FILE = "onnx/model_qint8_avx512_vnni.onnx"
MODEL_KEY = f"{MODEL}@{REVISION}/{MODEL_FILE}:chunks-v2:single-passage"
RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANK_REVISION = "1427fd652930e4ba29e8149678df786c240d8825"
RERANK_FILE = "onnx/model_qint8_arm64.onnx"
PIPELINE_KEY = MODEL_KEY + f":{RERANK_MODEL}@{RERANK_REVISION}/{RERANK_FILE}:rrf-v5:single-pair"


def model_snapshot(repo, revision, model_file, cache, download):
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    files = [model_file, "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
    try:
        folder = snapshot_download(repo, revision=revision, cache_dir=str(cache), allow_patterns=files, local_files_only=True)
        if all((Path(folder) / name).is_file() for name in files):
            return folder
    except LocalEntryNotFoundError:
        pass
    if not download:
        raise ValueError("Los pesos locales todavía no están completos.")
    return snapshot_download(repo, revision=revision, cache_dir=str(cache), allow_patterns=files)


class LocalEmbedder:
    def __init__(self, cache: Path):
        self.cache = cache
        self.lock = RLock()
        self.model = None
        self.tokenizer = None

    def prepare(self, *, download=False):
        with self.lock:
            if self.model is not None:
                return
            from fastembed import TextEmbedding
            from fastembed.common.model_description import ModelSource, PoolingType
            from tokenizers import Tokenizer

            try:
                folder = model_snapshot(MODEL, REVISION, MODEL_FILE, self.cache, download)
                if MODEL not in {m["model"] for m in TextEmbedding.list_supported_models()}:
                    TextEmbedding.add_custom_model(
                        model=MODEL, pooling=PoolingType.MEAN, normalization=True,
                        sources=ModelSource(hf=MODEL), dim=384, model_file=MODEL_FILE,
                    )
                model = TextEmbedding(
                    model_name=MODEL, specific_model_path=folder,
                    cache_dir=str(self.cache), threads=2,
                    providers=["CPUExecutionProvider"], local_files_only=True,
                )
                tokenizer = Tokenizer.from_file(str(Path(folder) / "tokenizer.json"))
                tokenizer.no_truncation()
                tokenizer.no_padding()
                self.model, self.tokenizer = model, tokenizer
            except Exception as error:
                raise ValueError(
                    "No se pudo cargar el modelo local. Ejecuta .venv/bin/python -m aiec rag-index "
                    "con conexión a internet para preparar su descarga."
                ) from error

    def offsets(self, text):
        self.prepare()
        with self.lock:
            return self.tokenizer.encode(text, add_special_tokens=False).offsets

    def passages(self, texts):
        self.prepare()
        with self.lock:
            return np.asarray(list(self.model.embed(
                # La cuantización dinámica depende del lote. Un pasaje por inferencia
                # conserva el mismo vector al indexar desde cero o de forma incremental.
                ["passage: " + text for text in texts], batch_size=1,
            )), dtype="<f4")

    @lru_cache(maxsize=128)
    def query(self, text):
        self.prepare()
        offsets = self.offsets(text)
        if len(offsets) > 450:
            text = text[:offsets[449][1]]
        with self.lock:
            return np.asarray(list(self.model.embed(["query: " + text]))[0], dtype="<f4")


class LocalReranker:
    def __init__(self, cache: Path):
        self.cache = cache
        self.model = None
        self.lock = RLock()

    def prepare(self, *, download=False):
        with self.lock:
            if self.model is not None:
                return
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            from fastembed.common.model_description import ModelSource
            try:
                folder = model_snapshot(RERANK_MODEL, RERANK_REVISION, RERANK_FILE, self.cache, download)
                if RERANK_MODEL not in {m["model"] for m in TextCrossEncoder.list_supported_models()}:
                    TextCrossEncoder.add_custom_model(
                        model=RERANK_MODEL, sources=ModelSource(hf=RERANK_MODEL), model_file=RERANK_FILE,
                    )
                self.model = TextCrossEncoder(
                    model_name=RERANK_MODEL, specific_model_path=folder, cache_dir=str(self.cache),
                    threads=2, providers=["CPUExecutionProvider"], local_files_only=True,
                )
            except Exception as error:
                raise ValueError("No se pudo cargar la revisión de relevancia local. Ejecuta rag-index para preparar el modelo.") from error

    @lru_cache(maxsize=64)
    def _scores(self, query, texts):
        with self.lock:
            # Evita que un documento ajeno altere la puntuación al cambiar su lote.
            return tuple(float(score) for score in self.model.rerank(query, texts, batch_size=1))

    def score(self, query, texts):
        self.prepare()
        return np.asarray(self._scores(query, tuple(texts)), dtype=np.float32)
