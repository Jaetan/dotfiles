"""state/tiers.py: the tier file of a claude process beside its ledger, the exact bytes of a tier line, a line appended
whole after the lines before it, and the digest route.py's. Each case keeps its state in a temporary folder."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from dispatcher.tree import ShellText
from state import route, tiers
from state.session import BootId, Pid, Places, ProcessKey, StartTicks

KEY = ProcessKey(BootId("bbbb0001"), Pid(10), StartTicks(100))
TEXT = ShellText("echo 'café' $HOME")
HEX = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()


class Tiers(unittest.TestCase):
    """A sessions folder of the case's own."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.places = Places(sessions=Path(scratch.name) / "sessions", proc=Path(scratch.name) / "proc")

    def test_the_tier_file_is_tiers_jsonl_beside_the_ledger(self) -> None:
        self.assertEqual(tiers.tiers(self.places, KEY), self.places.sessions / KEY.name / "tiers.jsonl")
        self.assertEqual(tiers.tiers(self.places, KEY).parent, route.ledger(self.places, KEY).parent)

    def test_a_line_is_the_digest_and_the_tier_as_json_and_a_newline(self) -> None:
        for tier in ("heavy", "light"):
            with self.subTest(tier=tier):
                self.assertEqual(tiers.line(route.digest(TEXT), tier),
                                 f'{{"sha256": "{HEX}", "tier": "{tier}"}}\n'.encode("ascii"))

    def test_a_whole_line_is_appended_its_folder_made_and_a_later_one_after_it(self) -> None:
        path = tiers.tiers(self.places, KEY)
        tiers.append(path, tiers.line(route.digest(TEXT), "heavy"))
        self.assertEqual(path.read_bytes(), tiers.line(route.digest(TEXT), "heavy"))
        tiers.append(path, tiers.line(route.digest(TEXT), "light"))
        self.assertEqual(path.read_bytes(),
                         tiers.line(route.digest(TEXT), "heavy") + tiers.line(route.digest(TEXT), "light"))

    def test_the_digest_is_route_s(self) -> None:
        self.assertIs(tiers.digest, route.digest)
