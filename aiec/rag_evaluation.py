"""Banco congelado de recuperación de imprenta, separado del índice de trabajo."""
import json
import re
import unicodedata
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from .rag import KnowledgeBase


def evidence_text(text):
    plain = ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if not unicodedata.combining(c))
    return re.sub(r'(?<=\d),(?=\d)', '', plain)


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
                expected_sources = {expected} if isinstance(expected, str) else set(expected)
                terms = [evidence_text(term) for term in case.get('required_terms', [])]
                rank = next((i for i, hit in enumerate(result['results'], 1)
                             if hit['source'] in expected_sources
                             and all(term in evidence_text(hit['text']) for term in terms)), None)
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
                "scope": fixture['notice'],
                "model": kb.status()["pipeline"], "min_similarity": kb.min_similarity, "min_relevance": kb.min_relevance,
                "metrics": metrics, "failures": [row for row in rows if not row["passed"]], "details": rows}
