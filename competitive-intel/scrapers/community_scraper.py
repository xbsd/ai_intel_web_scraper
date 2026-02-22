"""Community scraper for Reddit and Hacker News.

Uses Reddit's public JSON API and Hacker News Algolia API to find
discussions, complaints, comparisons, and real-world experience reports.
"""

import logging
import time
from datetime import date, datetime
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from schemas.source_record import (
    Credibility,
    HNMetadata,
    RedditMetadata,
    Sentiment,
    SourceRecord,
    SourceType,
)
from scrapers.utils import count_words, generate_record_id, save_records

logger = logging.getLogger(__name__)

REDDIT_HEADERS = {
    "User-Agent": "CompetitiveIntel/1.0 (competitive intelligence research)",
}

HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


class CommunityScraper:
    """Scrapes Reddit and Hacker News for community discussions."""

    def __init__(self, origin: str):
        self.origin = origin

    def scrape(self, community_config: dict, output_dir: str) -> list[SourceRecord]:
        """Scrape all community sources.

        Args:
            community_config: Config dict with 'reddit' and/or 'hackernews' keys.
            output_dir: Directory for output JSON.

        Returns:
            List of SourceRecord objects.
        """
        all_records = []

        reddit_config = community_config.get("reddit")
        if reddit_config:
            reddit_records = self._scrape_reddit(reddit_config, output_dir)
            all_records.extend(reddit_records)

        hn_config = community_config.get("hackernews")
        if hn_config:
            hn_records = self._scrape_hackernews(hn_config, output_dir)
            all_records.extend(hn_records)

        return all_records

    def _scrape_reddit(self, config: dict, output_dir: str) -> list[SourceRecord]:
        """Scrape Reddit using the public JSON API."""
        search_terms = config.get("search_terms", [])
        subreddits = config.get("subreddits", [])
        max_results = config.get("max_results_per_query", 50)

        seen_urls = set()
        records = []

        # Search globally
        for term in search_terms:
            results = self._reddit_search(term, max_results=max_results)
            for post in results:
                url = f"https://www.reddit.com{post.get('permalink', '')}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                record = self._reddit_post_to_record(post, url)
                if record:
                    records.append(record)

            time.sleep(1.0)  # Rate limit between searches

        # Search in specific subreddits
        for subreddit in subreddits:
            for term in search_terms[:3]:  # Limit per-subreddit queries
                results = self._reddit_search(
                    term, subreddit=subreddit, max_results=max_results // 2
                )
                for post in results:
                    url = f"https://www.reddit.com{post.get('permalink', '')}"
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    record = self._reddit_post_to_record(post, url)
                    if record:
                        records.append(record)

                time.sleep(1.0)

        if records:
            save_records(records, f"{output_dir}", f"{self.origin}_reddit.json")

        logger.info("Scraped %d Reddit posts for %s", len(records), self.origin)
        return records

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def _reddit_search(
        self, query: str, subreddit: Optional[str] = None, max_results: int = 50
    ) -> list[dict]:
        """Search Reddit using the public JSON API."""
        if subreddit:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {"q": query, "restrict_sr": "on", "limit": min(max_results, 100), "sort": "relevance"}
        else:
            url = "https://www.reddit.com/search.json"
            params = {"q": query, "limit": min(max_results, 100), "sort": "relevance"}

        try:
            resp = requests.get(url, headers=REDDIT_HEADERS, params=params, timeout=15)
            if resp.status_code == 429:
                logger.warning("Reddit rate limited, sleeping 60s")
                time.sleep(60)
                return []
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            return [c.get("data", {}) for c in children]
        except (requests.RequestException, ValueError) as e:
            logger.error("Reddit search error for '%s': %s", query, e)
            return []

    def _reddit_post_to_record(self, post: dict, url: str) -> Optional[SourceRecord]:
        """Convert a Reddit post dict to a SourceRecord."""
        title = post.get("title", "")
        selftext = post.get("selftext", "")
        text = f"# {title}\n\n{selftext}" if selftext else f"# {title}"

        if count_words(text) < 10:
            return None

        score = post.get("score", 0)
        sentiment = self._estimate_sentiment(title + " " + selftext)

        metadata = RedditMetadata(
            subreddit=post.get("subreddit", ""),
            score=score,
            num_comments=post.get("num_comments", 0),
            author=post.get("author", "unknown"),
            created_utc=post.get("created_utc", 0),
            permalink=post.get("permalink", ""),
        )

        return SourceRecord(
            id=generate_record_id(self.origin, "community_reddit", url),
            origin=self.origin,
            source_type=SourceType.COMMUNITY_REDDIT,
            url=url,
            title=title,
            text=text,
            scraped_date=date.today(),
            content_date=self._utc_to_date(post.get("created_utc", 0)),
            credibility=Credibility.COMMUNITY,
            sentiment=sentiment,
            word_count=count_words(text),
            metadata=metadata.model_dump(),
        )

    def _scrape_hackernews(self, config: dict, output_dir: str) -> list[SourceRecord]:
        """Scrape Hacker News using the Algolia API."""
        search_terms = config.get("search_terms", [])
        max_results = config.get("max_results_per_query", 50)

        seen_ids = set()
        records = []

        for term in search_terms:
            results = self._hn_search(term, max_results=max_results)
            for hit in results:
                hn_id = hit.get("objectID", "")
                if hn_id in seen_ids:
                    continue
                seen_ids.add(hn_id)

                record = self._hn_hit_to_record(hit)
                if record:
                    records.append(record)

            time.sleep(0.5)  # Be nice to Algolia

        if records:
            save_records(records, f"{output_dir}", f"{self.origin}_hn.json")

        logger.info("Scraped %d HN stories for %s", len(records), self.origin)
        return records

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def _hn_search(self, query: str, max_results: int = 50) -> list[dict]:
        """Search Hacker News via Algolia API."""
        url = f"{HN_ALGOLIA_BASE}/search"
        params = {
            "query": query,
            "hitsPerPage": min(max_results, 100),
            "tags": "story",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json().get("hits", [])
        except (requests.RequestException, ValueError) as e:
            logger.error("HN search error for '%s': %s", query, e)
            return []

    def _hn_hit_to_record(self, hit: dict) -> Optional[SourceRecord]:
        """Convert an HN Algolia hit to a SourceRecord."""
        title = hit.get("title", "")
        story_url = hit.get("url", "")
        hn_id = hit.get("objectID", "")
        hn_url = f"https://news.ycombinator.com/item?id={hn_id}"

        # Use the HN discussion URL as the canonical URL
        text = f"# {title}\n\nHN Discussion: {hn_url}"
        if story_url:
            text += f"\nOriginal URL: {story_url}"

        # Fetch top comments for high-scoring stories
        points = hit.get("points", 0) or 0
        top_comments = []
        if points > 5:
            top_comments = self._fetch_hn_comments(hn_id)

        if top_comments:
            text += "\n\n## Top Comments\n\n"
            text += "\n\n---\n\n".join(top_comments[:5])

        metadata = HNMetadata(
            hn_id=int(hn_id) if hn_id.isdigit() else 0,
            points=points,
            num_comments=hit.get("num_comments", 0) or 0,
            author=hit.get("author", "unknown") or "unknown",
            created_at=hit.get("created_at", ""),
            top_comments=top_comments[:5],
        )

        sentiment = self._estimate_sentiment(title + " " + " ".join(top_comments))

        return SourceRecord(
            id=generate_record_id(self.origin, "community_hn", hn_url),
            origin=self.origin,
            source_type=SourceType.COMMUNITY_HN,
            url=hn_url,
            title=title,
            text=text,
            scraped_date=date.today(),
            credibility=Credibility.COMMUNITY,
            sentiment=sentiment,
            word_count=count_words(text),
            metadata=metadata.model_dump(),
        )

    def _fetch_hn_comments(self, story_id: str, max_comments: int = 10) -> list[str]:
        """Fetch top comments for an HN story."""
        url = f"{HN_ALGOLIA_BASE}/items/{story_id}"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            comments = []
            for child in data.get("children", [])[:max_comments]:
                text = child.get("text", "")
                if text:
                    # Strip HTML tags from HN comments
                    import re
                    clean = re.sub(r"<[^>]+>", " ", text).strip()
                    author = child.get("author", "anon")
                    comments.append(f"**{author}**: {clean}")
            return comments
        except (requests.RequestException, ValueError):
            return []

    def _estimate_sentiment(self, text: str) -> Sentiment:
        """Basic keyword-based sentiment estimation."""
        text_lower = text.lower()

        negative_signals = [
            "problem", "issue", "bug", "broken", "crash", "slow",
            "limitation", "missing", "doesn't support", "can't",
            "disappointing", "frustrating", "worse", "awful",
            "not production", "not ready", "unstable",
        ]
        positive_signals = [
            "fast", "great", "excellent", "love", "amazing",
            "impressed", "recommend", "solid", "reliable",
            "production ready", "best", "performant",
        ]

        neg_count = sum(1 for s in negative_signals if s in text_lower)
        pos_count = sum(1 for s in positive_signals if s in text_lower)

        if neg_count > pos_count + 1:
            return Sentiment.NEGATIVE
        elif pos_count > neg_count + 1:
            return Sentiment.POSITIVE
        elif neg_count > 0 and pos_count > 0:
            return Sentiment.MIXED
        return Sentiment.NEUTRAL

    def _utc_to_date(self, utc_timestamp: float) -> Optional[date]:
        """Convert a UTC timestamp to a date."""
        if not utc_timestamp:
            return None
        try:
            return datetime.utcfromtimestamp(utc_timestamp).date()
        except (OSError, ValueError):
            return None


def scrape_community(competitor_config: dict, data_dir: str) -> list[SourceRecord]:
    """Top-level function to scrape community sources for a competitor."""
    origin = competitor_config["short_name"]
    community_config = competitor_config.get("sources", {}).get("community")
    if not community_config:
        logger.info("No community config for %s, skipping", origin)
        return []

    output_dir = f"{data_dir}/{origin}/community"
    scraper = CommunityScraper(origin)
    return scraper.scrape(community_config, output_dir)
