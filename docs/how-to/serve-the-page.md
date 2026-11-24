# Serve the page

## Run it

```sh
quarterdeck serve
```

Open the address it prints, `http://127.0.0.1:8765/` by default. Use `--port`
to pick another port and `--title` to change the site name. To write the pages
as files instead, run `quarterdeck render`; see the
[CLI reference](../reference/cli-and-config.md#pages).

Open a question under Needs attention or select a title under References.

## Reach it from a phone

Serve HTTPS on an address the phone can reach:

```sh
quarterdeck serve --host 192.0.2.10 --tls-cert cert.pem --tls-key key.pem
```

The page fits a phone: the table rows stack, and the item pages read in one
column. The page names an icon and a manifest, so a phone can pin it to the
home screen. A non-loopback address has no login; read the security section
below first.

## Run it in the background with systemd

```sh
quarterdeck service --host 127.0.0.1 --port 8765 --write
systemctl --user daemon-reload
systemctl --user enable --now quarterdeck.service
```

`quarterdeck service` without `--write` prints the unit instead. `quarterdeck url`
prints the address the installed unit serves. After `quarterdeck update`,
restart the service with `systemctl --user restart quarterdeck.service`.

## Serve it under a path prefix behind a reverse proxy

A reverse proxy on the standard HTTPS port can forward one path, such as
`/qd/`, to Quarterdeck. Tell Quarterdeck the prefix with `--base-path /qd` (or
`"base_path": "/qd"` in the config; the flag wins):

```sh
quarterdeck serve --host 192.0.2.10 --port 8765 --tls-cert cert.pem --tls-key key.pem --base-path /qd
```

A Caddy example, with placeholder names. Point the proxy at a non-loopback
address Quarterdeck listens on: a server bound only to loopback refuses a
request whose `Host` is not loopback.

```caddyfile
dashboard.example.org {
    redir /qd /qd/?{query} 308
    handle /qd* {
        reverse_proxy https://192.0.2.10:8765 {
            header_up Host {hostport}
            transport http {
                tls_insecure_skip_verify  # Quarterdeck's own certificate is self-signed
            }
        }
    }
}
```

`quarterdeck service --base-path /qd` writes the option into the unit.

## Security: loopback by default

The default bind is `127.0.0.1`. A loopback listener refuses requests whose
`Host` is outside loopback. Binding a non-loopback address exposes your saved
items without a login to everyone who can reach the port, and Quarterdeck
prints a warning when you do. Anyone who can reach the service can read items and send answers as the owner. Keep the listener and proxy on the trusted network, or protect both with an owner access gate. Browser submissions require a matching Origin and Host. The proxy must preserve the external Host and scheme.
