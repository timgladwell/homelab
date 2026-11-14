"""scripts/lib-pinned-tool.sh: require_pinned_tool, driven through bash.

Each test copies the library into a scratch repo with its own validate.yml and
a fake tool on PATH, so the real machine's tools never decide the outcome.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib-pinned-tool.sh"
# Enough for the library itself (sed, awk, dirname), and no pipx or real tools.
BASE_PATH = "/usr/bin:/bin"


def run(root, tool, var, path, package=""):
    return subprocess.run(
        ["bash", "-c", f'source "{root}/scripts/lib-pinned-tool.sh" && require_pinned_tool {tool} {var} {package}'],
        capture_output=True,
        text=True,
        env={"PATH": path, "HOME": str(root)},
        check=False,
    )


class RequirePinnedTool(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "scripts").mkdir()
        shutil.copy(LIB, self.root / "scripts")
        wf = self.root / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "validate.yml").write_text("env:\n  FAKE_VERSION: 1.2.3\n  WRAPPED_VERSION: 0.11.0.1\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def fake(self, version, output=None):
        exe = self.bin / "fake"
        exe.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output or f'fake {version}'}'\n")
        exe.chmod(0o755)

    # shellcheck --version: a banner, then the version on its own line.
    BANNER = "ShellCheck - shell script analysis tool\nversion: {}\nlicense: GNU General Public License"

    def test_matching_version_passes(self):
        self.fake("1.2.3")
        r = run(self.root, "fake", "FAKE_VERSION", f"{self.bin}:{BASE_PATH}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")

    def test_other_version_fails_with_the_fix(self):
        self.fake("1.2.4")
        r = run(self.root, "fake", "FAKE_VERSION", f"{self.bin}:{BASE_PATH}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("fake 1.2.4 is installed", r.stderr)
        self.assertIn("pipx install --force fake==1.2.3", r.stderr)
        # Not pipx's link, so it says to remove the copy that is winning.
        self.assertIn(f"remove the copy at {self.bin}/fake", r.stderr)

    def test_missing_tool_fails_with_the_install(self):
        r = run(self.root, "fake", "FAKE_VERSION", BASE_PATH)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fake not found on PATH", r.stderr)
        self.assertIn("pipx install fake==1.2.3", r.stderr)
        # No pipx on this PATH either, so it says how to get one.
        self.assertIn("brew install pipx", r.stderr)

    def test_wrapper_pin_with_its_own_revision_passes(self):
        # shellcheck-py 0.11.0.1 installs shellcheck 0.11.0.
        self.fake(None, self.BANNER.format("0.11.0"))
        r = run(self.root, "fake", "WRAPPED_VERSION", f"{self.bin}:{BASE_PATH}", "fake-py")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wrapper_of_another_release_fails_naming_the_package(self):
        self.fake(None, self.BANNER.format("0.10.0"))
        r = run(self.root, "fake", "WRAPPED_VERSION", f"{self.bin}:{BASE_PATH}", "fake-py")
        self.assertEqual(r.returncode, 1)
        self.assertIn("fake 0.10.0 is installed", r.stderr)
        self.assertIn("pipx install --force fake-py==0.11.0.1", r.stderr)

    def test_shorter_version_is_not_a_match_without_a_wrapper(self):
        # Only a wrapper's pin may run one component longer than the tool.
        self.fake("1.2")
        r = run(self.root, "fake", "FAKE_VERSION", f"{self.bin}:{BASE_PATH}")
        self.assertEqual(r.returncode, 1)

    def test_unknown_pin_fails(self):
        self.fake("1.2.3")
        r = run(self.root, "fake", "NOPE_VERSION", f"{self.bin}:{BASE_PATH}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not read NOPE_VERSION", r.stderr)


class RealWorkflowPins(unittest.TestCase):
    """require_pinned_tool reads each pin with one sed over the real validate.yml."""

    def test_each_pipx_pin_is_defined_once(self):
        text = (REPO_ROOT / ".github" / "workflows" / "validate.yml").read_text()
        for var in ("YAMLLINT_VERSION", "UV_VERSION", "RUFF_VERSION", "SHELLCHECK_VERSION"):
            with self.subTest(var=var):
                # Twice would read as a two-line "version" that no tool matches.
                self.assertEqual(len(re.findall(rf"^ *{var}: *\d+(\.\d+)+$", text, re.MULTILINE)), 1)


if __name__ == "__main__":
    unittest.main()
