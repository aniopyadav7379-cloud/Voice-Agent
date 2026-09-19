export type SessionPhase =
  | "signed_out" // no stored application JWT, needs the dev bootstrap form
  | "authenticating" // dev/token or livekit/token exchange in flight
  | "ready_to_connect" // JWT present, no active room connection yet
  | "connecting" // Room.connect() in flight
  | "connected"
  | "reconnecting"
  | "disconnecting"
  | "error";

export type MicPhase = "stopped" | "starting" | "started" | "muted";

export interface AppErrorInfo {
  id: string;
  title: string;
  message: string;
  at: number;
}
