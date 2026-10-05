import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "builder"))

from local_plan import (DRAFT_SCHEMA, make_prompt, preview,
                        transcript_only_plan, usable_transcript, validate_plan)


def sample_draft():
    return {
        "app_name": "Pocket Tasks",
        "description": "A tiny offline todo list for quick tasks.",
        "features": ["Add and complete tasks", "Filter active and done tasks"],
        "data_sources": ["none"],
    }


class PlanContractTests(unittest.TestCase):
    def test_valid_brief_has_exact_fields_and_verbatim_transcript(self):
        transcript = "  Make a pocket todo app, please.  "
        plan = validate_plan(sample_draft(), transcript)
        self.assertEqual(list(plan), ["transcript", "app_name", "app_id",
                                      "description", "features", "data_sources"])
        self.assertEqual(plan["transcript"], transcript)
        self.assertEqual(plan["app_id"], "pocket-tasks")
        self.assertEqual(plan["data_sources"], ["none"])

    def test_app_id_is_safely_derived_as_lowercase_slug(self):
        draft = sample_draft()
        draft["app_name"] = "Pocket Tasks!"
        self.assertEqual(validate_plan(draft, "idea")["app_id"], "pocket-tasks")

    def test_rejects_missing_and_too_few_features(self):
        draft = sample_draft()
        draft["features"] = ["Add tasks"]
        with self.assertRaisesRegex(RuntimeError, "2–5"):
            validate_plan(draft, "idea")

    def test_prompt_is_short_and_never_invites_invented_apis(self):
        prompt = make_prompt("Make a todo list.")
        self.assertIn("Never invent an API", prompt)
        self.assertIn('Exact voice transcript: Make a todo list.', prompt)
        self.assertLess(len(prompt), 500)

    def test_generated_schema_omits_transcript_to_preserve_exact_words(self):
        self.assertNotIn("transcript", DRAFT_SCHEMA["properties"])

    def test_transcript_only_handoff_contains_no_gemma_generated_fields(self):
        words = "Add a tiny weather app and show today's forecast."
        self.assertEqual(transcript_only_plan(words), {"transcript": words})

    def test_single_word_whisper_noise_is_rejected_for_transcript_handoff(self):
        self.assertFalse(usable_transcript("you"))
        self.assertFalse(usable_transcript("   "))
        self.assertTrue(usable_transcript("Make a todo list"))

    def test_preview_uses_compact_brief_fields(self):
        result = preview(validate_plan(sample_draft(), "Make a pocket todo app."))
        self.assertIn("Pocket Tasks", result)
        self.assertIn("Add and complete tasks", result)
        self.assertIn("none", result)

    def test_unmentioned_endpoint_is_never_forwarded(self):
        draft = sample_draft()
        draft["data_sources"] = ["https://invented.example/api"]
        result = validate_plan(draft, "Make an offline pocket todo list.")
        self.assertEqual(result["data_sources"], ["none"])


if __name__ == "__main__":
    unittest.main()
