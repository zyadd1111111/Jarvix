"""Default-browser navigation and explicit local bookmarks/history; no browser scraping."""
from __future__ import annotations

import webbrowser
from urllib.parse import quote

from jarvix.capabilities.schema import BOOL, ID, array, register, schema, string
from jarvix.runtime import check_cancelled
from jarvix.services import required_text
from jarvix.tools.builtin import _valid_web_url


def public_url(url):
    if not isinstance(url, str) or len(url) > 4096 or not _valid_web_url(url):
        raise ValueError("Use a public HTTP(S) URL without credentials or control characters.")
    return url


class BrowserService:
    def __init__(self, services):
        self.s = services

    def open_url(self, url):
        public_url(url)
        check_cancelled()
        if not webbrowser.open(url, new=2):
            raise RuntimeError("No browser could open the link.")
        self.s.repository.audit("browser", "Public website opened")
        return {"opened": True}

    def search(self, query):
        return self.open_url("https://duckduckgo.com/?q=" + quote(required_text(query, "Search", 500), safe=""))

    def save_bookmark(self, title, url, id=None, favorite=False):
        if id:
            self.s.records.get("bookmark", id)
        value = {"title": required_text(title, "Bookmark title", 200), "url": public_url(url), "favorite": favorite}
        return {"id": self.s.records.put("bookmark", value, id)}

    def bookmarks(self, query="", favorites_only=False):
        needle = query.casefold()
        items = [item for item in self.s.records.list("bookmark")
                 if needle in (item["title"] + " " + item["url"]).casefold()
                 and (not favorites_only or item.get("favorite"))]
        return {"items": items[:100], "truncated": len(items) > 100}

    def delete_bookmark(self, id):
        self.s.records.get("bookmark", id)
        self.s.records.delete("bookmark", id)
        return {"deleted": True}

    def open_bookmark(self, id):
        bookmark = self.s.records.get("bookmark", id)
        return self.s.execute_tool("web.open", {"url": bookmark["url"]})

    def save_group(self, name, urls, id=None):
        if not 1 <= len(urls) <= 20:
            raise ValueError("A website group needs between 1 and 20 URLs.")
        if id:
            self.s.records.get("website.group", id)
        value = {"name": required_text(name, "Group name", 160), "urls": list(dict.fromkeys(public_url(url) for url in urls))}
        return {"id": self.s.records.put("website.group", value, id)}

    def groups(self, query=""):
        return {"items": [value for value in self.s.records.list("website.group") if query.casefold() in value["name"].casefold()][:100]}

    def delete_group(self, id):
        self.s.records.get("website.group", id)
        self.s.records.delete("website.group", id)
        return {"deleted": True}

    def open_group(self, id):
        group = self.s.records.get("website.group", id)
        results = []
        for url in group["urls"]:
            check_cancelled()
            result = self.s.execute_tool("web.open", {"url": url})
            results.append(result.as_dict())
            if not result.ok:
                return {"completed": False, "results": results}
        return {"completed": True, "results": results}

    def import_history(self, entries):
        if len(entries) > 100:
            raise ValueError("Import at most 100 explicitly selected entries at a time.")
        checked = []
        for entry in entries:
            checked.append({"url": public_url(entry["url"]), "title": required_text(entry["title"], "History title", 200),
                            "source": "explicit import", "visited_at": entry.get("visited_at", "")})
        for value in checked:
            self.s.records.put("browser.history", value)
        self.s.repository.audit("browser", "User-selected browser history imported locally")
        return {"imported": len(checked)}

    def history(self, query=""):
        values = self.s.records.list("browser.history")
        items = [value for value in values if query.casefold() in (value["title"] + " " + value["url"]).casefold()]
        return {"items": items[:100], "truncated": len(items) > 100, "source": "explicit imports only"}

    def clear_history(self):
        self.s.db.execute("DELETE FROM records WHERE kind='browser.history'")
        return {"cleared": True}


def setup(s, registry):
    service = s.browser = BrowserService(s)
    def add(name, desc, props, required, handler, level=1):
        register(registry, name, desc, props, required, handler, level, "browser.read" if level == 1 else "browser.write")
    add("browser.bookmark_save", "Create or update an explicit local website bookmark and favorite flag.",
        {"title": string(200), "url": string(), "id": ID, "favorite": BOOL}, ["title", "url"], service.save_bookmark, 2)
    add("browser.bookmarks", "Search local browser shortcuts; optionally list favorites only.",
        {"query": string(200, 0), "favorites_only": BOOL}, [], service.bookmarks)
    add("browser.bookmark_delete", "Remove a local bookmark after confirmation.", {"id": ID}, ["id"], service.delete_bookmark, 3)
    add("browser.bookmark_open", "Open a saved bookmark through the normal browser permission checks.", {"id": ID}, ["id"], service.open_bookmark, 2)
    add("browser.group_save", "Create or edit a named website group, for example School.",
        {"name": string(160), "urls": array(string(), 20), "id": ID}, ["name", "urls"], service.save_group, 2)
    add("browser.groups", "Search configured website groups.", {"query": string(200, 0)}, [], service.groups)
    add("browser.group_delete", "Delete a website group after confirmation.", {"id": ID}, ["id"], service.delete_group, 3)
    add("browser.group_open", "Open each configured group URL with individual permission checks. Stops on failure.", {"id": ID}, ["id"], service.open_group, 2)
    add("browser.history_import", "Import explicitly supplied browser history. Never reads or scrapes browser profiles.",
        {"entries": array(schema({"title": string(200), "url": string(), "visited_at": string(50, 0)}, ["title", "url"]), 100)}, ["entries"], service.import_history, 3)
    add("browser.history_search", "Search browser history explicitly imported into Jarvix.", {"query": string(200, 0)}, [], service.history)
    add("browser.history_clear", "Permanently clear Jarvix's imported browser history after confirmation.", {}, [], service.clear_history, 3)
