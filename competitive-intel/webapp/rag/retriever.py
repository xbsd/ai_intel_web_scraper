"""Advanced retrieval layer with HyDE, multi-query, and reranking.

Wraps the existing VectorStore + Embedder with RAG best practices:
  - Hypothetical Document Embeddings (HyDE) for improved recall
  - Multi-query expansion (search original + sub-queries)
  - Metadata-aware filtering
  - Reciprocal Rank Fusion for merging result sets
  - Deduplication across multiple retrievals
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """A single retrieved chunk with metadata and relevance score."""
    chunk_id: str
    text: str
    score: float  # 0-1, higher = more relevant
    competitor: str
    source_type: str
    source_url: str
    source_title: str
    primary_topic: str
    topic_ids: str  # comma-separated
    credibility: str
    content_date: str
    chunk_index: int
    parent_doc_id: str
    token_count: int
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_chroma_result(cls, chunk_id: str, doc: str, meta: dict, distance: float) -> "RetrievedChunk":
        score = max(0.0, 1.0 - distance)
        return cls(
            chunk_id=chunk_id,
            text=doc,
            score=score,
            competitor=meta.get("competitor", ""),
            source_type=meta.get("source_type", ""),
            source_url=meta.get("source_url", ""),
            source_title=meta.get("source_title", ""),
            primary_topic=meta.get("primary_topic", ""),
            topic_ids=meta.get("topic_ids", ""),
            credibility=meta.get("credibility", ""),
            content_date=meta.get("content_date", ""),
            chunk_index=meta.get("chunk_index", 0),
            parent_doc_id=meta.get("parent_doc_id", ""),
            token_count=meta.get("token_count", 0),
            metadata={k: v for k, v in meta.items() if k not in {
                "competitor", "source_type", "source_url", "source_title",
                "primary_topic", "topic_ids", "credibility", "content_date",
                "chunk_index", "parent_doc_id", "token_count",
            }},
        )


class Retriever:
    """Advanced retrieval engine wrapping VectorStore + Embedder."""

    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        sub_queries: Optional[list[str]] = None,
        hyde_passage: Optional[str] = None,
        competitors: Optional[list[str]] = None,
        topics: Optional[list[str]] = None,
        source_types: Optional[list[str]] = None,
        n_results: int = 10,
        collections: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        """Multi-strategy retrieval with result fusion.

        Args:
            query: The user's original query.
            sub_queries: Decomposed sub-queries for multi-query expansion.
            hyde_passage: Hypothetical answer for HyDE-enhanced retrieval.
            competitors: Filter to specific competitors.
            topics: Filter to specific topic IDs.
            source_types: Filter to specific source types.
            n_results: Number of results per query.
            collections: Which collections to search (default: both).

        Returns:
            Deduplicated, ranked list of RetrievedChunks.
        """
        if collections is None:
            collections = ["competitive_intel", "competitive_comparisons"]

        # Build metadata filter
        where = self._build_where(competitors, topics, source_types)

        # Collect all result sets for fusion
        all_result_sets: list[list[RetrievedChunk]] = []

        # 1. Direct query embedding
        for coll in collections:
            try:
                results = self._search(query, coll, n_results, where)
                if results:
                    all_result_sets.append(results)
            except Exception as e:
                logger.warning("Search failed for collection %s: %s", coll, e)

        # 2. Sub-query expansion
        if sub_queries:
            for sq in sub_queries[:3]:
                for coll in collections:
                    try:
                        results = self._search(sq, coll, n_results // 2, where)
                        if results:
                            all_result_sets.append(results)
                    except Exception as e:
                        logger.warning("Sub-query search failed: %s", e)

        # 3. HyDE — embed the hypothetical passage for semantic similarity
        if hyde_passage:
            for coll in collections:
                try:
                    results = self._search(hyde_passage, coll, n_results // 2, where)
                    if results:
                        all_result_sets.append(results)
                except Exception as e:
                    logger.warning("HyDE search failed: %s", e)

        # Fuse and deduplicate
        if not all_result_sets:
            return []

        fused = self._reciprocal_rank_fusion(all_result_sets, k=60)
        return fused[:n_results]

    def search_single(
        self,
        query: str,
        collection: str = "competitive_intel",
        n_results: int = 8,
        where: Optional[dict] = None,
    ) -> list[RetrievedChunk]:
        """Simple single-query search (used by ReAct agent)."""
        return self._search(query, collection, n_results, where)

    def _search(
        self,
        query: str,
        collection: str,
        n_results: int,
        where: Optional[dict],
    ) -> list[RetrievedChunk]:
        """Execute a single vector search."""
        # Check if the collection exists and has data before querying
        try:
            col = self.store.client.get_collection(collection)
            if col.count() == 0:
                return []
        except Exception:
            return []  # collection doesn't exist yet

        try:
            results = self.store.query_by_text(
                query_text=query,
                embedder=self.embedder,
                collection_name=collection,
                n_results=n_results,
                where=where,
            )
        except Exception as e:
            logger.warning("Vector query failed for %s: %s", collection, e)
            return []

        chunks = []
        if results and results.get("ids") and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                chunk = RetrievedChunk.from_chroma_result(
                    chunk_id=results["ids"][0][i],
                    doc=results["documents"][0][i],
                    meta=results["metadatas"][0][i],
                    distance=results["distances"][0][i],
                )
                chunks.append(chunk)
        return chunks

    def _build_where(
        self,
        competitors: Optional[list[str]],
        topics: Optional[list[str]],
        source_types: Optional[list[str]],
    ) -> Optional[dict]:
        """Build a ChromaDB where clause from filters."""
        conditions = []

        if competitors and len(competitors) == 1:
            conditions.append({"competitor": competitors[0]})
        elif competitors and len(competitors) > 1:
            conditions.append({"competitor": {"$in": competitors}})

        if topics and len(topics) == 1:
            conditions.append({"primary_topic": topics[0]})
        elif topics and len(topics) > 1:
            conditions.append({"primary_topic": {"$in": topics}})

        if source_types and len(source_types) == 1:
            conditions.append({"source_type": source_types[0]})
        elif source_types and len(source_types) > 1:
            conditions.append({"source_type": {"$in": source_types}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _reciprocal_rank_fusion(
        self,
        result_sets: list[list[RetrievedChunk]],
        k: int = 60,
    ) -> list[RetrievedChunk]:
        """Merge multiple ranked lists using Reciprocal Rank Fusion.

        RRF score = sum(1 / (k + rank_i)) across all result sets.
        This is robust to score calibration differences between queries.
        """
        scores: dict[str, float] = {}
        chunks: dict[str, RetrievedChunk] = {}

        for result_set in result_sets:
            for rank, chunk in enumerate(result_set):
                rrf_score = 1.0 / (k + rank + 1)
                if chunk.chunk_id in scores:
                    scores[chunk.chunk_id] += rrf_score
                    # Keep the chunk with the higher original score
                    if chunk.score > chunks[chunk.chunk_id].score:
                        chunks[chunk.chunk_id] = chunk
                else:
                    scores[chunk.chunk_id] = rrf_score
                    chunks[chunk.chunk_id] = chunk

        # Sort by fused score and update the chunk scores
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        result = []
        for chunk_id, fused_score in ranked:
            chunk = chunks[chunk_id]
            chunk.score = round(fused_score, 6)
            result.append(chunk)

        return result
