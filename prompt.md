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
* K3s is configured to support multiple containers. Containers are defined through manifest-type files or Helm charts. Containers are to be accessible via URI (and, in the case of a DNS server like PiHole, also via IP).
* Continuious deployment is handled by Flux CD.
* K3s is configured to use the local path storage provider to support per-container persistent storage. The total amount of storage allocated to K3s will not exceed 100 gigabytes.
* Containers automatically start at boot, and automatically restart if they fail.
* Uses Flux CD to manage Gitops.

### Flux CD deployment
* Flux CD is installed using the official installation script through Homebrew
* Flux CD will monitor the repo at https://github.com/timgladwell/homelab. Any updates to the repo will trigger an automatic deployment.
* Flux CD will support executing post-deployment scripts to configure the containers after they are deployed.

### PiHole deployment
* The PiHole container must be accessible by IP in order to act as the DNS server for the internal network. 
* Image is pulled from the image tagged "latest" at https://hub.docker.com/r/pihole/pihole
* Map the container's "/etc/pihole" path to the persistent volume on the K3s host.

#### PiHole configuration
* Upstream DNS server is Cloudflare, 1.1.1.1 and 1.0.0.1
* Use DNSSEC
* Receive and respond to DNS queries from all local networks
* Reverse server (conditional forwarding) is configured to forward DNS queries for the entire /16 subnet to the gateway IP address for the given local domain.
* Disable UI password
* Disable private relays like iCloud private relay
* Disable DHCP server
* Timezone is Toronto, Ontario, Canada

#### PiHole post-deployment steps
* Flux CD will be responsible for executing a post-deployment step against the PiHole container. The script will not be started until the PiHole instance is up and running.
* The purpose of this post-deployment step is to keep the adlist subscriptions up-to-date and will consist of 3 steps:
    1. Send a POST request to update the allowlist subscriptions. The `curl` equivalent to this request looks like
        * curl -v -i -H "Content-Type: application/json" -X POST -d @%%ALLOW_LIST_JSON_FILE%% http://%%PIHOLE_INSTANCE_IP_ADDRESS/api/lists\?type\=allow
        * using the following substitution rules
            * substitute %%ALLOW_LIST_JSON_FILE%% with the relative path to the `pihole_allow_lists.json" file in this repo
            * substitute %%PIHOLE_INSTANCE_IP_ADDRESS%% with the IP address of the PiHole instance
    2. Send a POST request to update the blocklist subscriptions. The `curl` equivalent to this request looks like
        * curl -v -i -H "Content-Type: application/json" -X POST -d @%%BLOCK_LIST_JSON_FILE%% http://%%PIHOLE_INSTANCE_IP_ADDRESS/api/lists\?type\=block
        * using the following substitution rules
            * substitute %%BLOCK_LIST_JSON_FILE%% with the relative path to the `pihole_block_lists.json" file in this repo
            * substitute %%PIHOLE_INSTANCE_IP_ADDRESS%% with the IP address of the PiHole instance
    3. Send a POST request to trigger processing of the updated adlist subscriptions
        * curl -v -i -H "Content-Type: application/json" -X POST http://%%PIHOLE_INSTANCE_IP_ADDRESS%%/api/action/gravity\?color\=true
        * using the following substitution rules
            * substitute %%PIHOLE_INSTANCE_IP_ADDRESS%% with the IP address of the PiHole instance