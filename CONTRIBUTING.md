# Contributing

Quarterdeck is a small, dependency-free Python tool. Keep changes focused and
stdlib-only, and keep all fixtures fictional. Never add real saved items, paths,
screenshots, hostnames, or project names.

Quarterdeck has no CI; validation relies on the local no-mistakes pipeline.

Before opening a pull request, run:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/privacy_guard.py --staged
```

The privacy guard checks staged paths and contents. It rejects the item store,
its lock, and the local visit log even if force-staged, as well as generated
pages, host-specific paths, private IPs, pull request links and ticket-shaped
keys. Keep generated pages and local configuration outside the repository.
