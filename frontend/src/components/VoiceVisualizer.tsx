import { useMemo } from "react";
import type { AgentState } from "../types/transcript";
import type { MicPhase, SessionPhase } from "../types/livekit";

interface VoiceVisualizerProps {
  agentState: AgentState;
  connectionPhase: SessionPhase;
  micPhase: MicPhase;
  localAudioLevel: number;
  agentAudioLevel: number;
}

const STATE_CONFIG: Record<
  AgentState,
  { label: string; badge: string; color: string; desc: string }
> = {
  idle: {
    label: "Ready to Listen",
    badge: "IDLE",
    color: "#6366f1",
    desc: "Speak anytime or click a quick prompt",
  },
  initializing: {
    label: "Warming Up Pipeline",
    badge: "INIT",
    color: "#38bdf8",
    desc: "Establishing neural audio stream",
  },
  listening: {
    label: "Listening to You...",
    badge: "LISTENING",
    color: "#10b981",
    desc: "Streaming mic audio to Sarvam STT",
  },
  thinking: {
    label: "Processing Intent...",
    badge: "REASONING",
    color: "#f59e0b",
    desc: "Groq Qwen-3.8 evaluating context and tools",
  },
  speaking: {
    label: "AI Speaking...",
    badge: "SPEAKING",
    color: "#a855f7",
    desc: "Sarvam Bulbul v3 synthesizing audio",
  },
};

const BARS_COUNT = 24;

export function VoiceVisualizer({
  agentState,
  connectionPhase,
  micPhase,
  localAudioLevel,
  agentAudioLevel,
}: VoiceVisualizerProps) {
  const isLive = connectionPhase === "connected";
  const activeLevel = agentState === "speaking" ? agentAudioLevel : localAudioLevel;
  const currentConfig = STATE_CONFIG[agentState] || STATE_CONFIG.idle;

  // Compute dynamic heights for waveform bars
  const waveBars = useMemo(() => {
    return Array.from({ length: BARS_COUNT }, (_, i) => {
      // Bell curve multiplier (center bars are taller)
      const center = BARS_COUNT / 2;
      const distFromCenter = Math.abs(i - center) / center;
      const envelope = Math.max(0.2, 1 - distFromCenter * 0.7);

      if (!isLive) return 6;

      // Base idle breathing height
      const idleWave = Math.sin(i * 0.5 + Date.now() / 600) * 4 + 8;

      if (activeLevel > 0.02) {
        // High reactive voice wave
        const dynamic = activeLevel * 56 * envelope;
        const jitter = Math.sin(i * 1.3) * 6;
        return Math.max(6, Math.min(64, dynamic + jitter + 8));
      }

      return idleWave;
    });
  }, [isLive, activeLevel]);

  // Dynamic scale for the glowing orb
  const orbScale = isLive ? 1 + Math.min(0.35, activeLevel * 1.2) : 1;

  return (
    <div className="voice-stage">
      {/* Background ambient lighting halo */}
      <div
        className={`voice-stage__ambient voice-stage__ambient--${isLive ? agentState : "offline"}`}
        aria-hidden="true"
      />

      {/* Main Orb Centerpiece */}
      <div className="voice-orb-container">
        <div
          className={`voice-orb voice-orb--${isLive ? agentState : "offline"}`}
          style={{ transform: `scale(${orbScale})` }}
          aria-hidden="true"
        >
          <div className="voice-orb__aura" />
          <div className="voice-orb__ring-outer" />
          <div className="voice-orb__ring-inner" />
          <div className="voice-orb__core" />
          <div className="voice-orb__particles" />
        </div>

        {/* Live Status Pill badge */}
        <div className="voice-status-badge">
          <span
            className={`voice-status-badge__dot voice-status-badge__dot--${
              isLive ? agentState : connectionPhase
            }`}
          />
          <span className="voice-status-badge__tag">
            {isLive ? currentConfig.badge : connectionPhase === "connecting" ? "CONNECTING" : "OFFLINE"}
          </span>
          <span className="voice-status-badge__title">
            {isLive ? currentConfig.label : connectionPhase === "connecting" ? "Connecting to LiveKit..." : "Not Connected"}
          </span>
        </div>
        <p className="voice-status-desc">
          {isLive ? currentConfig.desc : "Click 'Connect Session' below to start talking"}
        </p>
      </div>

      {/* Dynamic Sound Wave Spectrum */}
      <div className="voice-spectrum" aria-label="Audio spectrum visualization">
        <div className="voice-spectrum__bars">
          {waveBars.map((height, index) => (
            <div
              key={index}
              className={`voice-spectrum__bar ${
                isLive && activeLevel > 0.05 ? "is-active" : ""
              } voice-spectrum__bar--${isLive ? agentState : "offline"}`}
              style={{
                height: `${height}px`,
                animationDelay: `${index * 45}ms`,
              }}
            />
          ))}
        </div>
      </div>

      {/* Precision Dual Stereo Audio Level Meters */}
      <div className="voice-meters-hud">
        <div className="meter-hud-box">
          <div className="meter-hud-box__top">
            <span className="meter-hud-box__label">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
              Mic In
            </span>
            <span className="meter-hud-box__value">
              {isLive && micPhase === "started" ? `${Math.round(localAudioLevel * 100)}%` : "Muted"}
            </span>
          </div>
          <div className="meter-hud-box__track">
            <div
              className="meter-hud-box__fill meter-hud-box__fill--user"
              style={{
                width: `${isLive && micPhase === "started" ? Math.min(100, Math.round(localAudioLevel * 100)) : 0}%`,
              }}
            />
          </div>
        </div>

        <div className="meter-hud-box">
          <div className="meter-hud-box__top">
            <span className="meter-hud-box__label">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
              </svg>
              Agent Out
            </span>
            <span className="meter-hud-box__value">
              {isLive ? `${Math.round(agentAudioLevel * 100)}%` : "0%"}
            </span>
          </div>
          <div className="meter-hud-box__track">
            <div
              className="meter-hud-box__fill meter-hud-box__fill--agent"
              style={{
                width: `${isLive ? Math.min(100, Math.round(agentAudioLevel * 100)) : 0}%`,
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
