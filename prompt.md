# Outcome

* We're creating the scripts, configuration, and other artifacts required to bootstrap a containerized homelab from a bare-metal OS install

# Role

* You are an experienced devops engineer communicating with an experienced software developer.

# Context

* The homelab is hosted on a single Raspberry Pi 4B with 8 gigabytes of RAM and 256 gigabytes of storage, running 64 bit Raspberry Pi OS Lite 
* The homelab will be used
    * to host services for internal use, like PiHole
    * for learning and experimentation with K8s
    * maintain Linux administration skills

# Instructions

* BRIEFLY explain all decisions in a highly technical context. Especially explain trade offs versus alternative approaches.
* All artifacts will be kept in git-based source control.
* All scripts will be constructed to be repeatable, idempotent, and self-documenting.
* All configurable parameters will be stored in a single configuration file.

# Success criteria

1. The homelab uses K3s to manage containers
    * K3s is configured to use the local path storage provider.
    * Containers automatically start at boot, and automatically restart if they fail.
    * The PiHole instance is accessible from a static IP in order to be the configured DNS server for the internal network. The static IP is assigned by the DHCP server.

2. The homelab hosts a PiHole deployment
    * PiHole container image from https://hub.docker.com/r/pihole/pihole
    * PiHole container documentation and best practices from the repo https://github.com/pi-hole/docker-pi-hole
    * PiHole application documentation and best practices from https://docs.pi-hole.net/
    * Upstream DNS server is Cloudflare, 1.1.1.1 and 1.0.0.1
    * Use DNSSEC
    * Receive and respond to DNS queries from all local networks
    * Reverse server active and set to the domain DHCP server for the entire /16 subnet and the `home.arpa` domain
    * Disable UI password
    * PiHole allow list domains from the pihole_allow_lists.txt file
    * PiHole block list domains from the pihole_block_lists.txt file