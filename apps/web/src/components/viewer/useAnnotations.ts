"use client";

import type { Annotation, Page } from "@neuroworkspace/shared-types";
import { FormEvent, useCallback, useState } from "react";

import { api, isConflict } from "../../lib/api";

export interface AnnotationConflict {
  /** The edit the user was trying to save when the server rejected it. */
  attempted: {
    onset_seconds: number;
    duration_seconds: number;
    label: string;
    description: string;
  };
  /** What is actually on the server right now. */
  server: Annotation | null;
  /** "update" vs "delete" changes the recovery copy/actions shown. */
  action: "update" | "delete";
}

export interface Selection {
  start: number;
  end: number;
}

/** Annotation list, pagination, region selection, and create/edit/delete with
 * revision-conflict recovery: a 409 never silently overwrites the server. */
export function useAnnotations(
  recordingId: string,
  onError: (message: string) => void,
) {
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [annotationsCursor, setAnnotationsCursor] = useState<string | null>(
    null,
  );
  const [loadingMoreAnnotations, setLoadingMoreAnnotations] = useState(false);
  const [annotationConflict, setAnnotationConflict] =
    useState<AnnotationConflict | null>(null);
  const [savingAnnotation, setSavingAnnotation] = useState(false);
  const [selectMode, setSelectMode] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [annotationLabel, setAnnotationLabel] = useState("");
  const [annotationDescription, setAnnotationDescription] = useState("");
  const [editingAnnotation, setEditingAnnotation] = useState<Annotation | null>(
    null,
  );

  const resetAnnotations = useCallback((page: Page<Annotation>) => {
    setAnnotations(page.items);
    setAnnotationsCursor(page.next_cursor);
  }, []);

  const toggleSelectMode = useCallback(
    () => setSelectMode((value) => !value),
    [],
  );

  function handleSelection(selectedStart: number, selectedEnd: number) {
    setSelection({ start: selectedStart, end: selectedEnd });
    setSelectMode(false);
    setAnnotationLabel("");
    setAnnotationDescription("");
  }

  async function saveAnnotation(event: FormEvent) {
    event.preventDefault();
    if (!selection || savingAnnotation) return;
    setSavingAnnotation(true);
    try {
      const created = await api.createAnnotation(recordingId, {
        onset_seconds: selection.start,
        duration_seconds: selection.end - selection.start,
        label: annotationLabel,
        description: annotationDescription,
      });
      setAnnotations((current) =>
        [...current, created].sort((a, b) => a.onset_seconds - b.onset_seconds),
      );
      setSelection(null);
    } catch (caught) {
      onError(
        caught instanceof Error
          ? caught.message
          : "The annotation could not be saved.",
      );
    } finally {
      setSavingAnnotation(false);
    }
  }

  function beginEditAnnotation(annotation: Annotation) {
    setEditingAnnotation(annotation);
    setAnnotationLabel(annotation.label);
    setAnnotationDescription(annotation.description);
  }

  async function saveEditedAnnotation(event: FormEvent) {
    event.preventDefault();
    if (!editingAnnotation || savingAnnotation) return;
    const attempted = {
      onset_seconds: editingAnnotation.onset_seconds,
      duration_seconds: editingAnnotation.duration_seconds,
      label: annotationLabel,
      description: annotationDescription,
    };
    setSavingAnnotation(true);
    try {
      const updated = await api.updateAnnotation(editingAnnotation.id, {
        revision: editingAnnotation.revision,
        ...attempted,
      });
      setAnnotations((current) =>
        current.map((annotation) =>
          annotation.id === updated.id ? updated : annotation,
        ),
      );
      setEditingAnnotation(null);
    } catch (caught) {
      if (isConflict(caught)) {
        setEditingAnnotation(null);
        const server = await api
          .annotationById(recordingId, editingAnnotation.id)
          .catch(() => null);
        if (server) {
          setAnnotations((current) =>
            current.map((item) => (item.id === server.id ? server : item)),
          );
        }
        setAnnotationConflict({ attempted, server, action: "update" });
      } else {
        onError(
          caught instanceof Error
            ? caught.message
            : "The annotation could not be updated.",
        );
      }
    } finally {
      setSavingAnnotation(false);
    }
  }

  async function deleteAnnotation(annotation: Annotation) {
    if (!window.confirm(`Delete annotation “${annotation.label}”?`)) return;
    try {
      await api.deleteAnnotation(annotation.id, annotation.revision);
      setAnnotations((current) =>
        current.filter((item) => item.id !== annotation.id),
      );
    } catch (caught) {
      if (isConflict(caught)) {
        const server = await api
          .annotationById(recordingId, annotation.id)
          .catch(() => null);
        if (server) {
          setAnnotations((current) =>
            current.map((item) => (item.id === server.id ? server : item)),
          );
        } else {
          // The other change was itself a delete: nothing to resync to.
          setAnnotations((current) =>
            current.filter((item) => item.id !== annotation.id),
          );
        }
        setAnnotationConflict({
          attempted: {
            onset_seconds: annotation.onset_seconds,
            duration_seconds: annotation.duration_seconds,
            label: annotation.label,
            description: annotation.description,
          },
          server,
          action: "delete",
        });
      } else {
        onError(
          caught instanceof Error
            ? caught.message
            : "The annotation could not be deleted.",
        );
      }
    }
  }

  function reapplyConflictedEdit() {
    if (!annotationConflict?.server) return;
    setAnnotationLabel(annotationConflict.attempted.label);
    setAnnotationDescription(annotationConflict.attempted.description);
    setEditingAnnotation(annotationConflict.server);
    setAnnotationConflict(null);
  }

  async function loadMoreAnnotations() {
    if (!annotationsCursor || loadingMoreAnnotations) return;
    setLoadingMoreAnnotations(true);
    try {
      const page = await api.annotationsAfter(recordingId, annotationsCursor);
      setAnnotations((current) => {
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...page.items.filter((item) => !seen.has(item.id))];
      });
      setAnnotationsCursor(page.next_cursor);
    } catch (caught) {
      onError(
        caught instanceof Error
          ? caught.message
          : "More annotations could not be loaded.",
      );
    } finally {
      setLoadingMoreAnnotations(false);
    }
  }

  return {
    annotations,
    hasMoreAnnotations: Boolean(annotationsCursor),
    loadingMoreAnnotations,
    loadMoreAnnotations,
    resetAnnotations,
    selectMode,
    toggleSelectMode,
    selection,
    setSelection,
    handleSelection,
    annotationLabel,
    setAnnotationLabel,
    annotationDescription,
    setAnnotationDescription,
    savingAnnotation,
    saveAnnotation,
    editingAnnotation,
    setEditingAnnotation,
    beginEditAnnotation,
    saveEditedAnnotation,
    deleteAnnotation,
    annotationConflict,
    setAnnotationConflict,
    reapplyConflictedEdit,
  };
}

export type AnnotationsState = ReturnType<typeof useAnnotations>;
