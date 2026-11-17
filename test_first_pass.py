"""End-to-end first-submission boundary tests with the preinstalled local renderer."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

from first_pass import digest, record


METHOD = b"""from PIL import Image
def render(plan):
    return Image.new('RGB', (2, 1), (plan['red'], 8, 9))
"""
PROGRAM = b"""def build(prompt):
    return {'red': len(prompt)}
"""


def local_runtime():
    try:
        __import__('PIL')
    except ImportError:
        return False
    return bool(sys.executable)


class FirstSubmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.registry = Path(temp.name)
        self.task = dict(id="dev", contract=digest(b'test only'), prompt="red",
                         prompt_sha256=digest(b'red'), method_sha256=digest(METHOD))

    def test_sha_mismatch_before_registration(self):
        with self.assertRaisesRegex(ValueError, "after freeze"):
            record(self.task, METHOD + b" ", PROGRAM, self.registry, 30)
        self.assertEqual(list(self.registry.iterdir()), [])

    @unittest.skipUnless(local_runtime(), 'local offline runtime unavailable')
    def test_frozen_method_renders_and_claim_survives_success(self):
        case = record(self.task, METHOD, PROGRAM, self.registry, 30)
        png = (case / 'image.png').read_bytes()
        receipt = json.loads((case / 'receipt.json').read_bytes())
        self.assertEqual((receipt['method_entry'], receipt['image_sha256']), ('method.render', digest(png)))
        self.assertEqual(png[37:41], b'IDAT')
        n = struct.unpack('>I', png[33:37])[0]
        self.assertEqual(zlib.decompress(png[41:41+n]), b'\x00\x03\x08\x09\x03\x08\x09')
        with self.assertRaisesRegex(ValueError, "already claimed"):
            record(self.task, METHOD, PROGRAM, self.registry, 30)

    @unittest.skipUnless(local_runtime(), 'local offline runtime unavailable')
    def test_failure_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError, "exited"):
            record(self.task, METHOD, b'def build(prompt): raise ValueError("broken")', self.registry, 30)
        receipt = next(self.registry.rglob('receipt.json'))
        self.assertEqual(json.loads(receipt.read_text())['status'], 'failed')
        with self.assertRaisesRegex(ValueError, "already claimed"):
            record(self.task, METHOD, PROGRAM, self.registry, 30)
