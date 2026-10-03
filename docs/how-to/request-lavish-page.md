# Request a Lavish page

Use the request control when a report or backlog item would be easier to
review as its own Lavish page.

1. Render the Lavish review page:

   `quarterdeck render --lavish`

2. Open the printed session URL and find a held, review-ready, or in-flight
   card.
3. Select **Request a Lavish page**. Quarterdeck queues a prompt containing the
   item ID, title, source kind, home label, and recorded source path.
4. Send the queued prompt to the agent from Lavish's conversation panel.

An armed listener must be active on that same session to receive the request.
For example, the Firstmate that owns the home can listen for requests, build the
page from the supplied source, and reply with its link in the session's
conversation panel. Quarterdeck only queues the prompt: it does not send it,
listen, poll, create the page, or display a returned link on the dashboard.
Nothing happens unless a listener is armed and the queued prompt is sent.

The control exists only on the Lavish page. A plain `quarterdeck render`
page has no request control; its report and backlog links open readable HTML
copies beside the main page.
