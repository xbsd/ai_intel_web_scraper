"""OpenAI embedding generator with batching and retry logic.

Uses text-embedding-3-small (1536 dimensions) for cost-effective,
high-quality embeddings suitable for competitive intelligence retrieval.

Supports batching (up to 2048 texts per API call) and exponential
backoff retry for rate limit handling.
"""

import logging
import os
import time
from typing import Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 1536
MAX_BATCH_SIZE = 512  # conservative; API supports 2048 but smaller = more resilient


class Embedder:
    """Generate embeddings using OpenAI's text-embedding-3-small model."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        api_key: Optional[str] = None,
    ):
        self.model = model
        self.dimensions = dimensions
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(
            "Embedding API retry %d after error: %s",
            retry_state.attempt_number,
            retry_state.outcome.exception() if retry_state.outcome else "unknown",
        ),
    )
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a single batch of texts via the API."""
        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions,
        )
        # Response data is sorted by index
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]

    def embed(self, texts: list[str], show_progress: bool = True) -> list[list[float]]:
        """Embed a list of texts, handling batching automatically.

        Args:
            texts: List of text strings to embed.
            show_progress: Whether to log progress.

        Returns:
            List of embedding vectors (same order as input texts).
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        total_batches = (len(texts) + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE

        for batch_idx in range(total_batches):
            start = batch_idx * MAX_BATCH_SIZE
            end = min(start + MAX_BATCH_SIZE, len(texts))
            batch = texts[start:end]

            if show_progress:
                logger.info(
                    "Embedding batch %d/%d (%d texts, cumulative %d/%d)",
                    batch_idx + 1, total_batches, len(batch), end, len(texts),
                )

            batch_embeddings = self._embed_batch(batch)
            all_embeddings.extend(batch_embeddings)

            # Brief pause between batches to stay well within rate limits
            if batch_idx < total_batches - 1:
                time.sleep(0.5)

        logger.info("Embedded %d texts (%d dimensions each)", len(all_embeddings), self.dimensions)
        return all_embeddings

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string (convenience method for queries)."""
        result = self.embed([text], show_progress=False)
        return result[0]
