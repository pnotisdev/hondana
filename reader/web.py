"""HTTP requests to the online services the library looks titles up on."""

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Hondana/1.1 (local Japanese reading library)"
TIMEOUT = 12


class LookupFailed(Exception):
    """The service could not be reached or gave an answer that can't be used; nothing should be cached."""


def quote(value):
    return urllib.parse.quote(value, safe="")


def request(url, headers=None, data=None, timeout=TIMEOUT):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, body, {"User-Agent": USER_AGENT, "Accept": "application/json, text/html",
                                             **({"Content-Type": "application/json"} if body else {}),
                                             **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise LookupFailed(f"{urllib.parse.urlsplit(url).hostname} answered HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LookupFailed(f"Could not reach {urllib.parse.urlsplit(url).hostname}: {error}") from error


def get_json(url, headers=None, data=None):
    try:
        result = json.loads(request(url, headers, data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LookupFailed(f"{urllib.parse.urlsplit(url).hostname} did not answer with JSON") from error
    if not isinstance(result, (dict, list)):
        raise LookupFailed(f"{urllib.parse.urlsplit(url).hostname} gave an unexpected answer")
    return result


def get_text(url, headers=None):
    return request(url, headers).decode("utf-8", errors="replace")
