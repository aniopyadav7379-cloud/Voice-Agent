import { useState } from "react";
import type { MicPhase, SessionPhase } from "../types/livekit";

interface VoiceControlsProps {
  connectionPhase: SessionPhase;
  micPhase: MicPhase;
  isAuthenticating?: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onStartMic: () => void;
  onStopMic: () => void;
  onToggleMute: () => void;
}

const QUICK_PROMPTS = [
  { icon: "🎫", text: "Check ticket status #4092", label: "Ticket Status" },
  { icon: "👷", text: "Dispatch technician to Sector 4", label: "Dispatch Tech" },
  { icon: "⚠️", text: "Report high vibration on Pump B-12", label: "Report Issue" },
  { icon: "🇮🇳", text: "Namaste! What can you help me with?", label: "Namaste" },
  { icon: "🔔", text: "Send emergency notification to dispatch team", label: "Send Alert" },
];

export function VoiceControls({
  connectionPhase,
  micPhase,
  isAuthenticating = false,
  onConnect,
  onDisconnect,
  onStartMic,
  onStopMic,
  onToggleMute,
}: VoiceControlsProps) {
  const [copiedPrompt, setCopiedPrompt] = useState<string | null>(null);

  const isConnected = connectionPhase === "connected";
  const isConnecting = connectionPhase === "connecting" || isAuthenticating;
  const isDisconnecting = connectionPhase === "disconnecting";
  const isBusy = isConnecting || isDisconnecting;
  const canConnect = (connectionPhase === "ready_to_connect" || connectionPhase === "error" || connectionPhase === "signed_out") && !isBusy;

  const handlePromptClick = (text: string) => {
    setCopiedPrompt(text);
    setTimeout(() => setCopiedPrompt(null), 3000);
  };

  const handleMicToggle = () => {
    if (micPhase === "started") {
      onStopMic();
    } else {
      onStartMic();
    }
    onToggleMute();
  };

  return (
    <div className="voice-controls-panel">
      {/* Primary Action Button */}
      <div className="voice-controls-panel__main">
        {!isConnected ? (
          <button
            type="button"
            className={`btn-hero-connect ${isConnecting ? "is-connecting" : ""}`}
            onClick={onConnect}
            disabled={!canConnect}
            aria-busy={isConnecting}
          >
            <span className="btn-hero-connect__glow" aria-hidden="true" />
            <span className="btn-hero-connect__icon">
              {isConnecting ? (
                <svg className="spin-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <circle cx="12" cy="12" r="10" strokeDasharray="32" strokeDashoffset="12" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.2">
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" y1="19" x2="12" y2="22" />
                </svg>
              )}
            </span>
            <span className="btn-hero-connect__label">
              {isConnecting ? "Establishing Live Audio Stream…" : "Connect Voice Session"}
            </span>
          </button>
        ) : (
          <div className="connected-actions-row">
            {/* Mic Toggle Button */}
            <button
              type="button"
              className={`btn-mic-toggle ${micPhase === "started" ? "is-active" : "is-muted"}`}
              onClick={handleMicToggle}
              title={micPhase === "started" ? "Click to Mute Microphone" : "Click to Unmute Microphone"}
            >
              <span className="btn-mic-toggle__indicator" />
              {micPhase === "started" ? (
                <>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3Z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <line x1="12" y1="19" x2="12" y2="23" />
                  </svg>
                  <span>Mic Live (Mute)</span>
                </>
              ) : (
                <>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="1" y1="1" x2="23" y2="23" />
                    <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
                    <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23" />
                    <line x1="12" y1="19" x2="12" y2="23" />
                  </svg>
                  <span>Mic Muted (Unmute)</span>
                </>
              )}
            </button>

            {/* Disconnect Call Button */}
            <button
              type="button"
              className="btn-disconnect"
              onClick={onDisconnect}
              disabled={isBusy}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 11H7v-2h10v2z"/>
              </svg>
              <span>{isDisconnecting ? "Ending…" : "Disconnect"}</span>
            </button>
          </div>
        )}
      </div>

      {/* Suggested Voice Prompt Chips */}
      <div className="voice-prompts-section">
        <div className="voice-prompts-section__header">
          <span className="voice-prompts-section__title">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
            </svg>
            Suggested Voice Commands
          </span>
          {copiedPrompt && (
            <span className="voice-prompts-section__tip">
              🎙️ Speak this now: "{copiedPrompt}"
            </span>
          )}
        </div>
        <div className="voice-prompt-chips">
          {QUICK_PROMPTS.map((prompt, idx) => (
            <button
              key={idx}
              type="button"
              className="prompt-chip"
              onClick={() => handlePromptClick(prompt.text)}
              title={`Click to copy: "${prompt.text}"`}
            >
              <span className="prompt-chip__icon">{prompt.icon}</span>
              <span className="prompt-chip__label">{prompt.text}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
