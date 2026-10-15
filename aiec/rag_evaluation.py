"""Evaluación reproducible con documentos ficticios, separada del negocio real."""
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from .rag import KnowledgeBase


def evaluate(embedder, reranker):
    fixture = json.loads(Path(__file__).with_name("rag_eval.json").read_text())
    with TemporaryDirectory(prefix="aiec-rag-eval-") as temporary:
        root = Path(temporary)
        folder = root / "knowledge"
        folder.mkdir()
        for name, text in fixture["documents"].items():
            (folder / name).write_text(text, encoding="utf-8")
        catalog_path = root / "catalog.json"
        catalog_path.write_text(json.dumps(fixture["catalog"], ensure_ascii=False), encoding="utf-8")
        kb = KnowledgeBase(folder, root / "index", catalog_path=catalog_path,
                           embedder=embedder, reranker=reranker)
        kb.index()
        rows, timings = [], []
        hits, first, reciprocal, negatives, rejected = 0, 0, 0, 0, 0
        for case in fixture["cases"]:
            start = perf_counter()
            result = kb.search(case["query"], limit=3)
            timings.append((perf_counter() - start) * 1000)
            sources = [r["source"] for r in result["results"]]
            expected = case["expected"]
            if expected:
                rank = next((i for i, source in enumerate(sources, 1) if source == expected), None)
                hits += rank is not None
                first += rank == 1
                reciprocal += 1 / rank if rank else 0
                ok = rank is not None
            else:
                negatives += 1
                rejected += not sources
                ok = not sources
            rows.append({**case, "retrieved": sources, "passed": ok})
        positives = len(rows) - negatives
        metrics = {"cases": len(rows), "recall_at_3": round(hits / positives, 4),
                   "top_1_accuracy": round(first / positives, 4), "mrr_at_3": round(reciprocal / positives, 4),
                   "irrelevant_rejection": round(rejected / negatives, 4),
                   "median_query_ms": round(median(timings), 1)}
        return {"passed": hits / positives >= .95 and rejected == negatives and first / positives >= .85,
                "scope": "Recuperación con modelo local real; documentos ficticios. No evalúa las respuestas generadas por Claude.",
                "model": kb.status()["pipeline"], "min_similarity": kb.min_similarity, "min_relevance": kb.min_relevance,
                "metrics": metrics, "failures": [row for row in rows if not row["passed"]], "details": rows}
