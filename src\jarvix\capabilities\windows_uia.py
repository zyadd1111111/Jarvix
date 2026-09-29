"""Windows accessibility boundary: isolated, cancellable .NET UI Automation calls.

Only a fixed script runs. Requests use stdin JSON, never interpolated executable
code. A fresh worker bounds hung third-party accessibility providers.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import subprocess
import time
from pathlib import Path

from jarvix.capabilities.native_windows import windows_only
from jarvix.runtime import check_cancelled


SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class JarvixInput {
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int left,top,right,bottom; }
 [StructLayout(LayoutKind.Sequential)] public struct Mouse { public int dx,dy; public uint data,flags,time; public UIntPtr extra; }
 [StructLayout(LayoutKind.Sequential)] public struct Key { public ushort code,scan; public uint flags,time; public UIntPtr extra; }
 [StructLayout(LayoutKind.Explicit)] public struct Body { [FieldOffset(0)] public Mouse mouse; [FieldOffset(0)] public Key key; }
 [StructLayout(LayoutKind.Sequential)] public struct Input { public uint type; public Body body; }
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window,out Rect rect);
 [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr window,int x,int y,int width,int height,bool repaint);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] static extern uint SendInput(uint count,Input[] input,int size);
 public static void Keys(int[] codes) {
  Input[] events = new Input[codes.Length*2];
  for(int i=0;i<codes.Length;i++) { events[i].type=1; events[i].body.key.code=(ushort)codes[i];
   int j=events.Length-1-i; events[j].type=1; events[j].body.key.code=(ushort)codes[i]; events[j].body.key.flags=2; }
  if(SendInput((uint)events.Length,events,Marshal.SizeOf(typeof(Input)))!=events.Length) throw new Exception("Input blocked");
 }
 public static void Click(int x,int y,int count,bool right) {
  if(!SetCursorPos(x,y)) throw new Exception("Pointer blocked");
  for(int i=0;i<count;i++) {
   Input[] events=new Input[2]; events[0].body.mouse.flags=right?8u:2u; events[1].body.mouse.flags=right?16u:4u;
   if(SendInput(2,events,Marshal.SizeOf(typeof(Input)))!=2) throw new Exception("Input blocked");
  }
 }
}
'@
$r = [Console]::In.ReadToEnd() | ConvertFrom-Json
function Rid($e) { return (($e.GetRuntimeId() | ForEach-Object { [string]$_ }) -join '.') }
function Bounded($text) { if ($null -eq $text) { return '' }; return ([string]$text).Substring(0,[Math]::Min(256,([string]$text).Length)) }
function Protected($e) {
 $c=$e.Current
 return $c.IsPassword -or (($c.Name+' '+$c.AutomationId) -match '(?i)password|passcode|credential|one.time.code|verification.code|security.code|api.key|access.token|secret.key|captcha')
}
function Row($e,$depth) {
 $c=$e.Current; $rect=$c.BoundingRectangle; $secret=Protected $e
 $patterns=@($e.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName.Replace('PatternIdentifiers.Pattern','').Replace('Pattern.Pattern','') })
 return @{ runtime_id=(Rid $e); depth=$depth; name=$(if($secret){'[protected]'}else{Bounded $c.Name});
  automation_id=$(if($secret){''}else{Bounded $c.AutomationId}); control_type=$c.ControlType.ProgrammaticName.Replace('ControlType.','');
  enabled=$c.IsEnabled; offscreen=$c.IsOffscreen; password=$secret; focused=$c.HasKeyboardFocus;
  bounds=@{x=$rect.X;y=$rect.Y;width=$rect.Width;height=$rect.Height}; patterns=$patterns }
}
function Nodes($root,$limit,$maxDepth) {
 $queue=[Collections.Generic.Queue[object]]::new(); $queue.Enqueue(@($root,0)); $rows=[Collections.Generic.List[object]]::new()
 $walker=[Windows.Automation.TreeWalker]::ControlViewWalker; $incomplete=$false
 while($queue.Count -gt 0 -and $rows.Count -lt $limit) {
  $pair=$queue.Dequeue(); $e=$pair[0]; $depth=[int]$pair[1]
  try {
   $rows.Add(@{element=$e;row=(Row $e $depth)})
   if(Protected $e) { $incomplete=$true }
   elseif($depth -lt $maxDepth) {
    $child=$walker.GetFirstChild($e); $siblings=0
    while($null -ne $child -and $siblings -lt $limit -and $queue.Count -lt $limit) {
     $queue.Enqueue(@($child,($depth+1))); $child=$walker.GetNextSibling($child); $siblings++
    }
    if($null -ne $child) {$incomplete=$true}
   } elseif($null -ne $walker.GetFirstChild($e)) {
    $incomplete=$true
   }
  } catch [Windows.Automation.ElementNotAvailableException] {$incomplete=$true}
 }
 return @{items=$rows;bounded=($incomplete -or $queue.Count -gt 0)}
}
function SameWindow($root,$handle,$pidValue,$started) {
 $now=[Windows.Automation.AutomationElement]::FromHandle($handle)
 if($null -eq $now -or $now.Current.ProcessId -ne $pidValue -or (Rid $now) -ne (Rid $root) -or
  [string](Get-Process -Id $pidValue).StartTime.ToUniversalTime().Ticks -ne $started) {throw 'Window changed'}
}
function InWindow($element,$root) {
 $walker=[Windows.Automation.TreeWalker]::RawViewWalker
 for($i=0;$i -lt 40 -and $null -ne $element;$i++) {
  if((Rid $element) -eq (Rid $root)){return $true}; $element=$walker.GetParent($element)
 }
 return $false
}
try {
 $handle=[IntPtr][long]$r.handle
 $root=[Windows.Automation.AutomationElement]::FromHandle($handle)
 if($null -eq $root -or $root.Current.ProcessId -ne [int]$r.process_id) { throw 'Stale window' }
 $process=Get-Process -Id ([int]$r.process_id)
 $started=[string]$process.StartTime.ToUniversalTime().Ticks
 if($r.process_started -and $started -ne [string]$r.process_started) { throw 'Replaced process' }
 if($r.root_id -and (Rid $root) -ne [string]$r.root_id) { throw 'Replaced window' }
 $limit=240; if($r.limit) { $limit=[Math]::Min(300,[int]$r.limit) }
 $depth=10; if($r.depth) { $depth=[Math]::Min(16,[int]$r.depth) }
 $tree=Nodes $root $limit $depth; $nodes=$tree.items
 if($r.operation -eq 'inspect') {
  $out=@{root_id=(Rid $root);process_started=$started;application=$process.ProcessName;
   elements=@($nodes | ForEach-Object {$_.row});bounded=$tree.bounded}
 } else {
  if($process.ProcessName -match '^(consent|CredentialUIBroker|LogonUI|lsass|SecurityHealthHost)$' -or
   $root.Current.Name -match '(?i)windows security|user account control|security warning|credential|password|sign.in|log.in|authenticate') { throw 'Protected application' }
  $node=$null
  if($r.runtime_id) {
   $matches=@($nodes | Where-Object { $_.row.runtime_id -eq [string]$r.runtime_id })
   if($matches.Count -ne 1) { throw 'Target unavailable' }; $node=$matches[0]; $e=$node.element
   if((Protected $e) -or -not $e.Current.IsEnabled -or $e.Current.IsOffscreen) { throw 'Protected or unavailable control' }
   if($e.Current.Name -ne [string]$r.expected_name -or $e.Current.ControlType.ProgrammaticName.Replace('ControlType.','') -ne [string]$r.expected_type -or $e.Current.AutomationId -ne [string]$r.expected_id) { throw 'Control changed' }
   if($e.Current.Name -match '(?i)^\s*(yes|ok|okay|confirm|allow|approve|authorize|accept|grant|trust|continue|run anyway)\b|bypass|disable.security|turn.off.protection') { throw 'Confirmation requires direct user interaction' }
  }
  SameWindow $root $handle ([int]$r.process_id) $started
  if($r.operation -eq 'bounds') {
   $rect=[JarvixInput+Rect]::new(); if(-not [JarvixInput]::GetWindowRect($handle,[ref]$rect)){throw 'Bounds unavailable'}
   $out=@{x=$rect.left;y=$rect.top;width=($rect.right-$rect.left);height=($rect.bottom-$rect.top)}
  } elseif($r.operation -eq 'move') {
   if(-not [JarvixInput]::MoveWindow($handle,[int]$r.x,[int]$r.y,[int]$r.width,[int]$r.height,$true)) {throw 'Move rejected'}
   $rect=[JarvixInput+Rect]::new(); if(-not [JarvixInput]::GetWindowRect($handle,[ref]$rect)){throw 'Bounds unavailable'}
   $out=@{requested=$true;verified=($rect.left -eq $r.x -and $rect.top -eq $r.y -and ($rect.right-$rect.left) -eq $r.width -and ($rect.bottom-$rect.top) -eq $r.height);
    bounds=@{x=$rect.left;y=$rect.top;width=($rect.right-$rect.left);height=($rect.bottom-$rect.top)}}
  } elseif($r.operation -eq 'focus_window') {
   if(-not [JarvixInput]::SetForegroundWindow($handle)){throw 'Focus rejected'}
   $out=@{requested=$true;verified=([JarvixInput]::GetForegroundWindow() -eq $handle)}
  } elseif($r.operation -eq 'shortcut') {
   if([JarvixInput]::GetForegroundWindow() -ne $handle) {throw 'Window is not active'}
   $focused=[Windows.Automation.AutomationElement]::FocusedElement
   if($null -eq $focused -or (Protected $focused) -or -not (InWindow $focused $root)) {throw 'Protected or changed focus'}
   if([JarvixInput]::GetForegroundWindow() -ne $handle){throw 'Focus changed'}
   [JarvixInput]::Keys([int[]]$r.codes); $out=@{requested=$true;verified=$false;verification='Inspect the expected resulting control state.'}
  } elseif($null -eq $node) { throw 'Target required'
  } elseif($r.operation -eq 'focus') {
   $e.SetFocus(); $out=@{requested=$true;verified=$e.Current.HasKeyboardFocus}
  } elseif($r.operation -eq 'type') {
   if($e.Current.ControlType -ne [Windows.Automation.ControlType]::Edit -and $e.Current.ControlType -ne [Windows.Automation.ControlType]::Document) {throw 'Not a text field'}
   $pattern=$e.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
   if($pattern.Current.IsReadOnly){throw 'Read only'}
   $pattern.SetValue([string]$r.text); $out=@{requested=$true;verified=($pattern.Current.Value -ceq [string]$r.text);characters=([string]$r.text).Length}
  } elseif($r.operation -eq 'scroll') {
   $pattern=$e.GetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern)
   $direction=[Windows.Automation.ScrollAmount]::SmallIncrement
   if($r.direction -in @('up','left')) {$direction=[Windows.Automation.ScrollAmount]::SmallDecrement}
   $old=@($pattern.Current.HorizontalScrollPercent,$pattern.Current.VerticalScrollPercent)
   for($i=0;$i -lt [int]$r.steps;$i++) {
    if($r.direction -in @('up','down')) {$pattern.Scroll([Windows.Automation.ScrollAmount]::NoAmount,$direction)}
    else {$pattern.Scroll($direction,[Windows.Automation.ScrollAmount]::NoAmount)}
   }
   $out=@{requested=$true;verified=($old[0] -ne $pattern.Current.HorizontalScrollPercent -or $old[1] -ne $pattern.Current.VerticalScrollPercent)}
  } elseif($r.operation -eq 'click') {
   $pattern=$null; $verified=$false; $method=''
   if($r.button -eq 'left' -and [int]$r.count -eq 1 -and $e.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) {
    $pattern.Invoke(); $method='InvokePattern'
   } elseif($r.button -eq 'left' -and [int]$r.count -eq 1 -and $e.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)) {
    $pattern.Select(); $method='SelectionItemPattern'; $verified=$pattern.Current.IsSelected
   } elseif($r.pointer_fallback) {
    if([JarvixInput]::GetForegroundWindow() -ne $handle) {throw 'Window is not active'}
    $point=$e.GetClickablePoint(); $hit=[Windows.Automation.AutomationElement]::FromPoint($point)
    if((Rid $hit) -ne (Rid $e)){throw 'Control is covered by another element'}
    if([JarvixInput]::GetForegroundWindow() -ne $handle){throw 'Focus changed'}
    [JarvixInput]::Click([int]$point.X,[int]$point.Y,[int]$r.count,($r.button -eq 'right')); $method='explicit_target_pointer'
   } else {throw 'No safe accessibility pattern. Explicit target pointer fallback is disabled.'}
   $out=@{requested=$true;verified=$verified;method=$method;verification=$(if($verified){'Selected state observed'}else{'Inspect the expected resulting control state.'})}
  } else {throw 'Unknown operation'}
 }
 @{ok=$true;data=$out} | ConvertTo-Json -Depth 12 -Compress
} catch {
 @{ok=$false;error='The selected window/control is unavailable, protected, changed, or does not support this operation.'} | ConvertTo-Json -Compress
 exit 1
}
'''


class WindowsUIAutomation:
    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint or check_cancelled

    def call(self, operation, *, timeout=12, **arguments):
        return run_native_script(SCRIPT, {"operation": operation, **arguments}, self.checkpoint, timeout)


def run_native_script(script, arguments, checkpoint=check_cancelled, timeout=12,
                      max_input=48000, max_output=500000):
        """Run a trusted fixed script, with untrusted request data confined to stdin JSON."""
        windows_only()
        checkpoint()
        # Compressed trusted code stays below CreateProcess's command-line limit.
        compressed = base64.b64encode(gzip.compress(script.encode("utf-8"))).decode("ascii")
        bootstrap = ("$b=[Convert]::FromBase64String('" + compressed + "');"
                     "$m=[IO.MemoryStream]::new([byte[]]$b);"
                     "$g=[IO.Compression.GZipStream]::new($m,[IO.Compression.CompressionMode]::Decompress);"
                     "$s=[IO.StreamReader]::new($g);& ([scriptblock]::Create($s.ReadToEnd()))")
        encoded = base64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        payload = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
        if len(payload) > max_input:
            raise ValueError("Accessibility request exceeds the input limit.")
        process = subprocess.Popen(
            [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + min(max(float(timeout), 0.1), 30)
        initial = True
        try:
            while True:
                checkpoint()
                if time.monotonic() >= deadline:
                    raise TimeoutError("The application's accessibility provider did not respond in time.")
                try:
                    output, _ = process.communicate(input=payload if initial else None, timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    initial = False
            checkpoint()
            if len(output) > max_output:
                raise ValueError("Accessibility result exceeds the output limit.")
            try:
                result = json.loads(output.decode("utf-8-sig"))
            except (ValueError, UnicodeError) as exc:
                raise RuntimeError("The Windows accessibility/image worker could not return a valid result.") from exc
            if process.returncode or not result.get("ok") or not isinstance(result.get("data"), dict):
                raise ValueError("The control changed, is protected, or cannot perform the requested action.")
            return result["data"]
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
