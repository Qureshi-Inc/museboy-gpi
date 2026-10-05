import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gpi-muse-skill-onboard"


class MuseSkillOnboardingTests(unittest.TestCase):
    def test_sends_one_consent_prompt_after_pairing_and_never_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pairing = root / "pairing.json"
            pairing.write_text("{}")
            marker = root / "skill-consent-prompt-sent"
            capture = root / "messages"
            fake_musegadget = root / "musegadget"
            fake_musegadget.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$MUSE_CAPTURE_FILE\"\n")
            fake_musegadget.chmod(0o755)
            fake_systemctl = root / "systemctl"
            fake_systemctl.write_text("#!/bin/sh\nexit 0\n")
            fake_systemctl.chmod(0o755)
            env = os.environ.copy()
            env.update({"MUSE_PAIRING_FILE": str(pairing),
                        "MUSE_SKILL_ONBOARD_MARKER": str(marker),
                        "MUSEGADGET_BIN": str(fake_musegadget),
                        "SYSTEMCTL_BIN": str(fake_systemctl),
                        "MUSE_CAPTURE_FILE": str(capture)})

            subprocess.run([str(SCRIPT)], env=env, check=True)
            subprocess.run([str(SCRIPT)], env=env, check=True)

            self.assertTrue(marker.exists())
            messages = capture.read_text().splitlines()
            self.assertEqual(len(messages), 1)
            self.assertIn("send-user-msg", messages[0])
            self.assertIn("/opt/gpi/apps/builder/SKILL.md", messages[0])
            self.assertIn("say yes", messages[0])
            self.assertIn("file.read", messages[0])

    def test_does_not_prompt_before_pairing_or_consume_consent_on_send_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pairing = root / "pairing.json"
            marker = root / "skill-consent-prompt-sent"
            fake_musegadget = root / "musegadget"
            fake_musegadget.write_text("#!/bin/sh\nexit 1\n")
            fake_musegadget.chmod(0o755)
            fake_systemctl = root / "systemctl"
            fake_systemctl.write_text("#!/bin/sh\nexit 0\n")
            fake_systemctl.chmod(0o755)
            env = os.environ.copy()
            env.update({"MUSE_PAIRING_FILE": str(pairing),
                        "MUSE_SKILL_ONBOARD_MARKER": str(marker),
                        "MUSEGADGET_BIN": str(fake_musegadget),
                        "SYSTEMCTL_BIN": str(fake_systemctl)})

            subprocess.run([str(SCRIPT)], env=env, check=True)
            self.assertFalse(marker.exists())
            pairing.write_text("{}")
            result = subprocess.run([str(SCRIPT)], env=env, check=False,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
