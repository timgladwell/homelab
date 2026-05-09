package homelab.monitoring

import future.keywords.every
import future.keywords.if

# Every endpoint in a ServiceMonitor must have a relabeling rule that sets
# a human-readable `instance` label. Without this, Prometheus records
# instance="<pod-ip>:<port>", which is unreadable in Grafana dashboards.
deny contains msg if {
	input.kind == "ServiceMonitor"
	some i
	endpoint := input.spec.endpoints[i]
	not endpoint_has_instance_relabeling(endpoint)
	msg := sprintf(
		"ServiceMonitor %q endpoint[%d] is missing a relabeling rule with targetLabel: instance",
		[input.metadata.name, i],
	)
}

# Every endpoint in a PodMonitor must have the same relabeling rule.
deny contains msg if {
	input.kind == "PodMonitor"
	some i
	endpoint := input.spec.podMetricsEndpoints[i]
	not endpoint_has_instance_relabeling(endpoint)
	msg := sprintf(
		"PodMonitor %q podMetricsEndpoints[%d] is missing a relabeling rule with targetLabel: instance",
		[input.metadata.name, i],
	)
}

endpoint_has_instance_relabeling(endpoint) if {
	some rule in endpoint.relabelings
	rule.targetLabel == "instance"
}
