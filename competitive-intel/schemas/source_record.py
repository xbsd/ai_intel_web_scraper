"""Pydantic models for scraped source records."""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date
from enum import Enum


class SourceType(str, Enum):
    OFFICIAL_DOCS = "official_docs"
    BLOG = "blog"
    GITHUB_ISSUE = "github_issue"
    GITHUB_DISCUSSION = "github_discussion"
    GITHUB_RELEASE = "github_release"
    COMMUNITY_REDDIT = "community_reddit"
    COMMUNITY_HN = "community_hn"
    BENCHMARK = "benchmark"
    PRODUCT_PAGE = "product_page"
    CASE_STUDY = "case_study"
    WHITEPAPER = "whitepaper"
    COMPARISON_PAGE = "comparison_page"


class Credibility(str, Enum):
    OFFICIAL = "official"
    THIRD_PARTY = "third_party"
    COMMUNITY = "community"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class SourceRecord(BaseModel):
    id: str = Field(description="Unique identifier: {origin}-{source_type}-{hash}")
    origin: str = Field(description="'kx' | 'questdb' | 'clickhouse' | ...")
    source_type: SourceType
    url: str
    title: str
    text: str = Field(description="Extracted plain text content")
    scraped_date: date
    content_date: Optional[date] = Field(
        None, description="Publication/update date if known"
    )
    topics: List[str] = Field(
        default_factory=list, description="Matched taxonomy topic IDs"
    )
    subtopics: List[str] = Field(default_factory=list)
    credibility: Credibility = Credibility.OFFICIAL
    sentiment: Sentiment = Sentiment.NEUTRAL
    word_count: int = 0
    metadata: dict = Field(
        default_factory=dict, description="Source-specific metadata"
    )


class GitHubIssueMetadata(BaseModel):
    issue_number: int
    state: str
    labels: List[str] = Field(default_factory=list)
    comments_count: int = 0
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None
    author: str
    top_comments: List[str] = Field(default_factory=list)
    is_feature_request: bool = False
    is_bug: bool = False


class GitHubDiscussionMetadata(BaseModel):
    discussion_number: int
    category: str
    is_answered: bool = False
    answer_body: Optional[str] = None
    comments_count: int = 0
    created_at: str
    author: str


class GitHubReleaseMetadata(BaseModel):
    tag_name: str
    release_name: str
    is_prerelease: bool = False
    created_at: str
    published_at: Optional[str] = None


class RedditMetadata(BaseModel):
    subreddit: str
    score: int = 0
    num_comments: int = 0
    author: str
    created_utc: float
    permalink: str
    top_comments: List[str] = Field(default_factory=list)


class HNMetadata(BaseModel):
    hn_id: int
    points: int = 0
    num_comments: int = 0
    author: str
    created_at: str
    top_comments: List[str] = Field(default_factory=list)
