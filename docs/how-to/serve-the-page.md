# Serve the page

## Run it

```sh
quarterdeck serve
```

Open the address it prints, `http://127.0.0.1:8765/` by default. Use `--port`
to pick another port and `--title` to change the site name. To write the pages
as files instead, run `quarterdeck render`; see the
[CLI reference](../reference/cli-and-config.md#pages).

To submit a decision, see [Answer questions](answer-questions.md).

To enable the roster, see [Read the Workers page](read-workers.md).

## Reach it from a phone

Serve HTTPS on an address the phone can reach:

```sh
quarterdeck serve --host 192.0.2.10 --tls-cert cert.pem --tls-key key.pem
```

Item pages read in one column. You can pin Quarterdeck to your home screen
using its app icon and manifest. Read the security section below before using
a non-loopback address. See [how to open Hold on a phone](use-the-library.md).

## Run it in the background with systemd

```sh
quarterdeck service --host 127.0.0.1 --port 8765 --write
systemctl --user daemon-reload
systemctl --user enable --now quarterdeck.service
```

`quarterdeck service` without `--write` prints the unit instead. `quarterdeck url`
prints the address the installed unit serves. See
[Update Quarterdeck](update.md) for restarting it after an update.

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

To terminate TLS at the proxy and use HTTP to reach Quarterdeck, omit
`--tls-cert` and `--tls-key`, use an HTTP upstream, and have the proxy
overwrite `X-Forwarded-Proto` with the incoming request's scheme. Preserve
the browser-facing `Host` and the `/qd/` prefix.

## Security: loopback by default

The default bind is `127.0.0.1`. A loopback listener refuses requests whose
`Host` is outside loopback. Quarterdeck has no login. Anyone who can reach the
service can read saved items, their images, answers, conversations, reply
images, and unread Catch-up notices including their titles, summaries, links
and projects. They can also send answers, pin, resolve and archive conversations, keep items for later,
create and rename projects and topics, move conversations, references and question sets between them, acknowledge Firstmate
messages, and mark Catch-up notices read as the owner.
When `firstmate_home` is set, anyone who can reach the service can view the
home's worker and second mate roster and run a check from the Workers page
or `GET /workers.json`. They can also send asks to that home's inbox and, with the
[conversation prerequisites](talk-to-firstmate.md) met, start conversations
from the Desk, create and start tasks, and send messages.
Quarterdeck prints a warning when you bind a non-loopback address. Keep the
listener and proxy on the trusted network, or protect both with an owner access
gate. Browser submissions require an `Origin` that matches the public host and
scheme. Conversation starts, task creations and reviews, pins, resolutions, archives, later marks, project and topic changes,
moves, asks, reply acknowledgments and Catch-up acknowledgments use the same check. The served Desk
blocks framing by any page. A served item page allows framing only by a page of
the same origin, which is how the Desk shows the item beside its lists.
