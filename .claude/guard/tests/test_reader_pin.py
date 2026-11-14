"""The shell reader's pin: the corpus gate has passed for the reader, the gate's own code and refusal list, and the
bash it asks, as they are, as the record gates/pin_gate.py writes says. The case reads the record through `pin_gate.py --check` and never builds
the reader or runs the corpus gate, so a suite run after a pin change is red until the pin gate has passed."""

import subprocess
import sys
import unittest
from pathlib import Path

PIN_GATE = Path(__file__).resolve().parents[1] / "gates" / "pin_gate.py"


class Pin(unittest.TestCase):
    def test_the_corpus_gate_passed_for_the_reader_pin(self) -> None:
        run = subprocess.run([sys.executable, str(PIN_GATE), "--check"], capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, f"run {PIN_GATE}, which builds the reader and runs the corpus gate: "
                                            f"{run.stdout}{run.stderr}")
