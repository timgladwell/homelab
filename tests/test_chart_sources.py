"""Tests for scripts/chart_sources.py's offline logic. The network parts are exercised by step 15 and the audit."""
import unittest

from loader import load

cs = load("scripts/chart_sources.py")


class Version(unittest.TestCase):
    def test_prefix_and_suffix_ignored(self):
        self.assertEqual(cs.version("v1.21.2"), (1, 21, 2))
        self.assertEqual(cs.version("3.7.9-amd64"), (3, 7, 9))
        self.assertEqual(cs.version("1.2"), (1, 2, 0))

    def test_non_versions(self):
        self.assertIsNone(cs.version("latest"))
        self.assertIsNone(cs.version(None))
        self.assertIsNone(cs.version("sha256-abc"))

    def test_stable(self):
        self.assertTrue(cs.stable("v5.8.1"))
        self.assertFalse(cs.stable("v1.37.0-rc.1"))
        self.assertFalse(cs.stable("2.11.2-Chart6"))


class SplitImage(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(cs.split_image("traefik:v3.7.16"), ("docker.io", "library/traefik", "v3.7.16"))
        self.assertEqual(cs.split_image("docker.io/grafana/loki:3.7.9"), ("docker.io", "grafana/loki", "3.7.9"))
        self.assertEqual(cs.split_image("registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0"),
                         ("registry.k8s.io", "kube-state-metrics/kube-state-metrics", "v2.20.0"))
        self.assertEqual(cs.split_image("localhost:5000/app:1.0@sha256:ff"), ("localhost:5000", "app", "1.0"))
        self.assertEqual(cs.split_image("grafana/loki"), ("docker.io", "grafana/loki", ""))


class Judge(unittest.TestCase):
    def test_app_version_carried(self):
        img, why = cs.judge("v1.21.2", {"docker.io/grafana/alloy:v1.21.2", "quay.io/x/reloader:v0.1.0"}, set())
        self.assertEqual((img, why), ("docker.io/grafana/alloy:v1.21.2", "carries appVersion"))

    def test_loki_on_the_gel_chart_fails(self):
        # grafana/loki 7.5.0: appVersion 3.6.16, OSS image default 3.6.11 (#531).
        self.assertEqual(cs.judge("3.6.16", {"docker.io/grafana/loki:3.6.11"}, set()), (None, None))

    def test_pinned_tag_passes(self):
        img, why = cs.judge("v5.5.0", {"ghcr.io/unpoller/unpoller:v5.8.1"}, {"v5.8.1"})
        self.assertEqual(why, "tag pinned in values")


class Walkers(unittest.TestCase):
    def test_images_and_values_tags(self):
        pod = {"spec": {"containers": [{"image": "a:1"}], "initContainers": [{"image": "b:2"}]},
               "metadata": {"annotations": {"image": "c:3"}}}
        self.assertEqual(sorted(cs.images(pod)), ["a:1", "b:2", "c:3"])
        self.assertEqual(list(cs.values_tags({"image": {"tag": "v5.8.1"}, "x": [{"tag": 3}]})), ["v5.8.1", "3"])


class IndexEntries(unittest.TestCase):
    INDEX = (
        "apiVersion: v1\n"
        "entries:\n"
        "  alloy:\n"
        "  - appVersion: v1.21.2\n"
        "    version: 1.14.3\n"
        "  - appVersion: v1.21.1\n"
        "    version: 1.14.2\n"
        "  loki:\n"
        "  - appVersion: 3.6.16\n"
        "    version: 7.5.0\n"
        "generated: \"2026-10-25T00:00:00Z\"\n"
    )

    def test_slices_one_chart(self):
        self.assertEqual([e["version"] for e in cs.index_entries(self.INDEX, "alloy")], ["1.14.3", "1.14.2"])
        self.assertEqual([e["version"] for e in cs.index_entries(self.INDEX, "loki")], ["7.5.0"])
        self.assertEqual(cs.index_entries(self.INDEX, "tempo"), [])


class BearerParams(unittest.TestCase):
    def test_challenge(self):
        h = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:x:pull"'
        self.assertEqual(cs.bearer_params(h)["realm"], "https://auth.docker.io/token")
        self.assertEqual(cs.bearer_params(None), {})


if __name__ == "__main__":
    unittest.main()
