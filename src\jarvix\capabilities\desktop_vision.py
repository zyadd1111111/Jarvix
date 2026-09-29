"""Explicit screen context and local image comparison; no ambient recording."""
from __future__ import annotations

import base64
import hashlib
import re
import struct
from pathlib import Path

from jarvix.capabilities.files import _linked
from jarvix.runtime import check_cancelled


OCR_SCRIPT = r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
try {
 Add-Type -AssemblyName System.Runtime.WindowsRuntime
 $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Storage.Streams.DataWriter,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
 $async=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
 function Await($operation,$type) {
  $task=$async.MakeGenericMethod($type).Invoke($null,@($operation)); $task.Wait(); return $task.Result
 }
 $r=[Console]::In.ReadToEnd() | ConvertFrom-Json
 $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
 if($null -eq $engine){throw 'No installed OCR language'}
 $bytes=[Convert]::FromBase64String([string]$r.bitmap)
 $stream=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
 $writer=[Windows.Storage.Streams.DataWriter]::new($stream)
 $writer.WriteBytes($bytes)
 $null=Await ($writer.StoreAsync()) ([uint32]); $stream.Seek(0)
 $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
 if($decoder.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
    $decoder.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension){throw 'Image too large'}
 $bitmap=Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                  [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore)) ([Windows.Graphics.Imaging.SoftwareBitmap])
 $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
 $lines=@($result.Lines | Select-Object -First 250 | ForEach-Object {
   $text=[string]$_.Text; $text.Substring(0,[Math]::Min(1000,$text.Length)) })
 @{ok=$true;data=@{available=$true;language=$engine.RecognizerLanguage.LanguageTag;lines=$lines;
   bounded=($result.Lines.Count -gt 250)}} | ConvertTo-Json -Depth 4 -Compress
} catch {
 @{ok=$true;data=@{available=$false}} | ConvertTo-Json -Compress
} finally {
 if($null -ne $bitmap){$bitmap.Dispose()}
 if($null -ne $writer){$writer.Dispose()}
 if($null -ne $stream){$stream.Dispose()}
}
'''


class DesktopVisionService:
    def __init__(self, services):
        self.services = services

    def _gate(self):
        check_cancelled()
        if not self.services.settings.get("screenshots.enabled", False):
            raise PermissionError("Enable screen access in Settings first.")

    def capture(self, target="active", monitor=0, handle=None, process_id=None):
        self._gate()
        if target == "active":
            current = self.services.desktop.active_window()
            if not current.get("handle"):
                raise ValueError("No foreground application is available.")
            handle, process_id = current["handle"], current["process_id"]
            target = "window"
        if target == "window":
            if not handle or not process_id:
                raise ValueError("Select a window handle and its owning process.")
            inspection = self.services.desktop.inspect_ui(handle, process_id, limit=180)
            if any(item.get("password") for item in inspection["elements"]):
                raise PermissionError("This window includes protected fields. Hide them before capturing.")
            if inspection.get("bounded"):
                raise PermissionError("This window cannot be fully checked for protected fields. Use an explicit monitor capture after hiding private content.")
        elif target != "monitor":
            raise ValueError("Choose an active window, selected window, or monitor.")
        result = self.services.screenshots.capture(target, monitor, handle, process_id)
        self._gate()
        self.services.records.put("vision_capture", {"screenshot_id": result["id"], "handle": handle,
                                                    "process_id": process_id, "target": target}, result["id"])
        return {**result, "external_upload": False, "analysis": "No image inference has been performed."}

    def inspect(self, handle, process_id):
        self._gate()
        result = self.services.desktop.inspect_ui(handle, process_id, limit=180)
        visible = [row for row in result["elements"] if not row.get("password") and not row.get("offscreen")]
        texts = list(dict.fromkeys(row["name"] for row in visible if row.get("name")))
        errors = [text for text in texts if re.search(r"\b(error|exception|failed|failure|denied|crash|not responding)\b", text, re.I)]
        controls = [{key: row[key] for key in ("element_ref", "name", "control_type", "enabled") if key in row}
                    for row in visible if row["control_type"] in {"Button", "Edit", "Menu", "MenuItem", "Tab", "TabItem", "ComboBox"}]
        return {"handle": handle, "process_id": process_id, "application": result["application"],
                "visible_text": texts[:100], "controls": controls[:80], "possible_errors": errors[:20],
                "bounded": result["bounded"], "source": "Windows UI Automation",
                "ocr_performed": False, "image_inference_performed": False,
                "note": "Only accessibility-exposed text is available; images, canvas content and inaccessible controls may be absent."}

    def history(self, limit=20):
        self._gate()
        return self.services.screenshots.history(limit)

    def _bitmap(self, screenshot_id):
        self._gate()
        row = self.services.records.get("screenshot", screenshot_id)
        path = Path(row["path"])
        directory = self.services.data_dir / "screenshots"
        if _linked(path) or _linked(directory) or not path.resolve().is_relative_to(directory.resolve()):
            raise PermissionError("Screenshot path is outside local capture storage.")
        if path.stat().st_size > 160000054:
            raise ValueError("Screenshot exceeds the comparison limit.")
        data = path.read_bytes()
        self._gate()
        if len(data) < 54 or data[:2] != b"BM":
            raise ValueError("The screenshot is not a supported bitmap.")
        offset = struct.unpack_from("<I", data, 10)[0]
        size, width, height, planes, bits, compression = struct.unpack_from("<IiiHHI", data, 14)
        if size != 40 or width <= 0 or height <= 0 or planes != 1 or bits != 32 or compression != 0:
            raise ValueError("Only native uncompressed 32-bit captures support pixel comparison.")
        pixel_count = width * height
        if pixel_count > 40000000 or offset < 54 or offset + pixel_count * 4 != len(data):
            raise ValueError("Invalid bitmap dimensions.")
        return width, height, data[offset:]

    def compare(self, before_id, after_id):
        before = self._bitmap(before_id)
        after = self._bitmap(after_id)
        if before[:2] != after[:2]:
            return {"changed": True, "same_dimensions": False, "before_size": list(before[:2]),
                    "after_size": list(after[:2]), "completion_verified": False}
        # Native BMP alpha bytes are unused, so comparison considers only RGB.
        different = 0
        total = before[0] * before[1]
        for offset in range(0, len(before[2]), 4):
            if offset % 262144 == 0:
                check_cancelled()
            if before[2][offset:offset + 3] != after[2][offset:offset + 3]:
                different += 1
        return {"changed": different > 0, "same_dimensions": True, "changed_pixels": different,
                "changed_percent": round(different * 100 / total, 4), "total_pixels": total,
                "before_hash": hashlib.sha256(before[2]).hexdigest(),
                "after_hash": hashlib.sha256(after[2]).hexdigest(), "completion_verified": False,
                "note": "Pixel differences do not establish whether an application task succeeded."}

    def read_text(self, screenshot_id):
        """Only the validated, already saved bytes enter the isolated local OCR worker."""
        from jarvix.capabilities.desktop import PROTECTED, SECRET, redact
        from jarvix.capabilities.windows_uia import run_native_script

        width, height, pixels = self._bitmap(screenshot_id)
        if len(pixels) > 32_000_000:
            raise ValueError("Select a smaller capture for OCR (up to 8 million pixels).")
        header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixels), 0, 0, 0, 0)
        bitmap = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54) + header + pixels
        result = run_native_script(OCR_SCRIPT, {"bitmap": base64.b64encode(bitmap).decode("ascii")},
                                   self._gate, timeout=25, max_input=43_000_000)
        self._gate()
        if not result.get("available"):
            raise RuntimeError("Windows OCR is unavailable for this image. Install a Windows OCR language or use accessibility inspection.")
        lines, suppress_next = [], False
        for text in result.get("lines", [])[:250]:
            protected = bool(PROTECTED.search(text) or SECRET.search(text))
            if protected or suppress_next:
                lines.append("[protected text omitted]")
            else:
                lines.append(redact(text))
            suppress_next = protected
        return {"screenshot_id": screenshot_id, "lines": lines, "text": "\n".join(lines),
                "language": result.get("language", ""), "bounded": bool(result.get("bounded")),
                "source": "Windows OCR", "ocr_performed": True, "external_upload": False,
                "note": "OCR may be inaccurate. It does not verify application state."}
