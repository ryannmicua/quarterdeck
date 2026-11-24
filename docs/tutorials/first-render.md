# Your first Quarterdeck page

This walks through saving three items and opening the page. It uses invented
examples.

1. Install Quarterdeck; see [Install Quarterdeck](../how-to/install.md).

2. Save a link:

   ```sh
   quarterdeck add --kind resource --title "Harbor preview" \
     --description "The preview of the harbor map, saved to check the labels later." \
     --url https://example.com/harbor/preview --link-text "Harbor map preview"
   ```

   It prints `Q1`.

3. Save a list from standard input:

   ```sh
   printf '%s\n' "Buy rope" "Paint the hull" | quarterdeck add --kind list \
     --title "Harbor chores" --description "Chores for the weekend." --body-file -
   ```

   It prints `Q2`.

4. Run `quarterdeck list`. You see Q2 above Q1: newest first.

5. Run `quarterdeck serve` and open `http://127.0.0.1:8765/`. The Saved table
   shows both items. Open Harbor chores: its entries are numbered Q2.1 and Q2.2.


Next, read [Save items to Quarterdeck](../how-to/save-items.md) for open
questions, and [Serve the page](../how-to/serve-the-page.md) to keep
it running.
