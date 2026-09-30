"""Opt-in Windows shell features, confined to Jarvix and approved file roots."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from jarvix.capabilities.native_windows import windows_only
from jarvix.capabilities.schema import BOOL, integer, register, string
from jarvix.capabilities.windows_uia import run_native_script
from jarvix.runtime import check_cancelled

SHELL_SCRIPT = r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$r=[Console]::In.ReadToEnd() | ConvertFrom-Json
try {
 if($r.operation -in @('explorer','navigate')) {
  $shell=New-Object -ComObject Shell.Application
  $window=@($shell.Windows() | Where-Object { [int64]$_.HWND -eq [int64]$r.handle })
  if($window.Count -ne 1){throw 'No unique Explorer window'}
  $doc=$window[0].Document
  if($r.operation -eq 'navigate') {
   $window[0].Navigate2([string]$r.path)
   $out=@{requested=$true;verified=$false;handle=[int64]$r.handle}
  } else {
  $selected=@($doc.SelectedItems() | Select-Object -First 100 | ForEach-Object { [string]$_.Path })
  $out=@{folder=[string]$doc.Folder.Self.Path;selected_files=$selected;handle=[int64]$r.handle}
  }
 } elseif($r.operation -eq 'search') {
  $scope=@($r.roots | ForEach-Object { "SCOPE='file:" + ([string]$_).Replace('\','/').Replace("'","''") + "'" }) -join ' OR '
  if(-not $scope){throw 'No approved scope'}
  $term=([string]$r.query).Replace("'","''")
  $sql='SELECT TOP ' + [int]$r.limit + ' System.ItemPathDisplay FROM SystemIndex WHERE (' + $scope + ") AND System.FileName LIKE '%" + $term + "%'"
  $connection=New-Object -ComObject ADODB.Connection
  $connection.ConnectionTimeout=3
  $connection.CommandTimeout=8
  $connection.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
  try {
   $rows=$connection.Execute($sql);$paths=@()
   while(-not $rows.EOF -and $paths.Count -lt [int]$r.limit) { $paths += [string]$rows.Fields.Item(0).Value; $rows.MoveNext() }
   $rows.Close();$out=@{paths=$paths}
  } finally { $connection.Close() }
 } elseif($r.operation -eq 'recent') {
  $shell=New-Object -ComObject WScript.Shell
  $root=$shell.SpecialFolders('Recent')
  $items=@(Get-ChildItem -LiteralPath $root -Filter '*.lnk' -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 200 | ForEach-Object {
   $target=$shell.CreateShortcut($_.FullName).TargetPath
   if($target){@{path=[string]$target;used_at=$_.LastWriteTimeUtc.ToString('o')}}
  })
  $out=@{items=$items}
 } else {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("A5CD92FF-29BE-454C-8D04-D82879FB3F1B"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IJarvixDesktopManager {
 [PreserveSig] int IsWindowOnCurrentVirtualDesktop(IntPtr window,[MarshalAs(UnmanagedType.Bool)] out bool current);
 [PreserveSig] int GetWindowDesktopId(IntPtr window,out Guid desktop);
 [PreserveSig] int MoveWindowToDesktop(IntPtr window,ref Guid desktop);
}
public static class JarvixShell {
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct OpenAs { public string file; public string type; public uint flags; }
 [DllImport("shell32.dll",CharSet=CharSet.Unicode)] public static extern int SHOpenWithDialog(IntPtr owner,ref OpenAs info);
 [DllImport("shell32.dll",CharSet=CharSet.Unicode)] public static extern void SHAddToRecentDocs(uint flag,string path);
}
'@
  if($r.operation -eq 'desktop') {
   $manager=[Activator]::CreateInstance([Type]::GetTypeFromCLSID([Guid]'AA509086-5CA9-4C25-8F95-589D3C07B48A'))
   try {
    $api=[IJarvixDesktopManager]$manager;$id=[Guid]::Empty;$current=$false
    [Runtime.InteropServices.Marshal]::ThrowExceptionForHR($api.GetWindowDesktopId([IntPtr][int64]$r.handle,[ref]$id))
    [Runtime.InteropServices.Marshal]::ThrowExceptionForHR($api.IsWindowOnCurrentVirtualDesktop([IntPtr][int64]$r.handle,[ref]$current))
    $out=@{desktop_id=$id.ToString();on_active_desktop=$current;handle=[int64]$r.handle}
   } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($manager) }
  } elseif($r.operation -eq 'open_with') {
   $info=[JarvixShell+OpenAs]::new();$info.file=[string]$r.path;$info.flags=4
   $status=[JarvixShell]::SHOpenWithDialog([IntPtr]::Zero,[ref]$info)
   # A user cancelled dialog is a completed interaction, not a file-open claim.
   if($status -ne 0 -and $status -ne -2147023673){[Runtime.InteropServices.Marshal]::ThrowExceptionForHR($status)}
   $out=@{requested=$true;verified=$false;cancelled=($status -eq -2147023673)}
  } elseif($r.operation -eq 'add_recent') {
   [JarvixShell]::SHAddToRecentDocs(3,[string]$r.path)
   $out=@{requested=$true;verified=$false}
  } else {throw 'Unknown operation'}
 }
 @{ok=$true;data=$out} | ConvertTo-Json -Depth 8 -Compress
} catch {
 @{ok=$false;error='Windows shell operation failed or is unavailable.'} | ConvertTo-Json -Compress
 exit 1
}
'''


class WindowsIntegrationService:
    def __init__(self, services, backend=run_native_script):
        self.s = services
        self.backend = backend

    def _call(self, operation, **arguments):
        check_cancelled()
        return self.backend(SHELL_SCRIPT, {"operation": operation, **arguments}, timeout=12)

    def explorer_snapshot(self, handle, process_id):
        self.s.desktop._screen_gate()
        self.s.desktop._window(handle, process_id)
        result = self._call("explorer", handle=handle)
        # COM may expose folders outside allowed roots. They never enter context.
        folder = None
        try:
            target = self.s.files.path(result.get("folder", ""))
            if target.is_dir():
                folder = str(target)
        except (ValueError, OSError):
            pass
        selected = []
        for value in result.get("selected_files", [])[:100]:
            try:
                selected.append(str(self.s.files.path(value)))
            except (ValueError, OSError):
                pass
        self.s.desktop._screen_gate()
        self.s.desktop._window(handle, process_id)
        return {"folder": folder, "selected_files": selected, "handle": handle, "process_id": process_id,
                "source": "Explorer Shell COM; approved folders only", "stored": False}

    def virtual_desktop(self, handle=None, process_id=None):
        if handle is None:
            active = self.s.desktop.active_window()
            if not active.get("handle"):
                raise ValueError("No foreground window is available.")
            handle, process_id = active["handle"], active["process_id"]
        self.s.desktop._window(handle, process_id)
        result = self._call("desktop", handle=handle)
        self.s.desktop._window(handle, process_id)
        return {**result, "process_id": process_id, "source": "Windows IVirtualDesktopManager"}

    def search(self, query, limit=30):
        if (not isinstance(query, str) or not query.strip() or len(query) > 200
                or any(character in query for character in "\0\r\n%_[]")
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("Use literal filename text without wildcard characters, and a limit between 1 and 100.")
        roots = []
        for value in self.s.file_roots()[:20]:
            try:
                roots.append(str(self.s.files.path(value)))
            except (ValueError, OSError):
                continue
        if not roots:
            return {"items": [], "source": "Windows Search", "reason": "No approved folders configured."}
        result = self._call("search", roots=roots, query=query.strip(), limit=limit)
        items = []
        for value in result.get("paths", [])[:limit]:
            try:
                item = self.s.files.path(value)
                if query.casefold().strip() in item.name.casefold():
                    items.append(self.s.files.metadata(item))
            except (OSError, ValueError):
                continue
        return {"items": items, "source": "Windows Search index; approved folders only",
                "index_coverage": "Only locations currently indexed by Windows Search"}

    def recent(self, limit=30):
        if not self.s.settings.get("windows.recent.enabled", False):
            raise PermissionError("Enable Windows recent-item access in Settings first.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100.")
        result = self._call("recent")
        items = []
        for row in result.get("items", [])[:200]:
            try:
                item = self.s.files.path(row["path"])
                items.append({**self.s.files.metadata(item), "used_at": row.get("used_at")})
                if len(items) >= limit:
                    break
            except (KeyError, OSError, ValueError):
                continue
        return {"items": items, "source": "Windows Recent shortcuts; approved folders only"}

    def add_recent(self, path):
        if not self.s.settings.get("windows.recent.enabled", False):
            raise PermissionError("Enable Windows recent-item access first.")
        target = self.s.files.path(path)
        if not target.is_file():
            raise ValueError("Select an approved regular file.")
        result = self._call("add_recent", path=str(target))
        return {**result, "path": str(target), "verification": "Windows receives the recent-item request; no content is opened."}

    def open_with(self, path):
        target = self.s.files.path(path)
        if not target.is_file() or target.suffix.casefold() not in {
            ".txt", ".md", ".pdf", ".json", ".csv", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}:
            raise ValueError("Open-with is limited to approved document/image files; executable associations are disabled.")
        result = self._call("open_with", path=str(target))
        return {**result, "path": str(target), "verification": "The user chooses an application in the Windows dialog."}

    def startup_preview(self):
        windows_only()
        if not getattr(sys, "frozen", False):
            raise ValueError("Windows startup registration requires the packaged Jarvix executable.")
        executable = Path(sys.executable).resolve(strict=True)
        if executable.name.casefold() != "jarvix.exe":
            raise ValueError("Startup can register only the packaged Jarvix.exe.")
        command = subprocess.list2cmdline([str(executable), "--data-dir", str(self.s.data_dir.resolve())])
        return {"command": command, "scope": "Current Windows user", "value_name": "Jarvix",
                "elevated": False, "trigger": "Windows sign-in"}

    def startup(self):
        windows_only()
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                command, kind = winreg.QueryValueEx(key, "Jarvix")
        except FileNotFoundError:
            return {"enabled": False, "scope": "Current Windows user"}
        # Avoid disclosure of a registry value modified by another program.
        try:
            expected = self.startup_preview()["command"]
        except ValueError:
            expected = None
        return {"enabled": kind == winreg.REG_SZ and command == expected, "needs_attention": command != expected,
                "scope": "Current Windows user", "value_name": "Jarvix"}

    def set_startup(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Choose whether to enable Jarvix startup.")
        preview = self.startup_preview()
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                               0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            try:
                previous, _kind = winreg.QueryValueEx(key, "Jarvix")
            except FileNotFoundError:
                previous = None
            if previous is not None and previous != preview["command"]:
                raise PermissionError("An unrelated or changed Jarvix startup entry exists. Manage it in Windows Settings.")
            check_cancelled()
            if enabled:
                winreg.SetValueEx(key, "Jarvix", 0, winreg.REG_SZ, preview["command"])
            elif previous is not None:
                winreg.DeleteValue(key, "Jarvix")
        self.s.repository.audit("settings", "Jarvix Windows sign-in startup " + ("enabled" if enabled else "disabled"))
        return {**self.startup(), "requested": enabled}


def setup(s, registry):
    s.windows_integration = service = WindowsIntegrationService(s)
    register(registry, "windows.search", "Search the native Windows file index by literal filename inside approved roots.",
             {"query": string(200), "limit": integer(1, 100)}, ["query"], service.search)
    register(registry, "windows.virtual_desktop", "Inspect the virtual desktop of a known window, or the foreground window.",
             {"handle": integer(1, 2**53), "process_id": integer(1, 2**31)}, [], service.virtual_desktop)
    register(registry, "windows.recent", "Read opt-in Windows recent files, filtered to approved roots.",
             {"limit": integer(1, 100)}, [], service.recent)
    register(registry, "windows.add_recent", "Explicitly add one approved file to Windows Recent without opening it.",
             {"path": string()}, ["path"], service.add_recent, 2, "computer.control")
    register(registry, "windows.open_with", "Show the Windows application chooser for an approved document/image; user chooses the app.",
             {"path": string()}, ["path"], service.open_with, 2, "computer.control")
    register(registry, "windows.startup", "Inspect only Jarvix's current-user sign-in startup registration.", {}, [], service.startup)
    register(registry, "windows.startup_preview", "Preview the packaged Jarvix-only sign-in startup command without modifying Windows.",
             {}, [], service.startup_preview)
    register(registry, "windows.set_startup", "Enable/remove only the unchanged packaged Jarvix current-user startup entry. Fresh confirmation.",
             {"enabled": BOOL}, ["enabled"], service.set_startup, 3, "system.startup")
