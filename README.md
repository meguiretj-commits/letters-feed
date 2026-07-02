# Shareholder Letters RSS Feed

RSS feed of shareholder letters from Berkshire Hathaway, JPMorgan (Jamie Dimon), Amazon, Markel Group, and Constellation Software.

**Subscribe in any RSS reader:**

    https://raw.githubusercontent.com/meguiretj-commits/letters-feed/main/feed.xml

A GitHub Action re-runs `letters_feed.py` on the 1st and 15th of each month and commits the updated `feed.xml` when a company publishes a new letter. Letters with unknown publication dates get an estimated date (March 1 of the year after the letter year), capped at discovery time.

To add a company, edit `companies.json` (see the two adapter strategies: `probe` for predictable URL patterns, `parse_index` for plain-HTML archive pages). To run locally: `python3 letters_feed.py` (stdlib only, Python 3.9+).
