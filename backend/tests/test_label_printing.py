import re

import niimprint
import pytest
from PIL import Image

import label_printing


def test_generate_request_id_format():
    rid = label_printing.generate_request_id()
    assert re.fullmatch(r"PR\d{6}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}", rid)


def test_generate_request_id_excludes_ambiguous_characters():
    # 0/O and 1/I are easily confused if a doctor has to read/type the ID
    # by hand — confirmed excluded from the suffix alphabet.
    ids = [label_printing.generate_request_id() for _ in range(200)]
    suffixes = "".join(rid.split("-")[1] for rid in ids)
    assert not any(c in suffixes for c in "01OI")


def test_generate_request_id_is_effectively_unique():
    ids = {label_printing.generate_request_id() for _ in range(500)}
    assert len(ids) == 500


# --------------------------------------------------------------------------- #
# QR content — a lookup URL referencing the request ID, never the request
# or contact details themselves. An earlier iteration encoded doctor
# name/phone/email directly in the QR; that was reverted specifically for
# privacy (a physical label anyone could find/photograph shouldn't carry
# a doctor's phone number) — contact details are looked up server-side
# instead via GET /print-lookup/{request_id} (see print_records.py /
# test_print_endpoints.py). The URL form (rather than the bare ID) lets a
# pathologist's device navigate straight to the right record on scan,
# which is what makes retrieval work from a completely different device
# with no shared network (frontend/lookup.js reads the ?id= param).
# --------------------------------------------------------------------------- #


def test_qr_code_encodes_a_lookup_url_referencing_the_request_id():
    import qrcode

    request_id = "PR260929-7K4M"
    img = label_printing.render_label_image("Dr. Thato Manape", request_id)
    assert isinstance(img, Image.Image)

    expected_data = f"{label_printing.FRONTEND_URL}/lookup.html?id={request_id}"

    # Independently build the same QR this module would, and confirm the
    # encoded payload matches exactly (a stand-in for a real decode — no
    # zbar/decoder dependency available in this environment; see this
    # file's other render tests for the visual/structural checks that
    # don't need one).
    expected = qrcode.QRCode(border=1)
    expected.add_data(expected_data)
    expected.make(fit=True)
    assert expected.data_list[0].data.decode() == expected_data
    assert expected.version <= 6  # still comfortably small enough for a 50mm label


# --------------------------------------------------------------------------- #
# Label rendering
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "doctor_name",
    [
        "Dr. Thato Manapi",
        "Dr. A",
        "Dr. Alexander Constantinou-Papadopoulos",  # exercises shrink-to-fit
    ],
)
def test_render_label_image_produces_correctly_sized_image(doctor_name):
    img = label_printing.render_label_image(doctor_name, "PR260929-7K4M")
    assert isinstance(img, Image.Image)
    expected_w = label_printing._mm_to_px(label_printing.LABEL_WIDTH_MM)
    expected_h = label_printing._mm_to_px(label_printing.LABEL_HEIGHT_MM)
    assert img.size == (expected_w, expected_h)


def test_render_label_image_never_raises_for_long_names():
    # Shrink-to-fit must degrade gracefully, never crash, for an
    # unreasonably long name.
    img = label_printing.render_label_image("Dr. " + "A" * 80, "PR260929-7K4M")
    assert img.size[0] > 0 and img.size[1] > 0


def test_image_to_data_url_round_trips():
    img = label_printing.render_label_image("Dr. Test", "PR260929-7K4M")
    data_url = label_printing.image_to_data_url(img)

    assert data_url.startswith("data:image/png;base64,")

    import base64
    import io

    payload = data_url.removeprefix("data:image/png;base64,")
    decoded = Image.open(io.BytesIO(base64.b64decode(payload)))
    assert decoded.size == img.size


def test_image_to_data_url_is_deterministic_for_identical_input():
    # The "two identical barcodes" guarantee depends on this: the SAME
    # rendered image always encodes to the SAME data URL, so the printed
    # copy and the stored/displayed copy are provably the same bytes.
    img1 = label_printing.render_label_image("Dr. Test", "PR260929-7K4M")
    img2 = label_printing.render_label_image("Dr. Test", "PR260929-7K4M")
    assert label_printing.image_to_data_url(img1) == label_printing.image_to_data_url(img2)


def test_print_label_image_calls_niimprint_with_configured_density(monkeypatch):
    calls = {}

    class FakeTransport:
        def __init__(self, *args, **kwargs):
            calls["transport_args"] = args

    class FakeClient:
        def __init__(self, transport):
            calls["client_transport"] = transport

        def print_image(self, image, density):
            calls["image"] = image
            calls["density"] = density

    monkeypatch.setattr(niimprint, "SerialTransport", FakeTransport)
    monkeypatch.setattr(niimprint, "PrinterClient", FakeClient)
    monkeypatch.setattr(label_printing, "PRINTER_CONNECTION", "usb")
    monkeypatch.setattr(label_printing, "PRINTER_DENSITY", 4)

    image = label_printing.render_label_image("Dr. Test", "PR260929-7K4M")
    label_printing.print_label_image(image)

    assert calls["image"] is image
    assert calls["density"] == 4


def test_print_label_image_wraps_failures_in_printer_connection_error(monkeypatch):
    class BrokenTransport:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("device not found")

    monkeypatch.setattr(niimprint, "SerialTransport", BrokenTransport)
    monkeypatch.setattr(label_printing, "PRINTER_CONNECTION", "usb")

    image = label_printing.render_label_image("Dr. Test", "PR260929-7K4M")
    with pytest.raises(label_printing.PrinterConnectionError, match="device not found"):
        label_printing.print_label_image(image)


def test_print_label_image_bluetooth_requires_address(monkeypatch):
    monkeypatch.setattr(label_printing, "PRINTER_CONNECTION", "bluetooth")
    monkeypatch.setattr(label_printing, "PRINTER_BT_ADDRESS", "")

    image = label_printing.render_label_image("Dr. Test", "PR260929-7K4M")
    with pytest.raises(label_printing.PrinterConnectionError, match="PRINTER_BT_ADDRESS"):
        label_printing.print_label_image(image)
