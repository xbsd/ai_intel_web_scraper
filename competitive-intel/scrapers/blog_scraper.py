"""Blog post scraper.

Crawls blog index pages to discover posts, then scrapes each post's content.
Scores posts by relevance using priority keywords from the competitor config.
"""

import logging
import re
from collections import deque
from datetime import date
from typing import Optional

from schemas.source_record import Credibility, SourceRecord, SourceType
from scrapers.utils import (
    RateLimiter,
    count_words,
    extract_content,
    extract_date_from_text,
    extract_links,
    fetch_url,
    generate_record_id,
    is_same_domain,
    normalize_url,
    save_records,
)

logger = logging.getLogger(__name__)


class BlogScraper:
    """Scrapes blog posts from a blog site."""

    def __init__(self, origin: str):
        self.origin = origin

    def scrape(self, blog_config: dict, output_dir: str) -> list[SourceRecord]:
        """Scrape blog posts based on config.

        Args:
            blog_config: Blog config dict with keys:
                base_url, scrape_method, content_selector, max_pages,
                rate_limit_seconds, priority_keywords
            output_dir: Directory for output JSON.

        Returns:
            List of SourceRecord objects.
        """
        base_url = blog_config["base_url"]
        content_selector = blog_config.get("content_selector", "article")
        max_pages = blog_config.get("max_pages", 50)
        rate_limit = blog_config.get("rate_limit_seconds", 0.5)
        priority_keywords = blog_config.get("priority_keywords", [])

        rate_limiter = RateLimiter(min_delay=rate_limit)

        # Discover blog post URLs by crawling the blog index
        post_urls = self._discover_posts(base_url, max_pages, rate_limiter)
        logger.info("Discovered %d blog post URLs from %s", len(post_urls), base_url)

        # Scrape each post
        records = []
        for url in post_urls:
            record = self._scrape_post(url, content_selector, rate_limiter, priority_keywords)
            if record:
                records.append(record)

            if len(records) >= max_pages:
                break

        # Sort by relevance score (stored in metadata)
        records.sort(key=lambda r: r.metadata.get("relevance_score", 0), reverse=True)

        if records:
            save_records(records, output_dir, f"{self.origin}_blog.json")

        logger.info("Scraped %d blog posts for %s", len(records), self.origin)
        return records

    def _discover_posts(
        self, base_url: str, max_pages: int, rate_limiter: RateLimiter
    ) -> list[str]:
        """Discover blog post URLs from index/listing pages."""
        visited = set()
        post_urls = []
        queue: deque[str] = deque()

        start_url = normalize_url(base_url)
        queue.append(start_url)
        visited.add(start_url)

        pages_checked = 0

        while queue and pages_checked < max_pages * 2:
            url = queue.popleft()
            pages_checked += 1

            response = fetch_url(url, rate_limiter=rate_limiter)
            if not response:
                continue

            links = extract_links(response.text, url)
            for link in links:
                norm_link = normalize_url(link)
                if norm_link in visited:
                    continue
                visited.add(norm_link)

                if not is_same_domain(norm_link, base_url):
                    continue

                # Heuristic: blog post URLs typically contain date patterns or /blog/post-slug
                if self._looks_like_post_url(norm_link, base_url):
                    post_urls.append(norm_link)
                elif self._looks_like_listing_page(norm_link, base_url):
                    queue.append(norm_link)

        return list(dict.fromkeys(post_urls))  # Dedupe preserving order

    def _looks_like_post_url(self, url: str, base_url: str) -> bool:
        """Heuristic: does this URL look like a blog post?"""
        path = url.replace(base_url.rstrip("/"), "")
        if not path or path == "/":
            return False
        # Skip tag/category listing pages
        if any(seg in path.lower() for seg in ["/tags/", "/category/", "/page/"]):
            return False
        # Post URLs typically have a slug segment
        segments = [s for s in path.split("/") if s]
        if len(segments) >= 1:
            # Has a meaningful slug (not just a short word)
            slug = segments[-1]
            if len(slug) > 5 and "-" in slug:
                return True
            # Or matches date pattern
            if re.search(r"\d{4}", path):
                return True
        return len(segments) >= 1

    def _looks_like_listing_page(self, url: str, base_url: str) -> bool:
        """Heuristic: does this URL look like a blog listing page?"""
        path = url.replace(base_url.rstrip("/"), "")
        if not path or path == "/":
            return False
        return any(
            seg in path.lower()
            for seg in ["/page/", "/tags/", "/category/", "/archive"]
        )

    def _scrape_post(
        self,
        url: str,
        content_selector: str,
        rate_limiter: RateLimiter,
        priority_keywords: list[str],
    ) -> Optional[SourceRecord]:
        """Scrape a single blog post."""
        response = fetch_url(url, rate_limiter=rate_limiter)
        if not response:
            return None

        title, text = extract_content(response.text, content_selector, url)
        if not text.strip() or count_words(text) < 50:
            return None

        # Score relevance
        relevance_score = self._score_relevance(title + " " + text, priority_keywords)

        # Try to extract publication date
        content_date = extract_date_from_text(text)

        return SourceRecord(
            id=generate_record_id(self.origin, "blog", url),
            origin=self.origin,
            source_type=SourceType.BLOG,
            url=url,
            title=title,
            text=text,
            scraped_date=date.today(),
            content_date=content_date,
            credibility=Credibility.OFFICIAL,
            word_count=count_words(text),
            metadata={
                "relevance_score": relevance_score,
                "priority_keywords_matched": [
                    kw for kw in priority_keywords
                    if kw.lower() in (title + " " + text).lower()
                ],
            },
        )

    def _score_relevance(self, text: str, keywords: list[str]) -> float:
        """Score text relevance based on keyword matches."""
        if not keywords:
            return 0.0
        text_lower = text.lower()
        matches = sum(1 for kw in keywords if kw.lower() in text_lower)
        return matches / len(keywords)


def scrape_blog(competitor_config: dict, data_dir: str) -> list[SourceRecord]:
    """Top-level function to scrape blog for a competitor."""
    origin = competitor_config["short_name"]
    blog_config = competitor_config.get("sources", {}).get("blog")
    if not blog_config:
        logger.info("No blog config for %s, skipping", origin)
        return []

    output_dir = f"{data_dir}/{origin}/blog"
    scraper = BlogScraper(origin)
    return scraper.scrape(blog_config, output_dir)
