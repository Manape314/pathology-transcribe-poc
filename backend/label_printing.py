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

The label's scannable code is a QR code encoding a LOOKUP URL —
"{FRONTEND_URL}/lookup.html?id={request_id}" — not the request or any
contact details themselves. The QR is a reference to the server-side
record, never a container for it: scanning it opens the lookup page
straight to that request (frontend/lookup.js reads the `?id=` query
param), which is what lets a pathologist on a different device just
scan and go rather than also having to type the ID. Doctor contact
details (name, phone) are looked up server-side too, via
GET /print-lookup/{request_id} (see print_records.py) — this is a
privacy choice, not an oversight: encoding a doctor's phone number
directly into a physical label anyone could find or photograph is worse
than requiring a server lookup that at least goes through this project's
one access-control mechanism (the request ID itself as the key). The
request ID is ALSO drawn as separate plain text under the QR code, so
it's still readable/typeable by hand if scanning fails or the QR points
at a stale FRONTEND_URL.

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

import qrcode
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

# Where frontend/lookup.html is actually served from — used to build the
# QR's lookup URL. The default is a common local static-server address for
# development; set this to your real hosted frontend URL (e.g.
# "https://my-frontend.onrender.com") before printing any label meant to
# be scanned from a different device.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5500").rstrip("/")

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


def render_label_image(doctor_name: str, request_id: str, qr_payload: str | None = None) -> Image.Image:
    """Builds the label bitmap: doctor_name as text at the top, a QR code
    encoding the lookup URL for request_id below it, with request_id ALSO
    drawn as separate plain text under the QR code so it's readable/
    typeable by hand without a scanner. Same two-section layout as the
    hand-drawn label sketch this feature was designed from. The QR never
    carries anything beyond a reference (FRONTEND_URL + request_id) — the
    actual request and any contact details are looked up server-side (see
    this module's docstring for why).

    `qr_payload` defaults to building the URL from this process's OWN
    FRONTEND_URL (every existing call site — the hosted backend's own
    display rendering). print_agent.py instead passes the EXACT string
    the backend already computed for this job, so the agent's local
    FRONTEND_URL (if it's even set) can never drift from what's actually
    in the printed QR — two independently-configured env vars needing to
    agree is a real footgun; this removes it rather than documenting
    around it."""
    if qr_payload is None:
        qr_payload = f"{FRONTEND_URL}/lookup.html?id={request_id}"
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

    # Small text line for the request ID, reserved at the very bottom, so
    # it stays readable without a scanner regardless of QR size above it.
    id_font = _load_font(max(8, round(height_px * 0.08)))
    id_bbox = draw.textbbox((0, 0), request_id, font=id_font)
    id_h = id_bbox[3] - id_bbox[1]

    # QR code fills the remaining space between the name and the ID line.
    qr_top = text_bottom + margin
    qr_available_w = max(1, width_px - 2 * margin)
    qr_available_h = max(1, height_px - qr_top - margin * 2 - id_h)

    qr = qrcode.QRCode(border=1)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    qr_image = qr.make_image().get_image().convert("RGB")

    # Fit within the remaining box WITHOUT distorting the aspect ratio.
    qr_image.thumbnail((qr_available_w, qr_available_h), Image.LANCZOS)
    qr_x = margin + (qr_available_w - qr_image.width) // 2
    qr_y = qr_top + (qr_available_h - qr_image.height) // 2
    canvas.paste(qr_image, (qr_x, qr_y))

    id_x = margin + (qr_available_w - (id_bbox[2] - id_bbox[0])) // 2
    id_y = qr_y + qr_image.height + margin
    draw.text((id_x, id_y), request_id, fill="black", font=id_font)

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
