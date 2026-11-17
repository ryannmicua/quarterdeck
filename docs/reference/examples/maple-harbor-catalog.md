# Maple Harbor catalog launch

Now: The catalog import works; PR 42 is ready to merge and the image host is waiting on the vendor.
Waiting on: you - merge PR 42
Waiting on: Sample vendor - a storage quota for the product images
Pieces: maple-21, paper-27, blocked-33
Links: [Catalog preview](https://example.com/maple-harbor/preview), data/amber-18/report.md
Status: open

## 2026-03-02 08:12 | you | chat
Get the Maple Harbor catalog live before the spring sale. Keep the old product pages until it is.

## 2026-03-02 08:20 | firstmate | chat
Started. I split it into three pieces: the import (maple-21), the patch that switches the pages (paper-27), and the image storage, which waits on the vendor (blocked-33).

## 2026-03-03 14:05 | firstmate | chat
Id: merge-42
Decision: Merge PR 42 now?
Options: Merge | Hold | Not now
Recommend: Merge

The patch switches the product pages to the new catalog. Tests pass and the old pages stay reachable for a week.

## 2026-03-03 14:30 | you | quarterdeck
Id: ask-3f9c2a1b
Will the old links still work after the switch?

## 2026-03-03 14:34 | firstmate | chat
Re: ask-3f9c2a1b
Yes. Every old address redirects to the matching new page for 90 days, and the report lists the ten most visited ones.

## 2026-03-04 09:00 | firstmate | chat
Id: images-host
Decision: Wait for the vendor's quota, or host the images ourselves for now?
Options: Wait | Host ourselves
Recommend: Wait

Hosting them ourselves costs a day of work that we throw away once the quota arrives.

## 2026-03-04 09:40 | you | chat
Re: images-host
Wait. Ask the vendor again on Friday.
