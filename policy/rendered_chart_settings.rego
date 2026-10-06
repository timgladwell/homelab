package homelab.rendered

import future.keywords.if

# Settings whose silent loss costs something, asserted against chart-rendered
# output (validation step 15). Only that step evaluates this package: these
# objects do not exist in the kustomize build, where step 8 would find nothing.
#
# The failure these catch is a chart upgrade that stops reading a values key.
# The values file still renders cleanly, the pod starts healthy, and the
# setting falls back to a chart default — which is how Loki ran on 744h
# retention for months while its values file said otherwise (#213). So each
# rule asserts the setting is *present* in the rendered object, not what it
# is: the value itself stays reviewable in the HelmRelease, and changing it
# needs no edit here.
#
# Each rule is conditional on the object existing, because not every site runs
# every chart. That means renaming the object would skip its rule silently;
# the names below are the charts' own, and changing one is a chart-values
# change this step exists to be looked at alongside.

# Loki: limits_config is only rendered from `loki.limits_config` (snake_case,
# chart v7+). The v6 spelling `limitsConfig` rendered nothing at all.
deny contains msg if {
	input.kind == "ConfigMap"
	input.metadata.name == "loki"
	config := yaml.unmarshal(input.data["config.yaml"])
	not config.limits_config.retention_period
	msg := "Loki's rendered config has no limits_config.retention_period — the chart is not reading the retention set in sites/akron/monitoring/loki.yaml, so Loki falls back to its default"
}

# Prometheus: without the out-of-order window, samples replayed from a remote
# site's collector WAL after an outage are rejected as out of bounds. Must stay
# in sync with the collectors' wal.max_keepalive_time (base/metrics-collection/alloy-metrics.alloy).
deny contains msg if {
	input.kind == "Prometheus"
	not input.spec.tsdb.outOfOrderTimeWindow
	msg := sprintf(
		"Prometheus %q has no spec.tsdb.outOfOrderTimeWindow — replayed remote-write data from other sites will be rejected after an outage",
		[input.metadata.name],
	)
}
