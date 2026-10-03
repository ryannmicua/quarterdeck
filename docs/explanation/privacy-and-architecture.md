# Privacy and architecture

Quarterdeck is a small local renderer, not a Firstmate extension. A render can
use one home from `--home` or `FM_HOME`, or several independent homes from the
`homes` array in a config file. For each home it reads the configured data
directory and adds a labeled section to one self-contained page.
`FM_DATA_OVERRIDE` selects a non-default data directory for the legacy
single-home invocation. In a home list, each entry may set its own `data_dir`;
otherwise that home's `data/` directory is used.

The inputs are `data/backlog.md` and `data/<id>/report.md` files. For each
configured parent home, Quarterdeck also reads the optional
`data/secondmates.md` route registry. Each local route's `home:` field points
to another Firstmate home, which gets its own labeled section underneath the
parent. That secondmate has its own `data/backlog.md` and report files. A remote
route is shown as unavailable: Quarterdeck does not use SSH or read another
machine. This follows Firstmate's [secondmate route and home layout](https://github.com/kunchenguid/firstmate/blob/main/docs/configuration.md).

Quarterdeck reads these files directly and does not invoke Firstmate scripts or
commands. This keeps rendering within the read-only boundary even when an
observational fleet command refreshes a cache as part of its own operation.
Each configured home is loaded independently. A missing or unreadable home
shows its own error while other sections still render.

The page may include task names, recommendations, and local paths from every
selected home. Its default location is under
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`, outside the repo. Keep any
custom output and real config outside the repo as well. The page uses inline
CSS and makes no network requests. There is no daemon, token, or write-back
path.

The repository ignores common output folders, image screenshots, and local
configuration files. The staged-file privacy guard rejects generated HTML,
Firstmate data files, likely host-specific paths, private IPs, and pull-request
URLs. The guard is a backstop; examples and tests still need to remain fictional.
