"use client";

import { FormEvent, useState } from "react";

import { APIError, api } from "../lib/api";

const CATEGORIES = [
  ["bug", "Bug"],
  ["confusing_workflow", "Confusing workflow"],
  ["missing_feature", "Missing feature"],
  ["file_import_issue", "File import issue"],
  ["performance_issue", "Performance issue"],
  ["other", "Other"],
] as const;

export function FeedbackButton({
  workspaceId,
  recordingId,
}: {
  workspaceId?: string | null;
  recordingId?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("bug");
  const [message, setMessage] = useState("");
  const [mayContact, setMayContact] = useState(false);
  const [contactEmail, setContactEmail] = useState("");
  const [status, setStatus] = useState("");
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    setStatus("");
    try {
      const result = await api.feedback({
        category,
        message,
        route: window.location.pathname,
        workspace_id: workspaceId,
        recording_id: recordingId,
        may_contact: mayContact,
        contact_email: mayContact && contactEmail ? contactEmail : null,
      });
      setMessage("");
      setStatus(`Feedback sent. Reference ${result.id.slice(0, 8)}.`);
    } catch (caught) {
      const reference = caught instanceof APIError ? caught.reference : null;
      setStatus(
        `${caught instanceof Error ? caught.message : "Feedback could not be sent."}${reference ? ` Reference ${reference}.` : ""}`,
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <button className="feedback-trigger" onClick={() => setOpen(true)}>
        Send feedback
      </button>
      {open && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setOpen(false)}
        >
          <form
            className="annotation-modal feedback-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="feedback-title"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={submit}
          >
            <p className="section-kicker">Alpha feedback</p>
            <h2 id="feedback-title">Tell us what got in your way.</h2>
            <p className="privacy-note">
              Do not include names, patient or subject identifiers, clinical
              notes, filenames, annotation text, or signal data. No recording
              file is attached automatically.
            </p>
            <label>
              Category
              <select
                value={category}
                onChange={(event) => setCategory(event.target.value)}
              >
                {CATEGORIES.map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              What happened or what did you expect?
              <textarea
                rows={5}
                required
                minLength={3}
                maxLength={4000}
                value={message}
                onChange={(event) => setMessage(event.target.value)}
              />
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={mayContact}
                onChange={(event) => setMayContact(event.target.checked)}
              />
              You may contact me about this feedback
            </label>
            {mayContact && (
              <label>
                Contact email (optional when signed in)
                <input
                  type="email"
                  value={contactEmail}
                  onChange={(event) => setContactEmail(event.target.value)}
                />
              </label>
            )}
            {status && (
              <p className="muted" role="status">
                {status}
              </p>
            )}
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setOpen(false)}
              >
                Close
              </button>
              <button className="button primary" disabled={sending}>
                {sending ? "Sending…" : "Send feedback"}
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
