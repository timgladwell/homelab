My homelab will contain multiple web-based apps (including dashboards for things like Traefik). I want to ensure that there's no route / name collisions between each app, and I do NOT want to use custom / non defaualt port numbers (in other words, web apps use standard port 80 / port 443).

In order to do this, I want each app "namespaced" under a route specific to each web app. This way I don't have to remember port numbers and there's no chance that multiple apps are all trying to mount the same http://<IP ADDRESS>/index.html.

Note that each app should keep its default behaviour for the default route. 

## Examples

### App-specific routes

* <IP ADDRESS> represents the network-accessible IP address of the Raspberry Pi
* <DOMAIN NAME> represents the domain name of the Raspberry Pi

| name       | repo path                       |  ip-based route                | domain-based route              | subdomain-based route           |
|------------|---------------------------------|--------------------------------|---------------------------------|---------------------------------|
| podman     | /apps/homelab/podman            | http://<IP ADDRESS>/podman     | http://<DOMAIN NAME>/podman     | http://podman.<DOMAIN NAME>     |
| pihole     | /apps/homelab/pihole            | http://<IP ADDRESS>/pihole     | http://<DOMAIN NAME>/pihole     | http://pihole.<DOMAIN NAME>     |
| custom_app | /apps/homelab/custom_app        | http://<IP ADDRESS>/custom_app | http://<DOMAIN NAME>/custom_app | http://custom_app.<DOMAIN NAME> |
| traefik    | /infrastructure/homelab/traefik | http://<IP ADDRESS>/traefik    | http://<DOMAIN NAME>/traefik    | http://traefik.<DOMAIN NAME>    |

### Maintaining default behaviour

* Traefik automatically redirects to `/dashboard`
* PiHole shows a HTTP 403 error page with UI to tell the user to request `/admin`

## Tasks

1. Modify the Traefik configuration, ingresses, and middleware to reflect the modified requirements above
2. Update the documentation to reflect the new approach 