# Roadmap

Status labels: **validated need**, **hypothesis**, **engineering**, **speculative**.
Only the mission's initial workflow is treated as validated enough to build; real
user discovery must confirm priorities after alpha.

## Alpha v0.1.0

- **validated need**: sample-first workspace/project and safe multi-file upload.
- **validated need**: EDF/BDF/BrainVision/EEGLAB/FIF/configured CSV ingestion with
  immutable originals, useful errors, and honest real-data matrix.
- **validated need**: scalable waveform windows/pyramid/Canvas viewer, metadata,
  events, channel controls, pan/zoom/jump/scale.
- **validated need**: attributed annotations, human bad-channel state, transparent
  QC flags, processing provenance, and useful exports.
- **engineering**: workspace RBAC, deletion, observability, privacy/security gates,
  containers, CI, deployment/restore docs, extension registries.

## Post-alpha candidates

- **hypothesis**: complete zipped/directory BIDS import and BIDS derivative export
  are a primary adoption driver.
- **hypothesis**: shareable read-only review links and comments materially shorten
  researcher collaboration cycles.
- **hypothesis**: spectra and topography are needed inside the workspace rather
  than downstream MNE/EEGLAB.
- **engineering**: binary waveform protocol/CDN delivery and WebGL renderer if
  measurements miss targets.
- **engineering**: GDF/CNT/XDF/EGI adapters chosen by failed real uploads.
- **speculative**: synchronized video, live OpenBCI/Muse/device streams, multimodal
  timebases, learned artifact candidates, embeddings/search.
- **speculative**: SSO/SCIM, regional storage, advanced retention, audit export,
  private deployment; build only with commercial demand and security ownership.

## Discovery questions

1. What is the last step before a recording becomes understandable, and where is
   time lost today?
2. Which actual file/device variants fail or require manual repair?
3. How do teams mark and reconcile bad channels/segments, and who decides?
4. Which transformations must be previewed versus persisted?
5. Which exact downstream artifact makes the workspace valuable?
6. What sharing/privacy constraints prevent use of existing tools?
7. At what recording sizes/channels does the current workflow become unusable?
8. What would make a user return weekly rather than use the viewer once?

Feature proposals record interview/evidence links, frequency, affected workflow,
current workaround, willingness to switch/pay/contribute, and a falsifiable success
metric. Lack of repeated evidence is grounds to defer or remove.

