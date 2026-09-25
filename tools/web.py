"""
Internet tools: web search and reading web pages.

Free, no API keys:
  - Search uses DuckDuckGo's plain-HTML page (html.duckduckgo.com).
  - If that fails, it falls back to Wikipedia's public search API.
Only the Python standard library is used.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from tools.base import Tool, ToolError
from utils.logger import get_logger

log = get_logger("tools.web")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_DOWNLOAD_BYTES = 3_000_000
DDG_URL = "https://html.duckduckgo.com/html/"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


# --- low-level helpers --------------------------------------------------------

def http_get(url: str, data: dict | None = None, timeout: int = 20) -> tuple[str, str]:
    """Download `url` (POST if `data` is given). Returns (text, content_type)."""
    body = urllib.parse.urlencode(data).encode() if data else None
    request = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_DOWNLOAD_BYTES)
            content_type = response.headers.get("Content-Type", "")
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as error:
        raise ToolError(f"The website returned HTTP {error.code} for {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ToolError(f"Could not reach {url}: {getattr(error, 'reason', error)}")
    return raw.decode(charset, errors="replace"), content_type


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


class DuckDuckGoParser(HTMLParser):
    """Pulls title/url/snippet out of DuckDuckGo's HTML results page."""

    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self._capture = None  # "title" or "snippet" while inside those links

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if "result__a" in classes:
            self.results.append({"title": "", "url": clean_result_url(attrs.get("href", "")), "snippet": ""})
            self._capture = "title"
        elif "result__snippet" in classes and self.results:
            self._capture = "snippet"

    def handle_endtag(self, tag):
        if tag == "a":
            self._capture = None

    def handle_data(self, data):
        if self._capture and self.results:
            self.results[-1][self._capture] += data


def clean_result_url(href: str) -> str:
    """DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<real url>."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        real = urllib.parse.parse_qs(parsed.query).get("uddg")
        if real:
            return real[0]
    return href


def parse_duckduckgo(html: str) -> list[dict]:
    parser = DuckDuckGoParser()
    parser.feed(html)
    results = []
    for item in parser.results:
        url = item["url"]
        # Skip ads (they go through duckduckgo.com/y.js) and empty links.
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue
        results.append({
            "title": re.sub(r"\s+", " ", item["title"]).strip(),
            "url": url,
            "snippet": re.sub(r"\s+", " ", item["snippet"]).strip(),
        })
    return results


def search_duckduckgo(query: str, max_results: int) -> list[dict]:
    html, _ = http_get(DDG_URL, data={"q": query, "kl": "wt-wt"})
    return parse_duckduckgo(html)[:max_results]


def search_wikipedia(query: str, max_results: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": max_results, "format": "json", "utf8": 1,
    })
    text, _ = http_get(f"{WIKIPEDIA_API}?{params}")
    try:
        hits = json.loads(text)["query"]["search"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ToolError("Wikipedia returned an unexpected response")
    return [{
        "title": hit["title"],
        "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(hit["title"].replace(" ", "_")),
        "snippet": strip_tags(hit.get("snippet", "")),
    } for hit in hits]


def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search the internet. Tries DuckDuckGo, then Wikipedia."""
    errors = []
    for label, search in (("DuckDuckGo", search_duckduckgo), ("Wikipedia", search_wikipedia)):
        try:
            results = search(query, max_results)
            if results:
                return results
        except ToolError as error:
            log.warning("%s search failed: %s", label, error)
            errors.append(f"{label}: {error}")
    if errors:
        raise ToolError("Web search failed: " + "; ".join(errors))
    return []


class TextExtractor(HTMLParser):
    """Turns an HTML page into readable plain text."""

    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
             "article", "pre", "blockquote", "table"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in joined.split("\n")]
        return "\n".join(line for line in lines if line)


def fetch_page(url: str, max_chars: int = 8000) -> tuple[str, str]:
    """Download a web page and return (title, readable text)."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ToolError("Only http:// and https:// addresses can be read")
    body, content_type = http_get(url)
    if "html" in content_type or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        extractor = TextExtractor()
        extractor.feed(body)
        title, text = extractor.title.strip(), extractor.text()
    elif content_type.startswith(("text/", "application/json")) or not content_type:
        title, text = url, body
    else:
        raise ToolError(f"{url} is not a text page ({content_type})")
    return title, text[:max_chars]


# --- tools --------------------------------------------------------------------

class WebSearch(Tool):
    name = "web_search"
    description = (
        "Search the internet for current information. Returns titles, links and snippets. "
        "Use fetch_webpage afterwards to read a result in full."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for"},
            "max_results": {"type": "integer", "description": "How many results (1-10, default 5)"},
        },
        "required": ["query"],
    }

    def run(self, query: str, max_results: int = 5) -> str:
        count = max(1, min(int(max_results or 5), 10))
        results = search_web(query, count)
        if not results:
            return f"No results found for: {query}"
        lines = [f"Search results for: {query}"]
        for number, item in enumerate(results, start=1):
            lines.append(f"{number}. {item['title']}\n   {item['url']}\n   {item['snippet']}")
        return "\n".join(lines)


class FetchWebpage(Tool):
    name = "fetch_webpage"
    description = "Read the text content of a web page (http/https URL)."
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The page address"}},
        "required": ["url"],
    }

    def run(self, url: str) -> str:
        title, text = fetch_page(url)
        return f"Title: {title}\nURL: {url}\n\n{text or '(no readable text found)'}"
