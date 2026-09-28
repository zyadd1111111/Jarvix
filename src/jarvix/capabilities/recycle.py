"""Receipt-bound Windows Recycle Bin restoration, without permanent-delete fallback.

Shell item metadata: https://devblogs.microsoft.com/oldnewthing/20140421-00/?p=1183
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from jarvix.capabilities.schema import ID, array, integer, register
from jarvix.runtime import check_cancelled

SHELL_SCRIPT = r"""
$ErrorActionPreference='Stop'; [Console]::InputEncoding=[Text.Encoding]::UTF8;
[Console]::OutputEncoding=[Text.Encoding]::UTF8;
$c=([Console]::In.ReadToEnd() | ConvertFrom-Json);
$shell=New-Object -ComObject Shell.Application; $bin=$shell.NameSpace(10);
if ($c.action -eq 'list') {
 $rows=@(); foreach($item in $bin.Items()) {
  $name=[string]$item.ExtendedProperty('System.FileName');
  $from=[string]$item.ExtendedProperty('System.Recycle.DeletedFrom');
  if ($name -and $from) {
   $rows+=@{identity=[string]$item.Path; original=(Join-Path $from $name)}
  }; if ($rows.Count -ge 5000) {break}
 }; ConvertTo-Json -InputObject $rows -Compress
} elseif ($c.action -eq 'restore') {
 $found=$null; foreach($item in $bin.Items()) {
  if ([string]$item.Path -ceq [string]$c.identity) { $found=$item; break }
 }
 if ($null -eq $found) {throw 'Item unavailable'}
 $original=Join-Path ([string]$found.ExtendedProperty('System.Recycle.DeletedFrom')) ([string]$found.ExtendedProperty('System.FileName'));
 if ($original -ine [string]$c.original -or (Test-Path -LiteralPath $original)) {throw 'Restore conflict'}
 $found.InvokeVerb('undelete'); @{requested=$true} | ConvertTo-Json -Compress
} else {throw 'Unknown action'}
"""


class WindowsRecycleBin:
    def _call(self, **args):
        if sys.platform != "win32":
            raise RuntimeError("Recycle restoration requires Windows.")
        check_cancelled()
        result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", SHELL_SCRIPT],
            input=json.dumps(args).encode(), capture_output=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or len(result.stdout) > 4_000_000:
            raise RuntimeError("Windows Recycle Bin is unavailable or the item changed.")
        return json.loads(result.stdout.decode("utf-8-sig"))

    def items(self):
        return self._call(action="list")

    def restore(self, item):
        return self._call(action="restore", **item)


class RecycleService:
    def __init__(self, services, backend=None):
        self.s = services
        self.backend = backend or WindowsRecycleBin()

    def before(self, path):
        try:
            return {item["identity"] for item in self.backend.items()}
        except Exception:
            return None  # Recycling still works; do not promise automatic restore.

    def record(self, path, digest, before):
        receipt = {"original": str(path), "fingerprint": digest, "identity": None, "restored": False}
        if before is not None:
            try:
                for _ in range(10):
                    items = [item for item in self.backend.items() if item["identity"] not in before
                             and Path(item["original"]) == path]
                    if len(items) == 1:
                        receipt["identity"] = items[0]["identity"]
                        break
                    if len(items) > 1:
                        break
                    time.sleep(.05)
            except Exception:
                pass
        id = self.s.records.put("recycled_item", receipt)
        return {"recycle_id": id, "restore_available": bool(receipt["identity"]),
                "restore": "Use files.restore_preview before restoring." if receipt["identity"] else
                           "Windows did not provide a unique receipt. Inspect Windows Recycle Bin manually."}

    def list(self, limit=50):
        try:
            items = {item["identity"]: item for item in self.backend.items()}
        except Exception:
            items = {}
        result = []
        for row in self.s.records.list("recycled_item")[:limit]:
            available = bool(row.get("identity") in items and not row.get("restored"))
            result.append({"id": row["id"], "original_path": row["original"], "recycled_at": row["created_at"],
                           "available": available, "restored": row["restored"]})
        return {"items": result}

    def preview(self, ids):
        if not 1 <= len(ids) <= 50 or len(set(ids)) != len(ids):
            raise ValueError("Select 1–50 different recycle receipts.")
        items = {item["identity"]: item for item in self.backend.items()}
        previews = []
        for id in ids:
            row = self.s.records.get("recycled_item", id)
            target = self.s.files.path(row["original"], existing=False, mutate=True)
            self.s.files.path(target.parent)
            item = items.get(row.get("identity"))
            available = bool(item and Path(item["original"]) == target and not row["restored"])
            previews.append({"id": id, "original_path": str(target), "available": available,
                             "conflict": target.exists(), "can_restore": available and not target.exists()})
        return {"items": previews, "can_restore": all(row["can_restore"] for row in previews),
                "conflict_policy": "Never overwrite. Move the existing file before restoring."}

    def restore(self, ids):
        preview = self.preview(ids)
        if not preview["can_restore"]:
            raise ValueError("A recycled item is missing, already restored, or has a destination conflict.")
        results = []
        for id in ids:
            check_cancelled()
            if not self.preview([id])["can_restore"]:
                return {"completed": False, "results": results, "error": "Item changed before restore"}
            row = self.s.records.get("recycled_item", id)
            self.backend.restore({"identity": row["identity"], "original": row["original"]})
            target = Path(row["original"])
            for _ in range(50):
                check_cancelled()
                if target.exists():
                    break
                time.sleep(.1)
            verified = target.exists() and self.s.files._fingerprint(target) == row["fingerprint"]
            if verified:
                row["restored"] = True
                self.s.records.put("recycled_item", row, id)
            self.s.repository.audit("file_action", "Recycle restore " + ("verified" if verified else "needs inspection"))
            results.append({"id": id, "restored": verified, "path": row["original"]})
            if not verified:
                return {"completed": False, "results": results, "error": "Restore not verified; inspect Recycle Bin and original path"}
        return {"completed": True, "results": results}


def setup(s, registry):
    service = s.recycle = RecycleService(s)
    register(registry, "files.recycled", "List recently Jarvix-recycled items; check whether Windows still has them.",
             {"limit": integer(1, 100)}, (), service.list)
    props = {"ids": {**array(ID, 50), "minItems": 1}}
    register(registry, "files.restore_preview", "Preview original paths, availability and conflicts for Jarvix recycle receipts.",
             props, ("ids",), service.preview)
    register(registry, "files.restore", "Restore selected Jarvix-recycled items to approved original paths; never overwrite; verify contents.",
             props, ("ids",), service.restore, 2, "computer.control")

