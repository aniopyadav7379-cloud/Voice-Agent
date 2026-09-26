import type { SessionPhase } from "../types/livekit";

interface ConnectionStatusProps {
  phase: SessionPhase;
  tenantSlug?: string;
  externalId?: string;
  agentIdentity: string | null;
}

const PHASE_CONFIG: Record<
  SessionPhase,
  { label: string; tone: "positive" | "warning" | "negative" | "neutral" }
> = {
  signed_out: { label: "Ready to Connect", tone: "neutral" },
  authenticating: { label: "Minting Token…", tone: "warning" },
  ready_to_connect: { label: "Ready", tone: "neutral" },
  connecting: { label: "Connecting WebRTC…", tone: "warning" },
  connected: { label: "Live Voice Call", tone: "positive" },
  reconnecting: { label: "Reconnecting…", tone: "warning" },
  disconnecting: { label: "Closing Session…", tone: "warning" },
  error: { label: "Connection Error", tone: "negative" },
};

export function ConnectionStatus({
  phase,
  tenantSlug,
  externalId,
  agentIdentity,
}: ConnectionStatusProps) {
  const current = PHASE_CONFIG[phase] || PHASE_CONFIG.signed_out;

  return (
    <div className="header-telemetry-bar">
      {/* Live Status Pill */}
      <div className={`telemetry-status-pill telemetry-status-pill--${current.tone}`}>
        <span className="telemetry-status-pill__pulse" aria-hidden="true" />
        <span className="telemetry-status-pill__label">{current.label}</span>
      </div>

      {/* Cloud & Provider Tags */}
      <div className="telemetry-tags-list">
        <span className="telemetry-chip telemetry-chip--cloud" title="LiveKit WebRTC Cloud Gateway">
          <span className="telemetry-chip__dot" />
          LiveKit Cloud · India South
        </span>

        <span className="telemetry-chip telemetry-chip--ai" title="AI Voice Pipeline">
          ⚡ Sarvam + Groq
        </span>

        {tenantSlug && (
          <span className="telemetry-chip telemetry-chip--tenant" title={`Active Tenant: ${tenantSlug}`}>
            🏢 {tenantSlug}
          </span>
        )}

        {externalId && (
          <span className="telemetry-chip telemetry-chip--user" title={`Logged in as: ${externalId}`}>
            👤 {externalId}
          </span>
        )}

        {agentIdentity && (
          <span className="telemetry-chip telemetry-chip--agent" title={`Agent ID: ${agentIdentity}`}>
            🤖 Active
          </span>
        )}
      </div>
    </div>
  );
}
