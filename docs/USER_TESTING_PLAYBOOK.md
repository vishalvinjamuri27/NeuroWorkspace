# User-testing playbook

## Goal

Run a 20–30 minute observed session that reveals whether an EEG user can reach
value without a tour. Test behavior, not stated enthusiasm. Use public,
synthetic, or appropriately deidentified data only. Never record a screen,
recording, or participant without explicit permission.

## Before the call

- Deploy the exact build under test and record its `BUILD_VERSION`.
- Verify landing → sample, hosted sign-in, upload, worker, feedback, and export.
- Ask the participant to bring a supported, non-identifiable recording they are
  allowed to use, or use the attributed public sample and prepared CSV fixture.
- Copy `docs/templates/USER_SESSION.md` to a private research-notes location.
  Do not commit participant identity or sensitive file details.
- Prepare a fallback alpha account through the normal OIDC provider. Do not share
  passwords or bypass workspace authorization.
- Ask permission for note-taking and, separately, for any recording.

## Facilitation

Say: “I’m testing the product, not you. Please think aloud. I will stay quiet
unless you are blocked for about two minutes or encounter a safety problem.”

Do not give a feature tour. Start at the landing page and ask:

1. **Open this EEG recording and tell me what you notice.**
2. **Find a particular time range.**
3. **Hide channels you do not care about.**
4. **Mark a segment you consider problematic.**
5. **Mark a bad channel.**
6. **Look at automatic QC and explain what you think it means.**
7. **Export or save whatever you would normally need next.**
8. Ask the participant to leave the recording, return through the workspace, and
   continue where they left off.

When the shared sample is used, explain only if needed that it is read-only; move
to the participant-owned/deidentified fixture for annotation, bad-channel, and
export tasks. This avoids implying public data belongs to them.

Observe first click, hesitations, wrong turns, terminology, loading tolerance,
whether errors lead to recovery, whether workspace/project/recording hierarchy is
understood, and what they attempt without prompting. Record exact safe error
reference IDs, never files or scientific contents.

## Closing questions

- What felt confusing?
- What would you normally do next?
- What did you expect to exist but could not find?
- What would make you come back tomorrow?
- What would stop you from using this on real data?
- What tool would this replace, if any?
- What would you still need Python, MATLAB, MNE, or EEGLAB for?
- Which part of your current workflow is still worse than this?

Do not ask “Would you use this?” Ask for a concrete next behavior: whether they
want to try another permitted recording, reopen this one later, or send a colleague
the public demo.

## After each session

1. Submit one in-app feedback item per distinct product problem if the participant
   agrees; do not paste sensitive notes.
2. Classify severity as task blocked, task delayed, confusion, or preference.
3. Add the problem to a cross-session cluster with no participant identity.
4. Check aggregate product events against observed actions; investigate gaps
   without expanding the payload.
5. Fix correctness, security, reliability, deployment, or severe usability
   blockers. Keep feature requests in the roadmap evidence classes.
6. Follow up only with explicit contact permission.

Success is an unprompted completed workflow and a reason to return, not praise.
