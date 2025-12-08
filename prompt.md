# Product Specifications

## Outcome

* We're creating the scripts, configuration, and other artifacts required to bootstrap a containerized homelab from a bare-metal OS install

## Role

* You are an experienced devops engineer communicating with an experienced software developer.

## Context

* The homelab is hosted on a single Raspberry Pi 4B with 8 gigabytes of RAM and 256 gigabytes of SSD storage, running 64 bit Raspberry Pi OS Lite 
* The homelab will be used
    * to host services for internal use, like PiHole
    * for learning and experimentation with K8s
    * maintain Linux administration skills

## Instructions

* Use best practices at all times. The scope of best practices includes but is not limited to security, reliability, maintainability, and scalability.
* BRIEFLY explain all decisions in a highly technical context. Especially explain trade offs versus alternative approaches.
* All artifacts will be kept in git-based source control.
* All scripts will be constructed to be repeatable, idempotent, and self-documenting. One-time bootstrapping scripts should be separated from the ongoing operational scripts.

### Configuration and Secrets
* Sensitive information and secrets must be injected during the deploy process. Generate the necessary schema that the items can be added during the deployment but insert dummy values. Do not store any configuration or secrets in the repository.
* Sensitive information includes, but is not limited to:
    * Internal network information, like domains, IP addresses and masks
    * Usernames and passwords
    * SSH and API keys
    * certificates

## Success criteria

### K3s deployment
* K3s is installed using the official installation script from https://get.k3s.io
* K3s is configured to support multiple containers defined through manifest-type files or Helm charts. 
* K3s is configured to default allow container access via URI. In the case of PiHole, since clients require access via a network-routable IP, K3s will be configured to allow access to PiHole via IP.
* Each deployment is separated into its own namespace. For example, all resources for the PiHole deployment should be grouped into the `pihole` namespace, all resources for the Unbound deployment in the `unbound` namespace, etc.
* Continuious deployment is handled by Flux CD.
* K3s is configured to use the local path storage provider to support per-deployment persistent storage. The total amount of storage allocated to K3s will not exceed 100 gigabytes.
* Containers automatically start at boot, and automatically restart if they fail.
* Uses Flux CD to manage Gitops.

### Flux CD deployment
* Flux CD is installed using the official installation script through Homebrew
* Flux CD will support executing post-deployment scripts to configure the containers after they are deployed.
* Flux CD will monitor the repo at https://github.com/timgladwell/homelab. Any updates to the repo will trigger an automatic deployment, including (but not limited to) manifest changes, configuration changes, shell script changes, and post-deployment job changes.

### Unbound deployment
* Unbound will be responsible for querying upstream DNS servers. Unbound will answer queries from the PiHole deployment only - queries from any other source will be ignored.
* Image will be pulled from the image tagged "latest" at https://hub.docker.com/r/klutchell/unbound
* Unbound will be configured to perform **recursive** DNS queries.
* Unbound will use DNSSEC.

### PiHole deployment
* The PiHole container must be accessible by IP in order to act as the DNS server for the internal network.
* PiHole will use the Unbound deployment as the sole upstram DNS provider. For context, the network firewall is configured to block any DNS requests that are not targeted to PiHole's IP.
* Image is pulled from the image tagged "latest" at https://hub.docker.com/r/pihole/pihole
* PiHole adlist subscriptions are kept up-to-date via a FluxCD post-deployment job - see "PiHole post-deployment steps" below. A single representation of the adlist subscription configuration data will be kept in a `ConfigMap` so changes to the adlist subscription data can trigger a FluxCD deployment.
* Map the container's "/etc/pihole" path to the persistent volume on the K3s host.

#### PiHole configuration
* Upstream DNS server is the Unbound deployment only.
* Receive and respond to DNS queries from local networks only. The local network architecture consists of multiple VLANs that are >1 hop away from PiHole, so use `SINGLE` interface listening mode
* Let PiHole automatically configure the physical listening interface
* Use the default "level 0" privacy level - logs should show everything.
* Disable UI password
* Disable special domains and other DNS workarounds, including (but not limited to):
    * disable iCloud private relay
    * disable Mozilla Canary
    * disable designated Resolver
* Disable DHCP server
* Timezone is Toronto, Ontario, Canada
* Reverse server (conditional forwarding) is configured to forward DNS queries for the local /16 subnet to a target server for the given local domain. This takes the form of:
    * "<enabled>,<cidr-ip-address-range>,<server-ip-address>,<domain>"
    * using the following substitution rules
        * substitute <enabled> with `true` - reverse server configuration should always be active
        * substitute <cidr-ip-address-range> with the CIDR-formatted address range of IPs applicable for reverse server lookups
        * substitute <servier-ip-address> with the target reverse server's IP
        * substitue <domain> with the local search domain


#### PiHole post-deployment steps
* Flux CD will be responsible for executing a post-deployment step against the PiHole container. The script will not be started until the PiHole instance is up and running.
* Executing this step is required whenever the PiHole container is deployed or when the adlist subscription information is updated.
* The purpose of this post-deployment step is to keep the adlist subscriptions up-to-date and will consist of 3 steps:
    1. Send a POST request to update the allowlist subscriptions. The `curl` equivalent to this request looks like
        * curl -v -i -H "Content-Type: application/json" -X POST -d @<allow_list_json_file> http://<pihole_instance_ip_address>/api/lists\?type\=allow
        * using the following substitution rules
            * substitute <allow_list_json_file> with the relative path to the allow adlist subscriptions stored in the `ConfigMap`
            * substitute <pihole_instance_ip_address> with the IP address of the PiHole instance
    2. Send a POST request to update the blocklist subscriptions. The `curl` equivalent to this request looks like
        * curl -v -i -H "Content-Type: application/json" -X POST -d @<block_list_json_file> http://<pihole_instance_ip_address>/api/lists\?type\=block
        * using the following substitution rules
            * substitute <block_list_json_file> with the relative path to the block adlist subscriptions stored in the `ConfigMap`
            * substitute <pihole_instance_ip_address> with the IP address of the PiHole instance
    3. Send a POST request to trigger processing of the updated adlist subscriptions
        * curl -v -i -H "Content-Type: application/json" -X POST http://<pihole_instance_ip_address>/api/action/gravity\?color\=true
        * using the following substitution rules
            * substitute <pihole_instance_ip_address> with the IP address of the PiHole instance