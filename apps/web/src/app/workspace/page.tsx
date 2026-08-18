"use client";

import type {
  Project,
  Recording,
  Workspace,
} from "@neuroworkspace/shared-types";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { FeedbackButton } from "../../components/FeedbackButton";
import { APIError, api, formatDuration } from "../../lib/api";

interface ProjectGroup {
  workspace: Workspace;
  project: Project;
  recordings: Recording[];
}

export default function WorkspacePage() {
  const [groups, setGroups] = useState<ProjectGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [signedIn, setSignedIn] = useState(false);
  const [config, setConfig] = useState<Awaited<
    ReturnType<typeof api.sessionConfig>
  > | null>(null);
  const [email, setEmail] = useState("researcher@example.test");
  const [displayName, setDisplayName] = useState("Researcher");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextConfig = await api.sessionConfig();
      setConfig(nextConfig);
      await api.session();
      setSignedIn(true);
      const workspaces = await api.workspaces();
      const nested = await Promise.all(
        workspaces.map(async (workspace) => {
          const projects = await api.projects(workspace.id);
          return Promise.all(
            projects.map(async (project) => ({
              workspace,
              project,
              recordings: await api.recordings(project.id),
            })),
          );
        }),
      );
      setGroups(nested.flat());
    } catch (caught) {
      if (caught instanceof APIError && caught.status === 401) {
        setSignedIn(false);
      } else {
        setError(
          caught instanceof Error
            ? caught.message
            : "Workspace could not be loaded.",
        );
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api.signIn(email, displayName);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign-in failed.");
    }
  }

  return (
    <main className="workspace-shell">
      <header className="app-header simple-header">
        <Link href="/" className="brand">
          <span className="brand-mark">NW</span>
          <span>NeuroWorkspace</span>
        </Link>
        <span className="scope-badge">Research use · non-clinical</span>
      </header>

      <section className="workspace-heading">
        <div>
          <p className="section-kicker">Your research workspace</p>
          <h1>Continue with a recording.</h1>
          <p className="muted">
            Workspaces contain projects; projects keep related recordings and
            their review history together.
          </p>
        </div>
        {signedIn && (
          <Link className="button primary" href="/upload">
            Import a recording
          </Link>
        )}
      </section>

      {loading && (
        <section className="workspace-state" aria-live="polite">
          <div className="processing-glyph">
            <span />
            <span />
            <span />
          </div>
          <p>Loading your projects…</p>
        </section>
      )}

      {!loading && !signedIn && (
        <section className="workspace-auth-card">
          <p className="section-kicker">Account</p>
          <h2>Sign in to return to your recordings.</h2>
          {config?.dev_auth_enabled ? (
            <form onSubmit={signIn}>
              <p className="muted">
                Local development access. Hosted alpha deployments use the
                configured identity provider.
              </p>
              <div className="form-grid two">
                <label>
                  Display name
                  <input
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    required
                  />
                </label>
                <label>
                  Email
                  <input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                  />
                </label>
              </div>
              <button className="button primary">Continue locally</button>
            </form>
          ) : config?.oidc_configured ? (
            <a
              className="button primary"
              href={`${config.oidc_login_url}?return_to=/workspace`}
            >
              Sign in
            </a>
          ) : (
            <div className="inline-error" role="alert">
              Hosted sign-in is not configured. The deployment operator must
              configure OIDC before inviting alpha users.
            </div>
          )}
        </section>
      )}

      {!loading && signedIn && groups.length === 0 && (
        <section className="workspace-state">
          <h2>No projects yet</h2>
          <p className="muted">
            Import your first supported EEG recording. NeuroWorkspace will
            create a workspace and project for it.
          </p>
          <Link className="button primary" href="/upload">
            Import a recording
          </Link>
        </section>
      )}

      {!loading && signedIn && groups.length > 0 && (
        <section className="workspace-groups">
          {groups.map(({ workspace, project, recordings }) => (
            <article className="project-card" key={project.id}>
              <header>
                <div>
                  <span>{workspace.name}</span>
                  <h2>{project.name}</h2>
                </div>
                <Link
                  className="button secondary small"
                  href={`/upload?project_id=${project.id}`}
                >
                  Add recording
                </Link>
              </header>
              {recordings.length ? (
                <div className="recording-list">
                  {recordings.map((recording) => (
                    <Link
                      href={`/recordings/${recording.id}`}
                      key={recording.id}
                    >
                      <div>
                        <strong>{recording.name}</strong>
                        <span>
                          {recording.format_id?.toUpperCase() ?? "UNKNOWN"} ·{" "}
                          {formatDuration(
                            Number(
                              recording.safe_metadata.duration_seconds ?? 0,
                            ),
                          )}
                        </span>
                      </div>
                      <span
                        className={
                          recording.state === "READY"
                            ? "ready-pill"
                            : "scope-badge"
                        }
                      >
                        {recording.state}
                      </span>
                    </Link>
                  ))}
                </div>
              ) : (
                <p className="empty-state">
                  No recordings in this project yet.
                </p>
              )}
            </article>
          ))}
        </section>
      )}

      {error && (
        <p className="inline-error workspace-error" role="alert">
          {error}
        </p>
      )}
      <FeedbackButton />
    </main>
  );
}
