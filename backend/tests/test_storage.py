from __future__ import annotations

import unittest
import uuid

from backend import storage


class TestValidAnalysisId(unittest.TestCase):
    def test_accepts_generated_ids(self):
        self.assertTrue(storage.valid_analysis_id(str(uuid.uuid4())))

    def test_rejects_traversal(self):
        for bad in ("..", "../../secrets", "..\\..\\secrets"):
            self.assertFalse(storage.valid_analysis_id(bad), bad)

    def test_rejects_absolute_path(self):
        self.assertFalse(storage.valid_analysis_id("C:/Windows"))

    def test_rejects_empty(self):
        self.assertFalse(storage.valid_analysis_id(""))

    def test_rejects_lookalike(self):
        # A near-miss that is still a legal filename must not be accepted just
        # because it looks uuid-ish.
        self.assertFalse(storage.valid_analysis_id(str(uuid.uuid4()) + "x"))
        self.assertFalse(storage.valid_analysis_id(str(uuid.uuid4()).upper() + " "))

    def test_load_raises_for_invalid_id(self):
        with self.assertRaises(FileNotFoundError):
            storage.AnalysisState.load("../../..")


class TestRoundTrip(unittest.TestCase):
    def test_create_update_and_reload(self):
        st = storage.AnalysisState.create()
        st.set_phase("selecting")
        st.update(results={"floodPixels": 7})

        reloaded = storage.AnalysisState.load(st.analysis_id).get()
        self.assertEqual(reloaded["phase"], "selecting")
        self.assertEqual(reloaded["results"]["floodPixels"], 7)
        self.assertIn("updatedAt", reloaded)

    def test_set_error_moves_to_error_phase(self):
        st = storage.AnalysisState.create()
        st.set_error("boom")
        data = st.get()
        self.assertEqual(data["phase"], "error")
        self.assertEqual(data["error"], "boom")


if __name__ == "__main__":
    unittest.main()