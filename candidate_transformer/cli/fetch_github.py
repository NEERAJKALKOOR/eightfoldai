"""Fetch a public GitHub profile into a local payload for the GitHub adapter.

This is an **opt-in, network-using** helper kept separate from the deterministic
engine. It calls GitHub's public REST API (no auth required, rate-limited) and
writes a JSON payload in exactly the shape :class:`GithubAdapter` expects, so the
rest of the pipeline stays offline and deterministic: you fetch once, save the
JSON, then run the engine on that file as many times as you like with identical
results.

Usage::

    python -m candidate_transformer.cli.fetch_github <username-or-url> [--out PATH]

Then feed the saved payload into the pipeline::

    python -m candidate_transformer.cli.main \
        --input "github=<PATH>" --config samples/configs/default.json

Failures (network error, 404, rate limit) are reported clearly and return a
non-zero exit code; nothing here can crash the engine because it runs separately.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Sequence

_API = "https://api.github.com"
# GitHub requires a User-Agent header on API requests.
_HEADERS = {"User-Agent": "candidate-transformer-fetch/1.0", "Accept": "application/vnd.github+json"}
_TIMEOUT = 15


def _username_from(ref: str) -> str:
    """Extract the bare username from a raw username or a github.com URL."""
    ref = ref.strip().rstrip("/")
    if "github.com/" in ref.lower():
        ref = ref.split("github.com/", 1)[1]
    # Drop any trailing path (e.g. /repos) and a leading '@'.
    return ref.split("/", 1)[0].lstrip("@")


def _get_json(url: str) -> Any:
    """GET ``url`` and parse JSON, raising urllib errors on failure."""
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_profile(username: str) -> dict[str, Any]:
    """Fetch a public GitHub profile + repo languages into an adapter payload.

    Returns a dict with the keys :class:`GithubAdapter` reads: ``login``, ``name``,
    ``bio``, ``location``, ``email``, ``html_url``, and a deduplicated ``languages``
    list derived from the user's public repositories.
    """
    user = _get_json(f"{_API}/users/{username}")
    repos = _get_json(f"{_API}/users/{username}/repos?per_page=100&sort=updated")

    languages: list[str] = []
    seen: set[str] = set()
    if isinstance(repos, list):
        for repo in repos:
            lang = repo.get("language") if isinstance(repo, dict) else None
            if isinstance(lang, str) and lang and lang.lower() not in seen:
                seen.add(lang.lower())
                languages.append(lang)

    return {
        "login": user.get("login"),
        "name": user.get("name"),
        "bio": user.get("bio"),
        "location": user.get("location"),
        "email": user.get("email"),
        "html_url": user.get("html_url"),
        "languages": languages,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: fetch a profile and write/print the adapter payload JSON."""
    parser = argparse.ArgumentParser(
        prog="fetch_github",
        description="Fetch a public GitHub profile into a local payload for the pipeline.",
    )
    parser.add_argument("profile", metavar="USERNAME_OR_URL", help="GitHub username or profile URL.")
    parser.add_argument("--out", metavar="PATH", default=None, help="Write JSON here (else stdout).")
    args = parser.parse_args(argv)

    username = _username_from(args.profile)
    if not username:
        print("error: could not determine a GitHub username", file=sys.stderr)
        return 2

    try:
        payload = fetch_profile(username)
    except urllib.error.HTTPError as exc:
        detail = "rate limit exceeded" if exc.code == 403 else f"HTTP {exc.code}"
        print(f"error: GitHub API request failed ({detail}) for {username!r}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"error: could not fetch GitHub profile {username!r}: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out is not None:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"wrote GitHub payload for {username!r} to {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
