"""Isolated PrintWindow capture. Never falls back to pixels from the desktop."""
from __future__ import annotations

import base64
import struct

from jarvix.capabilities.windows_uia import run_native_script


SCRIPT = r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
public static class JarvixWindowCapture {
 [StructLayout(LayoutKind.Sequential)] public struct Rect {public int left,top,right,bottom;}
 [StructLayout(LayoutKind.Sequential)] public struct Header {
  public uint size; public int width,height; public ushort planes,bits;
  public uint compression,imageSize; public int xppm,yppm; public uint used,important;
 }
 [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
 [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
 [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window,out Rect rect);
 [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr window,IntPtr dc,uint flags);
 [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr window);
 [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr window,IntPtr dc);
 [DllImport("gdi32.dll")] static extern IntPtr CreateCompatibleDC(IntPtr dc);
 [DllImport("gdi32.dll")] static extern IntPtr CreateCompatibleBitmap(IntPtr dc,int width,int height);
 [DllImport("gdi32.dll")] static extern IntPtr SelectObject(IntPtr dc,IntPtr obj);
 [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr obj);
 [DllImport("gdi32.dll")] static extern bool DeleteDC(IntPtr dc);
 [DllImport("gdi32.dll")] static extern int GetDIBits(IntPtr dc,IntPtr bitmap,uint first,uint lines,
  byte[] pixels,ref Header header,uint usage);
 static void Check(IntPtr window,uint expected,int x,int y,int width,int height) {
  uint pid; Rect rect; GetWindowThreadProcessId(window,out pid);
  if(pid!=expected || !IsWindowVisible(window) || IsIconic(window) || !GetWindowRect(window,out rect) ||
    rect.left!=x || rect.top!=y || rect.right-rect.left!=width || rect.bottom-rect.top!=height)
   throw new Exception("Window changed or is unavailable");
 }
 public static string Capture(long hwnd,uint pid,int x,int y,int width,int height) {
  if(width<=0 || height<=0 || (long)width*height>40000000) throw new Exception("Capture too large");
  IntPtr window=new IntPtr(hwnd),screen=IntPtr.Zero,memory=IntPtr.Zero,bitmap=IntPtr.Zero,old=IntPtr.Zero;
  IntPtr previous=SetThreadDpiAwarenessContext(new IntPtr(-4));
  try {
   Check(window,pid,x,y,width,height);
   long started=Process.GetProcessById((int)pid).StartTime.ToUniversalTime().Ticks;
   screen=GetDC(IntPtr.Zero); if(screen==IntPtr.Zero) throw new Exception("No display context");
   memory=CreateCompatibleDC(screen); bitmap=CreateCompatibleBitmap(screen,width,height);
   if(memory==IntPtr.Zero || bitmap==IntPtr.Zero) throw new Exception("No capture context");
   old=SelectObject(memory,bitmap); if(old==IntPtr.Zero || old==new IntPtr(-1)) throw new Exception("No bitmap context");
   // The target app renders its own surface. No BitBlt, screen crop or coordinate fallback.
   if(!PrintWindow(window,memory,0)) throw new Exception("Application cannot render an isolated capture");
   Check(window,pid,x,y,width,height);
   if(Process.GetProcessById((int)pid).StartTime.ToUniversalTime().Ticks!=started) throw new Exception("Process replaced");
   SelectObject(memory,old); old=IntPtr.Zero;
   Header header=new Header(); header.size=40; header.width=width; header.height=height;
   header.planes=1; header.bits=32; header.imageSize=(uint)(width*height*4);
   byte[] pixels=new byte[header.imageSize];
   if(GetDIBits(memory,bitmap,0,(uint)height,pixels,ref header,0)!=height) throw new Exception("No pixels returned");
   bool nonblack=false;
   for(int i=0;i<pixels.Length;i+=4) {if(pixels[i]!=0 || pixels[i+1]!=0 || pixels[i+2]!=0){nonblack=true;break;}}
   if(!nonblack) throw new Exception("Application returned a blank capture");
   return Convert.ToBase64String(pixels);
  } finally {
   if(old!=IntPtr.Zero && memory!=IntPtr.Zero) SelectObject(memory,old);
   if(bitmap!=IntPtr.Zero) DeleteObject(bitmap);
   if(memory!=IntPtr.Zero) DeleteDC(memory);
   if(screen!=IntPtr.Zero) ReleaseDC(IntPtr.Zero,screen);
   if(previous!=IntPtr.Zero) SetThreadDpiAwarenessContext(previous);
  }
 }
}
'@
try {
 $r=[Console]::In.ReadToEnd() | ConvertFrom-Json
 $pixels=[JarvixWindowCapture]::Capture([long]$r.handle,[uint32]$r.process_id,[int]$r.x,[int]$r.y,[int]$r.width,[int]$r.height)
 @{ok=$true;data=@{pixels=$pixels}} | ConvertTo-Json -Compress
} catch {
 @{ok=$false;error='Isolated window capture is unavailable.'} | ConvertTo-Json -Compress
 exit 1
}
'''


def capture_window(handle, process_id, x, y, width, height):
    """Hung application rendering is killed after the fixed worker deadline."""
    try:
        result = run_native_script(SCRIPT, {"handle": handle, "process_id": process_id,
                                           "x": x, "y": y, "width": width, "height": height},
                                   timeout=12, max_output=214_000_000)
    except ValueError as exc:
        raise RuntimeError("This application did not provide an isolated window capture. No desktop pixels were substituted.") from exc
    pixels = base64.b64decode(result["pixels"], validate=True)
    if len(pixels) != width * height * 4:
        raise ValueError("The application returned invalid capture dimensions.")
    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    return struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54) + header + pixels
