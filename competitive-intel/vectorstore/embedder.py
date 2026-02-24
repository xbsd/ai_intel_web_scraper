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

import tiktoken
from openai import BadRequestError, OpenAI
from tenacity import retry, retry_if_exception_type, retry_if_not_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 1536
MAX_BATCH_SIZE = 256  # OpenAI has 300K token/request limit; smaller batches avoid hitting it
MAX_TOKENS_PER_TEXT = 8000  # model limit is 8192; leave margin


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
        self._encoder = tiktoken.encoding_for_model(model)

    def _truncate_text(self, text: str) -> str:
        """Truncate text to fit within the embedding model's token limit."""
        tokens = self._encoder.encode(text)
        if len(tokens) <= MAX_TOKENS_PER_TEXT:
            return text
        logger.warning(
            "Truncating text from %d to %d tokens (first 60 chars: '%.60s')",
            len(tokens), MAX_TOKENS_PER_TEXT, text,
        )
        return self._encoder.decode(tokens[:MAX_TOKENS_PER_TEXT])

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_not_exception_type(BadRequestError),
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

        # Truncate any oversized texts to fit the model's token limit
        texts = [self._truncate_text(t) for t in texts]

        all_embeddings: list[list[float]] = []
        total_batches = (len(texts) + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE
        overall_start = time.time()

        for batch_idx in range(total_batches):
            start = batch_idx * MAX_BATCH_SIZE
            end = min(start + MAX_BATCH_SIZE, len(texts))
            batch = texts[start:end]

            batch_start = time.time()
            batch_embeddings = self._embed_batch(batch)
            batch_elapsed = time.time() - batch_start
            all_embeddings.extend(batch_embeddings)

            if show_progress:
                overall_elapsed = time.time() - overall_start
                rate = end / max(overall_elapsed, 0.001)
                eta = (len(texts) - end) / max(rate, 0.001)
                logger.info(
                    "Embedding batch %d/%d (%d texts, cumulative %d/%d) — batch %.1fs, elapsed %.1fs, ETA %.0fs",
                    batch_idx + 1, total_batches, len(batch), end, len(texts),
                    batch_elapsed, overall_elapsed, eta,
                )

            # Brief pause between batches to stay well within rate limits
            if batch_idx < total_batches - 1:
                time.sleep(0.5)

        total_elapsed = time.time() - overall_start
        logger.info(
            "Embedded %d texts (%d dimensions each) in %.1fs (%.1f texts/sec)",
            len(all_embeddings), self.dimensions, total_elapsed,
            len(all_embeddings) / max(total_elapsed, 0.001),
        )
        return all_embeddings

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string (convenience method for queries)."""
        result = self.embed([text], show_progress=False)
        return result[0]
