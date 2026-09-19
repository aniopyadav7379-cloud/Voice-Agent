import type { SessionPhase } from "../types/livekit";

interface ConnectionStatusProps {
  phase: SessionPhase;
  tenantSlug?: string;
  externalId?: string;
  agentIdentity: string | null;
}

const PHASE_LABEL: Record<SessionPhase, string> = {
  signed_out: "Signed out",
  authenticating: "Authenticating",
  ready_to_connect: "Ready",
  connecting: "Connecting",
  connected: "Connected",
  reconnecting: "Reconnecting",
  disconnecting: "Disconnecting",
  error: "Connection error",
};

const PHASE_TONE: Record<SessionPhase, "neutral" | "positive" | "active" | "negative"> = {
  signed_out: "neutral",
  authenticating: "active",
  ready_to_connect: "neutral",
  connecting: "active",
  connected: "positive",
  reconnecting: "active",
  disconnecting: "active",
  error: "negative",
};

export function ConnectionStatus({ phase, tenantSlug, externalId, agentIdentity }: ConnectionStatusProps) {
  const tone = PHASE_TONE[phase];
  return (
    <div className="connection-status">
      <span className={`status-pill status-pill--${tone}`}>
        <span className="status-pill__dot" aria-hidden="true" />
        {PHASE_LABEL[phase]}
      </span>
      {tenantSlug && (
        <span className="connection-status__meta">
          <span className="connection-status__meta-label">Tenant</span>
          <span className="connection-status__meta-value">{tenantSlug}</span>
        </span>
      )}
      {externalId && (
        <span className="connection-status__meta">
          <span className="connection-status__meta-label">User</span>
          <span className="connection-status__meta-value">{externalId}</span>
        </span>
      )}
      {agentIdentity && (
        <span className="connection-status__meta">
          <span className="connection-status__meta-label">Agent</span>
          <span className="connection-status__meta-value">{agentIdentity}</span>
        </span>
      )}
    </div>
  );
}
