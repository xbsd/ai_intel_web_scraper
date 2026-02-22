"""GitHub scraper for issues, discussions, and releases.

Uses the GitHub REST API for issues and releases, and the GraphQL API
for discussions. Requires a GITHUB_TOKEN environment variable.
"""

import logging
import os
from datetime import date
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from schemas.source_record import (
    Credibility,
    GitHubDiscussionMetadata,
    GitHubIssueMetadata,
    GitHubReleaseMetadata,
    Sentiment,
    SourceRecord,
    SourceType,
)
from scrapers.utils import count_words, generate_record_id, save_records

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"


def _get_github_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "CompetitiveIntel/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    else:
        logger.warning("GITHUB_TOKEN not set — rate limited to 60 requests/hour")
    return headers


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
)
def _github_get(url: str, params: Optional[dict] = None) -> Optional[requests.Response]:
    headers = _get_github_headers()
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code == 403:
        logger.error("GitHub rate limit hit. Remaining: %s", resp.headers.get("X-RateLimit-Remaining"))
        return None
    resp.raise_for_status()
    return resp


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
)
def _github_graphql(query: str, variables: Optional[dict] = None) -> Optional[dict]:
    headers = _get_github_headers()
    headers["Content-Type"] = "application/json"
    resp = requests.post(
        GITHUB_GRAPHQL,
        json={"query": query, "variables": variables or {}},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        logger.error("GraphQL errors: %s", data["errors"])
        return None
    return data


class GitHubScraper:
    """Scrapes GitHub issues, discussions, and releases for a repository."""

    def __init__(self, origin: str):
        self.origin = origin

    def scrape(self, repo_config: dict, output_dir: str) -> dict[str, list[SourceRecord]]:
        """Scrape a GitHub repo based on config.

        Args:
            repo_config: Config dict with keys: repo, scrape_issues, scrape_discussions,
                scrape_releases, max_issues, max_discussions, etc.
            output_dir: Directory for output JSON files.

        Returns:
            Dict with keys 'issues', 'discussions', 'releases', each a list of SourceRecord.
        """
        repo = repo_config["repo"]
        results = {"issues": [], "discussions": [], "releases": []}

        if repo_config.get("scrape_issues", False):
            results["issues"] = self._scrape_issues(repo, repo_config, output_dir)

        if repo_config.get("scrape_discussions", False):
            results["discussions"] = self._scrape_discussions(repo, repo_config, output_dir)

        if repo_config.get("scrape_releases", False):
            results["releases"] = self._scrape_releases(repo, output_dir)

        return results

    def _scrape_issues(
        self, repo: str, config: dict, output_dir: str
    ) -> list[SourceRecord]:
        """Scrape GitHub issues."""
        max_issues = config.get("max_issues", 500)
        sort = config.get("issue_sort", "comments")
        direction = config.get("issue_direction", "desc")
        fetch_comments_top_n = config.get("fetch_comments_for_top_n", 50)
        labels_of_interest = set(config.get("labels_of_interest", []))

        records = []
        page = 1
        per_page = 100

        while len(records) < max_issues:
            url = f"{GITHUB_API_BASE}/repos/{repo}/issues"
            params = {
                "state": "all",
                "sort": sort,
                "direction": direction,
                "per_page": per_page,
                "page": page,
            }
            resp = _github_get(url, params)
            if not resp:
                break

            items = resp.json()
            if not items:
                break

            for item in items:
                # Skip pull requests (GitHub API returns PRs in issues endpoint)
                if "pull_request" in item:
                    continue

                if len(records) >= max_issues:
                    break

                issue_labels = [l["name"] for l in item.get("labels", [])]
                is_bug = any(l.lower() in ("bug", "defect") for l in issue_labels)
                is_feature = any(
                    l.lower() in ("enhancement", "feature request", "feature")
                    for l in issue_labels
                )

                body = item.get("body") or ""
                title = item.get("title", "")
                issue_number = item["number"]

                # Fetch comments for top N issues
                top_comments = []
                if len(records) < fetch_comments_top_n and item.get("comments", 0) > 0:
                    top_comments = self._fetch_issue_comments(repo, issue_number)

                text = f"# {title}\n\n{body}"
                if top_comments:
                    text += "\n\n## Top Comments\n\n"
                    text += "\n\n---\n\n".join(top_comments)

                issue_url = item["html_url"]
                metadata = GitHubIssueMetadata(
                    issue_number=issue_number,
                    state=item["state"],
                    labels=issue_labels,
                    comments_count=item.get("comments", 0),
                    created_at=item["created_at"],
                    updated_at=item["updated_at"],
                    closed_at=item.get("closed_at"),
                    author=item["user"]["login"],
                    top_comments=top_comments[:5],
                    is_feature_request=is_feature,
                    is_bug=is_bug,
                )

                # Determine sentiment from labels/content
                sentiment = Sentiment.NEUTRAL
                if is_bug:
                    sentiment = Sentiment.NEGATIVE
                elif is_feature:
                    sentiment = Sentiment.NEUTRAL

                record = SourceRecord(
                    id=generate_record_id(self.origin, "github_issue", issue_url),
                    origin=self.origin,
                    source_type=SourceType.GITHUB_ISSUE,
                    url=issue_url,
                    title=title,
                    text=text,
                    scraped_date=date.today(),
                    credibility=Credibility.COMMUNITY,
                    sentiment=sentiment,
                    word_count=count_words(text),
                    metadata=metadata.model_dump(),
                )
                records.append(record)

            page += 1

        if records:
            save_records(records, f"{output_dir}/github_issues", f"{repo.replace('/', '_')}_issues.json")

        logger.info("Scraped %d issues from %s", len(records), repo)
        return records

    def _fetch_issue_comments(self, repo: str, issue_number: int, max_comments: int = 10) -> list[str]:
        """Fetch top comments for an issue."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{issue_number}/comments"
        resp = _github_get(url, {"per_page": max_comments})
        if not resp:
            return []

        comments = []
        for c in resp.json():
            body = c.get("body", "")
            if body.strip():
                author = c.get("user", {}).get("login", "unknown")
                comments.append(f"**{author}**: {body}")
        return comments

    def _scrape_discussions(
        self, repo: str, config: dict, output_dir: str
    ) -> list[SourceRecord]:
        """Scrape GitHub discussions using GraphQL API."""
        max_discussions = config.get("max_discussions", 200)
        owner, name = repo.split("/")

        records = []
        cursor = None

        while len(records) < max_discussions:
            batch_size = min(50, max_discussions - len(records))
            query = """
            query($owner: String!, $name: String!, $first: Int!, $after: String) {
              repository(owner: $owner, name: $name) {
                discussions(first: $first, after: $after, orderBy: {field: CREATED_AT, direction: DESC}) {
                  pageInfo {
                    hasNextPage
                    endCursor
                  }
                  nodes {
                    number
                    title
                    body
                    category { name }
                    isAnswered
                    answer { body }
                    comments { totalCount }
                    createdAt
                    author { login }
                    url
                  }
                }
              }
            }
            """
            variables = {
                "owner": owner,
                "name": name,
                "first": batch_size,
                "after": cursor,
            }

            data = _github_graphql(query, variables)
            if not data:
                break

            discussions_data = data.get("data", {}).get("repository", {}).get("discussions", {})
            nodes = discussions_data.get("nodes", [])
            if not nodes:
                break

            for d in nodes:
                title = d.get("title", "")
                body = d.get("body", "")
                answer_body = None
                if d.get("isAnswered") and d.get("answer"):
                    answer_body = d["answer"].get("body", "")

                text = f"# {title}\n\n{body}"
                if answer_body:
                    text += f"\n\n## Accepted Answer\n\n{answer_body}"

                discussion_url = d.get("url", "")
                metadata = GitHubDiscussionMetadata(
                    discussion_number=d["number"],
                    category=d.get("category", {}).get("name", ""),
                    is_answered=d.get("isAnswered", False),
                    answer_body=answer_body,
                    comments_count=d.get("comments", {}).get("totalCount", 0),
                    created_at=d.get("createdAt", ""),
                    author=d.get("author", {}).get("login", "unknown") if d.get("author") else "unknown",
                )

                record = SourceRecord(
                    id=generate_record_id(self.origin, "github_discussion", discussion_url),
                    origin=self.origin,
                    source_type=SourceType.GITHUB_DISCUSSION,
                    url=discussion_url,
                    title=title,
                    text=text,
                    scraped_date=date.today(),
                    credibility=Credibility.COMMUNITY,
                    word_count=count_words(text),
                    metadata=metadata.model_dump(),
                )
                records.append(record)

            page_info = discussions_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        if records:
            save_records(
                records,
                f"{output_dir}/github_discussions",
                f"{repo.replace('/', '_')}_discussions.json",
            )

        logger.info("Scraped %d discussions from %s", len(records), repo)
        return records

    def _scrape_releases(self, repo: str, output_dir: str) -> list[SourceRecord]:
        """Scrape GitHub releases."""
        records = []
        page = 1

        while True:
            url = f"{GITHUB_API_BASE}/repos/{repo}/releases"
            resp = _github_get(url, {"per_page": 100, "page": page})
            if not resp:
                break

            items = resp.json()
            if not items:
                break

            for item in items:
                tag = item.get("tag_name", "")
                name = item.get("name", tag)
                body = item.get("body") or ""
                release_url = item["html_url"]

                text = f"# Release {name} ({tag})\n\n{body}"

                metadata = GitHubReleaseMetadata(
                    tag_name=tag,
                    release_name=name,
                    is_prerelease=item.get("prerelease", False),
                    created_at=item.get("created_at", ""),
                    published_at=item.get("published_at"),
                )

                record = SourceRecord(
                    id=generate_record_id(self.origin, "github_release", release_url),
                    origin=self.origin,
                    source_type=SourceType.GITHUB_RELEASE,
                    url=release_url,
                    title=f"Release {name}",
                    text=text,
                    scraped_date=date.today(),
                    credibility=Credibility.OFFICIAL,
                    word_count=count_words(text),
                    metadata=metadata.model_dump(),
                )
                records.append(record)

            page += 1

        if records:
            save_records(
                records,
                f"{output_dir}/github_releases",
                f"{repo.replace('/', '_')}_releases.json",
            )

        logger.info("Scraped %d releases from %s", len(records), repo)
        return records


def scrape_github(competitor_config: dict, data_dir: str) -> list[SourceRecord]:
    """Top-level function to scrape all GitHub sources for a competitor."""
    origin = competitor_config["short_name"]
    github_config = competitor_config.get("sources", {}).get("github", {})
    repos = github_config.get("repos", [])

    output_dir = f"{data_dir}/{origin}"
    scraper = GitHubScraper(origin)
    all_records = []

    for repo_config in repos:
        results = scraper.scrape(repo_config, output_dir)
        for records in results.values():
            all_records.extend(records)

    logger.info("Total GitHub records for %s: %d", origin, len(all_records))
    return all_records
