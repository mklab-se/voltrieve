import type { Auth } from "../auth";
import type { Tab } from "../insights";
import { Dashboard } from "./Dashboard";
import { Ask } from "./Insights";
import { Loading, NoRole } from "./Notices";

export type Access = "checking" | "allowed" | "denied";

interface Props {
  access: Access;
  apiUrl: string;
  auth: Auth;
  tab?: Tab;
  liveMetricsUrl?: string;
  workbookUrl?: string;
}

/** The page of a tab is created only when access is allowed: it is what asks the API. Only one of the
 *  two exists at a time, so the live feed stops when the other tab is shown. */
export function Gate({ access, apiUrl, auth, tab = "live", liveMetricsUrl, workbookUrl }: Props) {
  if (access === "checking") return <Loading />;
  if (access === "denied") return <NoRole />;
  return (
    <div className="panel" id="panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
      {tab === "ask" ? <Ask apiUrl={apiUrl} auth={auth} /> : <Dashboard apiUrl={apiUrl} auth={auth} liveMetricsUrl={liveMetricsUrl} workbookUrl={workbookUrl} />}
    </div>
  );
}
