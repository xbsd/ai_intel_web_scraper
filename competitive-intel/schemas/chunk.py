"""Pydantic model for retrieval-ready chunks (for RAG/vector store ingestion)."""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


class Chunk(BaseModel):
    id: str = Field(description="Unique chunk ID")
    competitor: str
    topic_id: str
    topic_name: str
    content_type: str = Field(
        description="'assessment', 'limitation', 'differentiator', 'objection', 'pitch', 'narrative', 'comparison'"
    )
    text: str = Field(description="The chunk text for embedding and retrieval")
    source_urls: List[str] = Field(
        default_factory=list, description="Source URLs backing this chunk"
    )
    confidence: str = Field(default="medium")
    generated_date: date
    embedding: Optional[List[float]] = Field(
        None, description="Vector embedding (populated during load phase)"
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata for filtering during retrieval"
    )
