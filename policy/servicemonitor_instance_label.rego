package homelab.monitoring

import future.keywords.every
import future.keywords.if

# All pod-template-bearing workloads must have an `app` label on the pod template
# so that Alloy's pod discovery attaches it as a Loki stream label, making logs
# queryable by app name in Grafana.
workload_kinds := {"Deployment", "DaemonSet", "StatefulSet", "Job", "CronJob"}

deny contains msg if {
	workload_kinds[input.kind]
	not input.spec.template.metadata.labels.app
	msg := sprintf(
		"%s %q is missing required label app on pod template",
		[input.kind, input.metadata.name],
	)
}

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
