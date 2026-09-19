import type { Participant, RoomOptions } from "livekit-client";
import type { AgentState } from "../types/transcript";

/** Matches livekit-agents' `ATTRIBUTE_AGENT_STATE = "lk.agent.state"` (app/types.py). */
export const AGENT_STATE_ATTRIBUTE = "lk.agent.state";

export const VALID_AGENT_STATES: readonly AgentState[] = [
  "idle",
  "initializing",
  "listening",
  "thinking",
  "speaking",
];

export function parseAgentState(value: string | undefined): AgentState | null {
  if (!value) return null;
  return (VALID_AGENT_STATES as readonly string[]).includes(value) ? (value as AgentState) : null;
}

/**
 * The dev-token identity is the frontend user; every other room participant
 * is treated as "agent". This mirrors `enforce_single_participant` in
 * app/security/session_boundary.py, which keeps exactly one human per room.
 */
export function classifyParticipant(
  identity: string,
  localIdentity: string,
): "user" | "agent" {
  return identity === localIdentity ? "user" : "agent";
}

export function findAgentParticipant(
  remoteParticipants: Map<string, Participant>,
): Participant | undefined {
  return Array.from(remoteParticipants.values())[0];
}

export const DEFAULT_ROOM_OPTIONS: RoomOptions = {
  adaptiveStream: true,
  dynacast: true,
};
