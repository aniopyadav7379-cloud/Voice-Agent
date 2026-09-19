import type { AgentState } from "../types/transcript";
import type { MicPhase, SessionPhase } from "../types/livekit";

interface VoiceVisualizerProps {
  agentState: AgentState;
  connectionPhase: SessionPhase;
  micPhase: MicPhase;
  localAudioLevel: number;
  agentAudioLevel: number;
}

const STATE_LABEL: Record<AgentState, string> = {
  idle: "Idle",
  initializing: "Initializing",
  listening: "Listening",
  thinking: "Processing",
  speaking: "Speaking",
};

const METER_SEGMENTS = 5;

function Meter({ label, level, active }: { label: string; level: number; active: boolean }) {
  const litCount = active ? Math.round(Math.min(1, Math.max(0, level)) * METER_SEGMENTS) : 0;
  return (
    <div className="meter">
      <span className="meter__label">{label}</span>
      <div className="meter__bars" role="img" aria-label={`${label} audio level ${Math.round(level * 100)} percent`}>
        {Array.from({ length: METER_SEGMENTS }, (_, index) => (
          <span key={index} className={`meter__bar${index < litCount ? " is-lit" : ""}`} />
        ))}
      </div>
    </div>
  );
}

export function VoiceVisualizer({
  agentState,
  connectionPhase,
  micPhase,
  localAudioLevel,
  agentAudioLevel,
}: VoiceVisualizerProps) {
  const isLive = connectionPhase === "connected";
  const ringState = isLive ? agentState : "idle";

  return (
    <div className="visualizer">
      <div className={`visualizer__ring visualizer__ring--${ringState}`} aria-hidden="true">
        <div className="visualizer__core" />
      </div>
      <p className="visualizer__state" aria-live="polite">
        {isLive ? STATE_LABEL[agentState] : connectionPhase === "connecting" ? "Connecting…" : "Not connected"}
      </p>
      <div className="visualizer__meters">
        <Meter label="You" level={localAudioLevel} active={isLive && micPhase === "started"} />
        <Meter label="Agent" level={agentAudioLevel} active={isLive} />
      </div>
    </div>
  );
}
