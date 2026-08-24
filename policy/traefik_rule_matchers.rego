package homelab.traefikrules

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# Traefik v3 removed multi-value rule matchers:
#
#   "All matchers now take a single value (except Header, HeaderRegexp, Query
#    and QueryRegexp which take two) and should be explicitly combined using
#    logical operators to mimic previous behavior."
#
# So Host(`a`, `b`) is v2 syntax. In v3 the rule fails to parse, the router is
# never created, and every request that would have matched it gets Traefik's
# own 404 — in text/plain, which reads exactly like a broken backend rather
# than a broken rule. Nothing else in the pipeline can see it: the match is an
# opaque string to kubeconform and kube-score, and Flux applies the CRD
# happily. #303 shipped it on all four app routes and it was only found by
# curl-ing the live site.
#
# Header and its friends legitimately take two values, so they are exempt.
two_value_matchers := {"Header", "HeaderRegexp", "Query", "QueryRegexp"}

# Matcher calls whose argument list holds more than one backtick-quoted value.
# Anchored on the backticks rather than on commas so that a comma *inside* a
# value (a regex quantifier, say) does not read as a second argument.
multi_value(rule) := matcher if {
	some m in regex.find_n(`[A-Za-z]+\([^)]*\)`, rule, -1)
	matcher := regex.find_n(`^[A-Za-z]+`, m, 1)[0]
	not matcher in two_value_matchers
	count(regex.find_n("`[^`]*`", m, -1)) > 1
}

deny contains msg if {
	input.kind == "IngressRoute"
	some route in input.spec.routes
	matcher := multi_value(route.match)
	msg := sprintf(
		"IngressRoute %q in namespace %q uses Traefik v2 multi-value matcher syntax in %q — %s() takes a single value in v3. Combine with || instead: %s(`a`) || %s(`b`). A v2-style rule does not error; the router is silently never created and every request to it 404s.",
		[input.metadata.name, input.metadata.namespace, route.match, matcher, matcher, matcher],
	)
}
