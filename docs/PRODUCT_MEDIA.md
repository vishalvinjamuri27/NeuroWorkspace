# Product media

## Checked-in screenshot

`docs/assets/neuroworkspace-recording-workspace.png` is a live capture of the
checksum-pinned OpenNeuro public sample. It shows real application state after
zoom, pan, channel visibility, and QC interactions. The sample attribution remains
visible in the one-minute guide; no subject field is displayed.

## Reproducible demo

Start a local deployment with the public sample, then run:

```bash
pnpm --filter @neuroworkspace/web media:capture
```

Set `NEUROWORKSPACE_MEDIA_URL` when the app is not at
`http://localhost:3000`. The script uses browser roles/labels rather than
coordinates where possible and creates:

- `docs/assets/neuroworkspace-recording-workspace.png`;
- `docs/assets/neuroworkspace-alpha-demo.webm`.

The recording follows landing → public sample → waveform → zoom/pan → toggle
channel → QC. Because the shared sample is intentionally read-only, it then moves
through the normal local sign-in/import flow with a generated, non-human CSV,
creates an annotation in that owned recording, and returns to QC. It never
manufactures an editable public-sample state.

For a GIF, convert the generated WebM only when `ffmpeg` is available:

```bash
ffmpeg -i docs/assets/neuroworkspace-alpha-demo.webm \
  -vf "fps=12,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
  -loop 0 docs/assets/neuroworkspace-alpha-demo.gif
```

Review the result before committing it. Keep the first useful interaction visible,
trim idle processing time, retain the non-clinical label, and do not capture
participant data, browser chrome containing personal information, tokens, or
operator dashboards. A short WebM is usually smaller and clearer than a GIF.

For a narrated video, record the same steps at 1440×1000, explain that the sample
is public/read-only, and use generated data for mutation. Do not claim clinical
interpretation or hide loading/error behavior.
