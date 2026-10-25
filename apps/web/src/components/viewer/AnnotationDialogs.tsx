"use client";

import { formatDuration } from "../../lib/api";
import type { AnnotationsState } from "./useAnnotations";

/** Create, edit, and revision-conflict dialogs for annotations. */
export function AnnotationDialogs({
  annotations,
}: {
  annotations: AnnotationsState;
}) {
  const {
    selection,
    setSelection,
    annotationLabel,
    setAnnotationLabel,
    annotationDescription,
    setAnnotationDescription,
    savingAnnotation,
    saveAnnotation,
    editingAnnotation,
    setEditingAnnotation,
    saveEditedAnnotation,
    annotationConflict,
    setAnnotationConflict,
    reapplyConflictedEdit,
  } = annotations;

  return (
    <>
      {selection && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setSelection(null)}
        >
          <form
            className="annotation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="annotation-title"
            onSubmit={saveAnnotation}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="section-kicker">
              Selected region · {formatDuration(selection.start)}–
              {formatDuration(selection.end)}
            </p>
            <h2 id="annotation-title">Create annotation</h2>
            <label>
              Label
              <input
                autoFocus
                maxLength={160}
                required
                value={annotationLabel}
                onChange={(event) => setAnnotationLabel(event.target.value)}
              />
            </label>
            <label>
              Description
              <textarea
                rows={3}
                maxLength={4000}
                value={annotationDescription}
                onChange={(event) =>
                  setAnnotationDescription(event.target.value)
                }
              />
            </label>
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setSelection(null)}
              >
                Cancel
              </button>
              <button className="button primary" type="submit" disabled={savingAnnotation}>
                {savingAnnotation ? "Saving…" : "Save annotation"}
              </button>
            </div>
          </form>
        </div>
      )}
      {editingAnnotation && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setEditingAnnotation(null)}
        >
          <form
            className="annotation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-annotation-title"
            onSubmit={saveEditedAnnotation}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="section-kicker">
              Revision {editingAnnotation.revision}
            </p>
            <h2 id="edit-annotation-title">Edit annotation</h2>
            <label>
              Label
              <input
                autoFocus
                maxLength={160}
                required
                value={annotationLabel}
                onChange={(event) => setAnnotationLabel(event.target.value)}
              />
            </label>
            <label>
              Description
              <textarea
                rows={3}
                maxLength={4000}
                value={annotationDescription}
                onChange={(event) =>
                  setAnnotationDescription(event.target.value)
                }
              />
            </label>
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setEditingAnnotation(null)}
              >
                Cancel
              </button>
              <button className="button primary" type="submit" disabled={savingAnnotation}>
                {savingAnnotation ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </div>
      )}
      {annotationConflict && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setAnnotationConflict(null)}
        >
          <div
            className="annotation-modal conflict-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="conflict-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="section-kicker">Someone else changed this first</p>
            <h2 id="conflict-title">
              This annotation changed{" "}
              {annotationConflict.action === "delete" ? "before your delete" : "elsewhere"}
            </h2>
            <p className="muted">
              {annotationConflict.server
                ? "Another edit was saved after you loaded this annotation. Your change was not applied, so nothing was silently overwritten. Review the current version below."
                : "This annotation was deleted by someone else. Your change was not applied."}
            </p>
            {annotationConflict.server && (
              <div className="conflict-columns">
                <div>
                  <p className="section-kicker">Current on server</p>
                  <strong>{annotationConflict.server.label}</strong>
                  <p>{annotationConflict.server.description || "—"}</p>
                  <span className="mono">
                    rev {annotationConflict.server.revision} ·{" "}
                    {formatDuration(annotationConflict.server.onset_seconds)}
                  </span>
                </div>
                <div>
                  <p className="section-kicker">Your attempted change</p>
                  <strong>{annotationConflict.attempted.label}</strong>
                  <p>{annotationConflict.attempted.description || "—"}</p>
                  <span className="mono">
                    {formatDuration(annotationConflict.attempted.onset_seconds)}
                  </span>
                </div>
              </div>
            )}
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setAnnotationConflict(null)}
              >
                Discard my change
              </button>
              {annotationConflict.action === "update" &&
                annotationConflict.server && (
                  <button
                    type="button"
                    className="button primary"
                    onClick={reapplyConflictedEdit}
                  >
                    Review &amp; reapply my edit
                  </button>
                )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
