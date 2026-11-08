import numpy as np
import pytest
from PIL import Image

from lab_toolbox.reader import Debouncer, decode
from lab_toolbox.spots import classify, decide


def test_decode_qr():
    zxingcpp = pytest.importorskip("zxingcpp")
    img = zxingcpp.write_barcode_to_image(zxingcpp.create_barcode("T-0001", zxingcpp.BarcodeFormat.QRCode), scale=8)
    assert decode(Image.fromarray(np.asarray(img))) == ["T-0001"]


def test_debouncer_one_holdup_one_scan():
    t = [0.0]
    d = Debouncer(hold_s=2, clock=lambda: t[0])
    assert d.feed(["A"]) == ["A"]
    for _ in range(10):  # label held in view for several seconds
        t[0] += 0.5
        assert d.feed(["A"]) == []
    t[0] += 2.5  # taken away, shown again
    assert d.feed(["A", "B", "B"]) == ["A", "B"]


def test_classify():
    ref = np.full((100, 100), 80, dtype=np.int16)
    ref[10:40, 10:40] = 200  # tool present
    box = (10, 10, 30, 30)
    assert classify(ref, ref.copy(), box) == "full"
    empty = ref.copy()
    empty[10:40, 10:40] = 80
    assert classify(ref, empty, box) == "empty"
    dim = ref.copy()
    dim[10:40, 10:40] = 180  # lighting shift
    assert classify(ref, dim, box) == "unclear"
    assert classify(ref, np.zeros((50, 50), dtype=np.int16), box) == "unclear"


@pytest.mark.parametrize(
    "camera, out, alert",
    [
        ("empty", True, None),
        ("full", False, None),
        ("empty", False, "missing_tool"),
        ("full", True, "returned_without_scan"),
        ("unclear", True, "check_camera"),
        ("unclear", False, "check_camera"),
    ],
)
def test_decide_matches_prd_table(camera, out, alert):
    assert decide(camera, out) == alert
