# TNI Signage

Web player for The NODE Institute's signage screens, served by GitHub Pages. An iiSignage²
playlist shows `index.html` full screen in a single Web page element.

- `index.html` plays the slides in `slides.json` in order. It re-reads the list every round,
  switches only to a fully loaded set, and keeps playing the last set when offline.
  Add `?debug` to the URL to show a status line.
- `sw.js` caches the page and the slides for offline playback.
- `slides/` holds the slide images, named by content hash.

Publish a new set from `Print_Poster_Scripts\indesign-mcp`:

    .\.venv\Scripts\python.exe -X utf8 publish_signage_web.py

This publishes the PNGs in `SIGNAGE\Signage_Slides_Play`, in file-name order.
