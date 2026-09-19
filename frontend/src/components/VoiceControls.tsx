import type { MicPhase, SessionPhase } from "../types/livekit";

interface VoiceControlsProps {
  connectionPhase: SessionPhase;
  micPhase: MicPhase;
  onConnect: () => void;
  onDisconnect: () => void;
  onStartMic: () => void;
  onStopMic: () => void;
  onToggleMute: () => void;
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path
        d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-5a3.5 3.5 0 0 0-7 0v5A3.5 3.5 0 0 0 12 15Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M6.5 11.25a5.5 5.5 0 0 0 11 0M12 17.25V20m-3 0h6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MicOffIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path
        d="M12 15a3.5 3.5 0 0 0 3.5-3.5V9m-.35-3.2A3.5 3.5 0 0 0 8.5 8.5v3c0 .38.048.748.138 1.098"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path
        d="M6.5 11.25a5.5 5.5 0 0 0 9.5 3.87M12 17.25V20m-3 0h6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path d="M4 4l16 16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export function VoiceControls({
  connectionPhase,
  micPhase,
  onConnect,
  onDisconnect,
  onStartMic,
  onStopMic,
  onToggleMute,
}: VoiceControlsProps) {
  const isConnected = connectionPhase === "connected";
  const isConnecting = connectionPhase === "connecting";
  const isDisconnecting = connectionPhase === "disconnecting";
  const isBusy = isConnecting || isDisconnecting;
  const canConnect = connectionPhase === "ready_to_connect" || connectionPhase === "error";

  return (
    <div className="voice-controls">
      {!isConnected ? (
        <button
          type="button"
          className="btn btn--primary btn--wide"
          onClick={onConnect}
          disabled={!canConnect && !isBusy}
          aria-busy={isConnecting}
        >
          {isConnecting ? "Connecting…" : "Connect"}
        </button>
      ) : (
        <button type="button" className="btn btn--danger btn--wide" onClick={onDisconnect} disabled={isBusy}>
          {isDisconnecting ? "Disconnecting…" : "Disconnect"}
        </button>
      )}

      <div className="voice-controls__row">
        {micPhase === "stopped" ? (
          <button type="button" className="btn btn--secondary" onClick={onStartMic} disabled={!isConnected}>
            <MicIcon />
            Start microphone
          </button>
        ) : (
          <button type="button" className="btn btn--secondary" onClick={onStopMic} disabled={!isConnected}>
            <MicOffIcon />
            Stop microphone
          </button>
        )}

        <button
          type="button"
          className={`btn btn--icon-toggle${micPhase === "muted" ? " is-active" : ""}`}
          onClick={onToggleMute}
          disabled={!isConnected || micPhase === "stopped" || micPhase === "starting"}
          aria-pressed={micPhase === "muted"}
        >
          {micPhase === "muted" ? <MicOffIcon /> : <MicIcon />}
          {micPhase === "muted" ? "Unmute" : "Mute"}
        </button>
      </div>
    </div>
  );
}
