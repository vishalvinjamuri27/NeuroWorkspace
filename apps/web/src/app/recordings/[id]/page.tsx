import type { Metadata } from "next";

import { ViewerShell } from "../../../components/viewer/ViewerShell";

export const metadata: Metadata = { title: "Signal viewer" };

export default async function RecordingPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ViewerShell recordingId={id} />;
}
