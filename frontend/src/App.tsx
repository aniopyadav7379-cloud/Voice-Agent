import { useCallback, useMemo } from "react";
import { AppShell } from "./components/AppShell";
import { SignInPanel } from "./components/SignInPanel";
import { useAuth } from "./hooks/useAuth";
import { useLiveKit } from "./hooks/useLiveKit";
import { useTheme } from "./hooks/useTheme";
import { LIVEKIT_URL } from "./lib/api";

const DEFAULT_TENANT_SLUG = "default";
const DEFAULT_EXTERNAL_ID = "demo-user";

function App() {
  const { preference, setPreference } = useTheme();
  const { session, isAuthenticating, authError, bootstrap, startNewSession } = useAuth();
  const {
  connectionPhase,
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
  } = useLiveKit();

  const showSignIn = !session && connectionPhase === "signed_out";

  const handleBootstrap = useCallback(
    async (tenantSlug: string, externalId: string) => {
      if (connectionPhase === "connecting" || connectionPhase === "connected" || isAuthenticating) return;
      const result = await bootstrap(tenantSlug, externalId).catch(() => null);
      if (!result) return;
      const targetUrl = result.livekitUrl || LIVEKIT_URL;
      await connect(targetUrl, result.livekitToken);
    },
    [bootstrap, connect, connectionPhase, isAuthenticating],
  );

  const handleConnect = useCallback(async () => {
    if (!session || connectionPhase === "connecting" || connectionPhase === "connected" || isAuthenticating) return;
    try {
      const { livekitToken, livekitUrl } = await startNewSession();
      const targetUrl = livekitUrl || LIVEKIT_URL;
      await connect(targetUrl, livekitToken);
    } catch {
      // authError from useAuth already reflects this; nothing further to do.
    }
  }, [session, startNewSession, connect, connectionPhase, isAuthenticating]);

  const handleDisconnect = useCallback(async () => {
    await disconnect();
  }, [disconnect]);

  const signInSlot = useMemo(
    () => (
      <SignInPanel
        defaultTenantSlug={session?.tenantSlug ?? DEFAULT_TENANT_SLUG}
        defaultExternalId={session?.externalId ?? DEFAULT_EXTERNAL_ID}
        isAuthenticating={isAuthenticating}
        authError={authError}
        onSubmit={handleBootstrap}
      />
    ),
    [session, isAuthenticating, authError, handleBootstrap],
  );

  const effectivePhase = connectionPhase === "signed_out" && session ? "ready_to_connect" : connectionPhase;

  return (
    <AppShell
      themePreference={preference}
      onThemeChange={setPreference}
      connectionPhase={effectivePhase}
      micPhase={micPhase}
      agentState={agentState}
      agentIdentity={agentIdentity}
      tenantSlug={session?.tenantSlug}
      externalId={session?.externalId}
      localAudioLevel={localAudioLevel}
      agentAudioLevel={agentAudioLevel}
      transcript={transcript}
      toolEvents={toolEvents}
      errors={errors}
      onDismissError={dismissError}
      onConnect={handleConnect}
      onDisconnect={handleDisconnect}
      onStartMic={startMicrophone}
      onStopMic={stopMicrophone}
      onToggleMute={toggleMute}
      signInSlot={signInSlot}
      showSignIn={showSignIn}
      isAuthenticating={isAuthenticating}
    />
  );
}

export default App;
