# Contributing

Quarterdeck is a small, dependency-free Python tool. Keep changes focused and
stdlib-only, and keep all fixtures fictional. Never add real Firstmate backlog
entries, reports, paths, screenshots, hostnames, or project names.

Before opening a pull request, run:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/privacy_guard.py --staged
```

The privacy guard checks staged paths and staged file contents. Keep generated
pages and local configuration outside the repository.
