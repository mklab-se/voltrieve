import type { Auth } from "../auth";
import { Dashboard } from "./Dashboard";
import { Loading, NoRole } from "./Notices";

export type Access = "checking" | "allowed" | "denied";

interface Props {
  access: Access;
  apiUrl: string;
  auth: Auth;
  liveMetricsUrl?: string;
  workbookUrl?: string;
}

/** The dashboard is created only when access is allowed: it is what asks the API for the feed. */
export function Gate({ access, apiUrl, auth, liveMetricsUrl, workbookUrl }: Props) {
  if (access === "checking") return <Loading />;
  if (access === "denied") return <NoRole />;
  return <Dashboard apiUrl={apiUrl} auth={auth} liveMetricsUrl={liveMetricsUrl} workbookUrl={workbookUrl} />;
}
