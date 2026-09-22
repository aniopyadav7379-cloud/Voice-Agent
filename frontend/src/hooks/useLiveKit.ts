import { useCallback, useRef, useState } from "react";
import {
  ConnectionState,
  DisconnectReason,
  Room,
  RoomEvent,
  Track,
  type LocalTrackPublication,
  type Participant,
  type RemoteParticipant,
} from "livekit-client";
import { AGENT_STATE_ATTRIBUTE, DEFAULT_ROOM_OPTIONS, classifyParticipant, parseAgentState } from "../lib/livekit";
import { generateId } from "../lib/auth";
import type { AgentState, ToolEvent, TranscriptEntry } from "../types/transcript";
import type { AppErrorInfo, MicPhase, SessionPhase } from "../types/livekit";

const DISCONNECT_REASON_LABEL: Partial<Record<DisconnectReason, string>> = {
  [DisconnectReason.DUPLICATE_IDENTITY]: "Another session for this user connected to the same room.",
  [DisconnectReason.SERVER_SHUTDOWN]: "The LiveKit server is shutting down.",
  [DisconnectReason.PARTICIPANT_REMOVED]: "You were removed from the room.",
  [DisconnectReason.ROOM_DELETED]: "The room was ended.",
  [DisconnectReason.STATE_MISMATCH]: "Connection state fell out of sync with the server.",
  [DisconnectReason.JOIN_FAILURE]: "Failed to join the room.",
};

function tryParseToolEvent(raw: unknown): Omit<ToolEvent, "id" | "receivedAt" | "raw"> | null {
  if (typeof raw !== "object" || raw === null) return null;
  const record = raw as Record<string, unknown>;
  const name = record.name ?? record.tool ?? record.tool_name;
  if (typeof name !== "string") return null;
  const statusRaw = record.status;
  const status =
    statusRaw === "started" || statusRaw === "succeeded" || statusRaw === "failed" ? statusRaw : "unknown";
  const detail =
    typeof record.detail === "string"
      ? record.detail
      : typeof record.message === "string"
        ? record.message
        : undefined;
  return { name, status, detail };
}

export function useLiveKit() {
  const roomRef = useRef<Room | null>(null);
  const [connectionPhase, setConnectionPhase] = useState<SessionPhase>("signed_out");
  const [micPhase, setMicPhase] = useState<MicPhase>("stopped");
  const [agentState, setAgentState] = useState<AgentState>("idle");
  const [agentIdentity, setAgentIdentity] = useState<string | null>(null);
  const [localAudioLevel, setLocalAudioLevel] = useState(0);
  const [agentAudioLevel, setAgentAudioLevel] = useState(0);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [errors, setErrors] = useState<AppErrorInfo[]>([]);

  const pushError = useCallback((title: string, message: string) => {
    setErrors((prev) => [...prev, { id: generateId("err"), title, message, at: Date.now() }]);
  }, []);

  const dismissError = useCallback((id: string) => {
    setErrors((prev) => prev.filter((e) => e.id !== id));
  }, []);

  const connect = useCallback(
    async (livekitUrl: string, livekitToken: string) => {
      if (!livekitUrl) {
        pushError("Missing LiveKit URL", "VITE_LIVEKIT_URL is not configured. Check your .env file.");
        setConnectionPhase("error");
        return;
      }

      setConnectionPhase("connecting");
      const room = new Room(DEFAULT_ROOM_OPTIONS);
      roomRef.current = room;

      room.on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
        if (state === ConnectionState.Connected) setConnectionPhase("connected");
        else if (state === ConnectionState.Connecting) setConnectionPhase("connecting");
        else if (state === ConnectionState.Reconnecting || state === ConnectionState.SignalReconnecting) {
          setConnectionPhase("reconnecting");
        }
      });

      room.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
        setConnectionPhase("signed_out"); // caller decides whether to route back to ready_to_connect
        setMicPhase("stopped");
        setAgentState("idle");
        setAgentIdentity(null);
        if (reason !== undefined && reason !== DisconnectReason.CLIENT_INITIATED) {
          const label = DISCONNECT_REASON_LABEL[reason] ?? "The session ended unexpectedly.";
          pushError("Disconnected", label);
        }
      });

      room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
        if (track.kind !== Track.Kind.Audio || participant.isLocal) return;

        const element = track.attach();
        element.autoplay = true;
        element.setAttribute("playsinline", "true");
        element.volume = 1;
        element.dataset.livekitAgentAudio = "true";
        document.body.appendChild(element);

        void element.play().catch((err: unknown) => {
          pushError(
            "Audio playback blocked",
            err instanceof Error
              ? err.message
              : "Click the page and reconnect to enable audio.",
          );
        });
      });

      room.on(RoomEvent.TrackUnsubscribed, (track, _publication, participant) => {
        if (track.kind !== Track.Kind.Audio || participant.isLocal) return;

        for (const element of track.detach()) {
          element.remove();
        }
      });

      room.on(RoomEvent.MediaDevicesError, (error: Error) => {
        pushError("Microphone error", error.message || "Could not access the microphone.");
        setMicPhase("stopped");
      });

      room.on(RoomEvent.ParticipantConnected, (participant: RemoteParticipant) => {
        setAgentIdentity(participant.identity);
        const state = parseAgentState(participant.attributes[AGENT_STATE_ATTRIBUTE]);
        if (state) setAgentState(state);
      });

      room.on(RoomEvent.ParticipantDisconnected, (participant: RemoteParticipant) => {
        setAgentIdentity((current) => (current === participant.identity ? null : current));
        setAgentState("idle");
      });

      room.on(
        RoomEvent.ParticipantAttributesChanged,
        (changed: Record<string, string>, participant: Participant) => {
          if (participant.isLocal) return;
          if (AGENT_STATE_ATTRIBUTE in changed) {
            const state = parseAgentState(changed[AGENT_STATE_ATTRIBUTE]);
            if (state) setAgentState(state);
          }
        },
      );

      room.on(RoomEvent.ActiveSpeakersChanged, (speakers: Participant[]) => {
        const localIdentity = room.localParticipant.identity;
        let nextLocalLevel = 0;
        let nextAgentLevel = 0;
        for (const speaker of speakers) {
          if (classifyParticipant(speaker.identity, localIdentity) === "user") {
            nextLocalLevel = Math.max(nextLocalLevel, speaker.audioLevel);
          } else {
            nextAgentLevel = Math.max(nextAgentLevel, speaker.audioLevel);
          }
        }
        setLocalAudioLevel(nextLocalLevel);
        setAgentAudioLevel(nextAgentLevel);
      });

      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        const localIdentity = room.localParticipant.identity;
        const speaker = participant ? classifyParticipant(participant.identity, localIdentity) : "unknown";
        setTranscript((prev) => {
          const byId = new Map(prev.map((entry) => [entry.id, entry] as const));
          for (const segment of segments) {
            byId.set(segment.id, {
              id: segment.id,
              speaker,
              participantIdentity: participant?.identity ?? "unknown",
              text: segment.text,
              final: segment.final,
              firstReceivedAt: segment.firstReceivedTime,
              lastReceivedAt: segment.lastReceivedTime,
            });
          }
          return Array.from(byId.values()).sort((a, b) => a.firstReceivedAt - b.firstReceivedAt);
        });
      });

      room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
        if (topic !== "tool_call" && topic !== "tool_result" && topic !== "tool_event") return;
        try {
          const text = new TextDecoder().decode(payload);
          const parsed = tryParseToolEvent(JSON.parse(text));
          if (!parsed) return;
          setToolEvents((prev) => [
            ...prev,
            { ...parsed, id: generateId("tool"), receivedAt: Date.now(), raw: text },
          ]);
        } catch {
          // Malformed payload from an untrusted source -- drop it rather than
          // displaying something we can't attribute to a real tool event.
        }
      });

      try {
        await room.connect(livekitUrl, livekitToken);
        try {
          await room.startAudio();
        } catch (audioErr) {
          pushError(
            "Audio playback needs permission",
            audioErr instanceof Error
              ? audioErr.message
              : "Click the page and reconnect to enable audio.",
          );
        }
        try {
          setMicPhase("starting");
          await room.localParticipant.setMicrophoneEnabled(true);
          setMicPhase("started");
        } catch (micErr) {
          setMicPhase("stopped");
          pushError(
            "Microphone permission needed",
            micErr instanceof Error ? micErr.message : "Could not enable the microphone.",
          );
        }
      } catch (err) {
        setConnectionPhase("error");
        pushError(
          "Connection failed",
          err instanceof Error ? err.message : "Could not connect to LiveKit.",
        );
        roomRef.current = null;
      }
    },
    [pushError],
  );

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    setConnectionPhase("disconnecting");
    await room.disconnect();
    roomRef.current = null;
    setTranscript([]);
    setToolEvents([]);
    setConnectionPhase("ready_to_connect");
  }, []);

  const startMicrophone = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    setMicPhase("starting");
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
      setMicPhase("started");
    } catch (err) {
      setMicPhase("stopped");
      pushError("Microphone error", err instanceof Error ? err.message : "Could not start the microphone.");
    }
  }, [pushError]);

  const stopMicrophone = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
      setMicPhase("stopped");
    } catch (err) {
      pushError("Microphone error", err instanceof Error ? err.message : "Could not stop the microphone.");
    }
  }, [pushError]);

  const toggleMute = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    const publication = room.localParticipant.getTrackPublication(Track.Source.Microphone) as
      | LocalTrackPublication
      | undefined;
    if (!publication) return;
    try {
      if (publication.isMuted) {
        await publication.unmute();
        setMicPhase("started");
      } else {
        await publication.mute();
        setMicPhase("muted");
      }
    } catch (err) {
      pushError("Microphone error", err instanceof Error ? err.message : "Could not toggle mute.");
    }
  }, [pushError]);

  return {
    connectionPhase,
    setConnectionPhase,
    micPhase,
    agentState,
    agentIdentity,
    localAudioLevel,
    agentAudioLevel,
    transcript,
    toolEvents,
    errors,
    dismissError,
    connect,
    disconnect,
    startMicrophone,
    stopMicrophone,
    toggleMute,
  };
}
