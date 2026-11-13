"""Download the screenshots attached to a merge request for the review's blind UX read.

prepare.py calls `download` outside the confinement, where the forge read token exists. Only
images the forge itself hosts are fetched:

- GitLab: an upload link (`/uploads/<32-hex secret>/<file>`, relative or on the forge's own
  host) is read through the API, `GET /projects/:id/uploads/:secret/:filename` (GitLab 17.4+),
  with the read token.
- GitHub: `https://<forge host>/user-attachments/assets/<id>` with the token, and on
  github.com also `https://*.githubusercontent.com/...` (user-images, private-user-images)
  without it.

Every other link is skipped. Redirects are followed by hand, at most three, https only, and the
token is never sent after a redirect: GitHub sends attachments to signed storage URLs, and an
automatic redirect would carry the token there. Each image is capped at MAX_BYTES and must be a
PNG, JPEG, GIF or WebP by its first bytes; it is saved as `shot-NN.<ext>`, so its name carries
no hint for the blind reader. At most MAX_IMAGES.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request

MAX_IMAGES = 10
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
# Bounded like fcore.IMAGE: the description is text any author writes, and an unbounded `[^x]*`
# is walked again from every `![` or `<img` in it.
LINK = re.compile(r"!\[[^\]]{0,500}\]\(\s{0,100}<?([^)\s>]+)"
                  r"|<img\s[^>]{0,500}?\bsrc\s{0,100}=\s{0,100}[\"']([^\"']{1,2000})[\"']", re.I)
GL_UPLOAD = re.compile(r"/uploads/([0-9a-f]{32})/([^/?#]+)\Z")
MAGIC = [(b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF87a", "gif"), (b"GIF89a", "gif")]


def links(texts: list[str]) -> list[str]:
    """Image links in markdown or <img> tags, in order, each once."""
    out: list[str] = []
    for t in texts:
        for a, b in LINK.findall(t or ""):
            url = (a or b).strip()
            if url and url not in out:
                out.append(url)
    return out


def web_host(forge) -> str:
    """The forge's web host, from its API base (https://g.example/api/v4, api.github.com, ...)."""
    u = urllib.parse.urlsplit(forge.http.api)
    host = u.netloc.lower()
    if forge.kind == "github":
        if host == "api.github.com":
            return "github.com"
        if host.startswith("api.") and host.endswith(".ghe.com"):
            return host[len("api."):]
    return host


def plan(forge, url: str) -> tuple[str, bool] | None:
    """(URL to fetch, send the token) for a link the forge hosts; None for any other link."""
    host = web_host(forge)
    u = urllib.parse.urlsplit(url)
    if forge.kind == "gitlab":
        if u.netloc and (u.scheme != "https" or u.netloc.lower() != host):
            return None
        if not u.netloc and not url.startswith("/"):
            return None
        m = GL_UPLOAD.search(u.path)
        if not m:
            return None
        secret, name = m.group(1), urllib.parse.unquote(m.group(2))
        return (f"{forge.http.api}{forge.p}/uploads/{secret}/{urllib.parse.quote(name, safe='')}", True)
    if u.scheme != "https" or not u.netloc:
        return None
    h = u.netloc.lower()
    if h == host and u.path.startswith("/user-attachments/assets/"):
        return url, True
    if host == "github.com" and h.endswith(".githubusercontent.com"):
        return url, False
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url: str, headers: dict) -> tuple[int, str, bytes]:
    """(status, Location header, body up to MAX_BYTES + 1). Never follows a redirect."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.headers.get("Location") or "", r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location") or "", b""


def kind_of(data: bytes) -> str | None:
    for magic, ext in MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _auth(forge) -> dict:
    keep = ("private-token", "authorization")
    return {k: v for k, v in forge.http.headers.items() if k.lower() in keep}


def download(forge, texts: list[str], out_dir: str, log: list, fetch=fetch) -> tuple[list[str], int]:
    """([saved file names], links that the forge hosts but could not be saved)."""
    saved: list[str] = []
    failed = 0
    skipped = 0
    for url in links(texts):
        p = plan(forge, url)
        if p is None:
            skipped += 1
            continue
        if len(saved) >= MAX_IMAGES:
            log.append(f"screenshots: more than {MAX_IMAGES} images, the rest not downloaded")
            break
        target, with_token = p
        headers = _auth(forge) if with_token else {}
        data, why = None, ""
        for _ in range(MAX_REDIRECTS + 1):
            try:
                status, location, body = fetch(target, headers)
            except (OSError, ValueError) as e:
                why = type(e).__name__
                break
            if status in (301, 302, 303, 307, 308) and location:
                nxt = urllib.parse.urljoin(target, location)
                if urllib.parse.urlsplit(nxt).scheme != "https":
                    why = "redirect to a non-https URL"
                    break
                target, headers = nxt, {}  # never carry the token past a redirect
                continue
            if status != 200:
                why = f"HTTP {status}"
            elif len(body) > MAX_BYTES:
                why = f"larger than {MAX_BYTES // (1024 * 1024)} MB"
            else:
                data = body
            break
        else:
            why = "too many redirects"
        ext = kind_of(data) if data is not None else None
        if data is not None and ext is None:
            why = "not a PNG, JPEG, GIF or WebP image"
        if ext is None:
            failed += 1
            log.append(f"screenshots: link {len(saved) + failed + skipped} NOT saved ({why})")
            continue
        os.makedirs(out_dir, exist_ok=True)
        name = f"shot-{len(saved) + 1:02d}.{ext}"
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(data)
        saved.append(name)
    log.append(f"screenshots: {len(saved)} saved, {failed} not saved, {skipped} link(s) not on the forge")
    return saved, failed
