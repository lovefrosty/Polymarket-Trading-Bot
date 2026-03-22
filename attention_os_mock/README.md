# Attention OS Mock

Standalone prototype for the Attention OS side project.

This folder is intentionally separate from the repo's existing `dashboard/` app.

## Open It

Simplest option:

- Open [index.html](/Users/padraigjudge/Desktop/Polymarket%20Bot/attention_os_mock/index.html)

If you want to serve it locally:

```bash
cd "/Users/padraigjudge/Desktop/Polymarket Bot/attention_os_mock"
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

## Routes

- [index.html](/Users/padraigjudge/Desktop/Polymarket%20Bot/attention_os_mock/index.html): weekly reflection home
- [graph.html](/Users/padraigjudge/Desktop/Polymarket%20Bot/attention_os_mock/graph.html): attention map
- [timeline.html](/Users/padraigjudge/Desktop/Polymarket%20Bot/attention_os_mock/timeline.html): rabbit-hole replay
- [drift.html](/Users/padraigjudge/Desktop/Polymarket%20Bot/attention_os_mock/drift.html): goals vs behavior
- [interventions.html](/Users/padraigjudge/Desktop/Polymarket%20Bot/attention_os_mock/interventions.html): intervention studio
- [sources.html](/Users/padraigjudge/Desktop/Polymarket%20Bot/attention_os_mock/sources.html): sources, transparency, and privacy

## Shared Assets

- `data.js`: centralized mock app state and content contract
- `app.js`: route rendering, lineage modal, local state, and interactions
- `styles.css`: shared visual system and layout

## Prototype Behaviors

- the selected time range persists across routes
- graph-node and replay selection persist across routes
- insight feedback is stored in local state
- intervention toggles are simulated in local state
- export and delete actions are mocked, not real
