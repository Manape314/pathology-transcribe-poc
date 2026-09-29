"""
Generates and prints the physical barcode label for a confirmed pathology
request, on a NIIMBOT B21 thermal label printer — via niimprint
(https://github.com/AndBondStyle/niimprint), a community reverse-engineered
protocol client, since NIIMBOT publishes neither a protocol nor a standard
Windows print driver for their printers.

Printing happens SERVER-SIDE, not in the browser: the PWA just POSTs to
/print-label (see main.py) and the backend — which runs on the same
machine the printer is connected to — does the actual USB/Bluetooth
printer communication. This is deliberate, not incidental: iOS Safari
supports neither the Web Bluetooth nor the WebUSB APIs (and Apple has
stated no intent to add them), so any design where the *browser* talks to
the printer directly is a dead end on iPhone/iPad. Routing the print
through the backend means the PWA works identically on every platform —
it's an ordinary HTTPS POST, not a hardware API call.

The label encodes a short REQUEST ID only (never the transcript itself —
barcodes can't hold that much data at a scannable print size) plus the
doctor's name as plain text. Scanning the barcode is how lab staff look up
the full confirmed transcript, via print_records.py + GET /print-lookup.

Setup note: niimprint's package metadata declares Python "<3.12", which is
overly conservative — nothing in the actual code is 3.12-incompatible, so
it's installed here with `pip install --ignore-requires-python` (see
requirements.txt). Pillow is pinned to niimprint's declared supported
range (<11.0) to match what it was actually tested against.
"""

import base64
import io
import os
import random
from datetime import datetime

import barcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# Configuration (all overridable via environment variables — same pattern as
# WHISPER_MODEL/DEVICE/COMPUTE_TYPE in main.py).
# --------------------------------------------------------------------------- #
PRINTER_CONNECTION = os.getenv("PRINTER_CONNECTION", "usb")  # "usb" | "bluetooth"
PRINTER_USB_PORT = os.getenv("PRINTER_USB_PORT", "auto")
PRINTER_BT_ADDRESS = os.getenv("PRINTER_BT_ADDRESS", "")
PRINTER_DENSITY = int(os.getenv("PRINTER_DENSITY", "3"))
LABEL_WIDTH_MM = float(os.getenv("PRINTER_LABEL_WIDTH_MM", "50"))
LABEL_HEIGHT_MM = float(os.getenv("PRINTER_LABEL_HEIGHT_MM", "30"))
LABEL_DPI = 203  # NIIMBOT B21 hardware resolution — not user-configurable

# Unambiguous alphabet for the random suffix — excludes 0/O and 1/I, which
# are easily confused when a doctor has to read/type the ID by hand.
_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_ID_SUFFIX_LEN = 4


def generate_request_id() -> str:
    """"PR" + YYMMDD + a 4-character random suffix, e.g. "PR260929-7K4M".
    Short enough to print clearly on a 50mm label; the suffix's 32^4
    per-day space makes collisions negligible (still retried on the rare
    DB collision in main.py, never assumed impossible)."""
    date_part = datetime.now().strftime("%y%m%d")
    suffix = "".join(random.choices(_ID_ALPHABET, k=_ID_SUFFIX_LEN))
    return f"PR{date_part}-{suffix}"


def _mm_to_px(mm: float) -> int:
    return round(mm / 25.4 * LABEL_DPI)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_label_image(doctor_name: str, request_id: str) -> Image.Image:
    """Builds the label bitmap: doctor_name as text at the top, a Code128
    barcode of request_id filling the space below — the same two-section
    layout as the hand-drawn label sketch this feature was designed from.
    Never encodes anything beyond request_id in the barcode itself; the
    full record is looked up server-side via print_records.py."""
    width_px = _mm_to_px(LABEL_WIDTH_MM)
    height_px = _mm_to_px(LABEL_HEIGHT_MM)
    margin = max(4, round(width_px * 0.04))

    canvas = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(canvas)

    # Doctor name text, shrinking to fit the label width if needed.
    font_size = round(height_px * 0.18)
    font = _load_font(font_size)
    while font_size > 8:
        bbox = draw.textbbox((0, 0), doctor_name, font=font)
        if bbox[2] - bbox[0] <= width_px - 2 * margin:
            break
        font_size -= 1
        font = _load_font(font_size)
    draw.text((margin, margin), doctor_name, fill="black", font=font)
    text_bottom = margin + draw.textbbox((0, 0), doctor_name, font=font)[3]

    # Barcode fills the remaining space below the text.
    barcode_top = text_bottom + margin
    barcode_w = max(1, width_px - 2 * margin)
    barcode_h = max(1, height_px - barcode_top - margin)
    code = barcode.get("code128", request_id, writer=ImageWriter())
    bc_image = code.render(
        writer_options={
            "module_width": 0.3,
            "module_height": 10.0,
            "quiet_zone": 1.0,
            "font_size": 0,
            "text_distance": 0,
            "write_text": False,
        }
    )
    bc_image = bc_image.resize((barcode_w, barcode_h), Image.LANCZOS)
    canvas.paste(bc_image, (margin, barcode_top))

    return canvas


def image_to_data_url(image: Image.Image) -> str:
    """Encodes the SAME rendered label as a "data:image/png;base64,..."
    string — used so the request produces two IDENTICAL barcodes (same
    request_id, same image bytes): one sent to the physical printer
    (print_label_image), one kept with the digital record and shown
    directly in the PWA (main.py includes this in /print-label and
    /print-lookup; never re-rendered separately, so the two can't drift)."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class PrinterConnectionError(Exception):
    """Raised for any printer communication failure (not connected, wrong
    port, Bluetooth pairing issue, etc). Always caught by main.py's
    /print-label handler — a hardware failure must never prevent the
    digital record from being saved, and must never surface as an
    unhandled 500 with no clear explanation."""


def print_label_image(image: Image.Image) -> None:
    """Sends `image` to the configured NIIMBOT printer via niimprint. Raises
    PrinterConnectionError (never a raw niimprint/serial/bluetooth
    exception) so callers get one consistent, clearly-worded failure mode."""
    import niimprint

    try:
        if PRINTER_CONNECTION == "bluetooth":
            if not PRINTER_BT_ADDRESS:
                raise PrinterConnectionError(
                    "PRINTER_CONNECTION=bluetooth but PRINTER_BT_ADDRESS is not set"
                )
            transport = niimprint.BluetoothTransport(PRINTER_BT_ADDRESS)
        else:
            transport = niimprint.SerialTransport(PRINTER_USB_PORT)

        client = niimprint.PrinterClient(transport)
        client.print_image(image, density=PRINTER_DENSITY)
    except PrinterConnectionError:
        raise
    except Exception as exc:  # noqa: BLE001 — any transport/protocol failure
        raise PrinterConnectionError(
            f"Could not reach the NIIMBOT printer ({PRINTER_CONNECTION} "
            f"connection): {exc}"
        ) from exc
