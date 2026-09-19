/**
 * These shapes are derived only from what LiveKit actually delivers to the
 * client: `RoomEvent.TranscriptionReceived` (livekit-agents' RoomIO publishes
 * STT + TTS text on the `lk.transcription` text-stream topic) and
 * `RoomEvent.DataReceived` for anything the agent chooses to send as
 * structured data. Nothing here is generated locally — see useLiveKit.ts.
 */

export type TranscriptSpeaker = "user" | "agent" | "unknown";

export interface TranscriptEntry {
  /** Stable per-segment id from LiveKit; re-used across interim -> final updates. */
  id: string;
  speaker: TranscriptSpeaker;
  participantIdentity: string;
  text: string;
  final: boolean;
  firstReceivedAt: number;
  lastReceivedAt: number;
}

export type ToolEventStatus = "started" | "succeeded" | "failed" | "unknown";

/**
 * A tool/action event as reported by the agent over a LiveKit data message.
 * The backend does not currently publish these (agent_entrypoint.py has no
 * data-channel publishing for tool calls), so this panel will legitimately
 * stay empty until that's added -- it is never populated from guesses.
 */
export interface ToolEvent {
  id: string;
  name: string;
  status: ToolEventStatus;
  detail?: string;
  receivedAt: number;
  raw: unknown;
}

export type AgentState = "idle" | "initializing" | "listening" | "thinking" | "speaking";
