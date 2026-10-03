# Privacy and architecture

Quarterdeck is a small local renderer, not a Firstmate extension. A render
starts from an explicitly selected home (`--home` or `FM_HOME`), reads the
configured data directory, and writes one self-contained page. `FM_DATA_OVERRIDE`
selects a non-default data directory; otherwise the home's `data/` directory is
used.

The inputs are `backlog.md` and `data/<id>/report.md` files. Quarterdeck reads
these files directly and does not invoke Firstmate scripts or commands. This
keeps rendering within the read-only boundary even when an observational fleet
command refreshes a cache as part of its own operation.

The page may include task names, recommendations, and local paths. Its default
location is under `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`, outside the
repo. Keep any custom output and real config outside the repo as well. The page
uses inline CSS and makes no network requests. There is no daemon, token, or
write-back path.

The repository ignores common output folders, image screenshots, and local
configuration files. The staged-file privacy guard rejects generated HTML,
Firstmate data files, likely host-specific paths, private IPs, and pull-request
URLs. The guard is a backstop; examples and tests still need to remain fictional.
