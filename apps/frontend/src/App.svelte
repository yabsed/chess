<script lang="ts">
  import { onMount } from 'svelte';
  import GameView    from './lib/GameView.svelte';

  import { connect, createGame, joinGame } from './lib/api';
  import type { Action, ConnectionStatus, Game, Session } from './lib/types';

  let roomId  = $state(new URLSearchParams(location.search).get('game') ?? '');
  let session = $state<Session | null>(null);
  let game    = $state<Game | null>(null);

  let connectionStatus = $state<ConnectionStatus>('connecting');
  let awaitingReply    = $state(false);
  let enteringGame     = $state(false);

  let error  = $state('');
  let notice = $state('');

  let client: ReturnType<typeof connect> | undefined;

  let validInvite = $derived(/^[a-f0-9]{12}$/.test(roomId));

  function start(newSession: Session) {
    client?.close();

    session       = newSession;
    roomId        = newSession.id;
    game          = null;
    awaitingReply = false;
    error         = '';

    const url = new URL(location.href);
    url.search = new URLSearchParams({ game: roomId }).toString();
    history.replaceState(null, '', url);

    client = connect(newSession, {
      onState(state) {
        game          = state;
        awaitingReply = false;
      },

      onStatus(status) {
        connectionStatus = status;
        if (status !== 'connected') {
          awaitingReply = false;
        }
      },

      onError(message) {
        error         = message;
        awaitingReply = false;
      },
    });
  }

  function create() {
    return enter(createGame());
  }

  function join() {
    return enter(joinGame(roomId));
  }

  async function enter(request: Promise<Session>) {
    enteringGame = true;
    error        = '';
    notice       = '';

    let newSession: Session;
    try {
      newSession = await request;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : 'Unable to connect to the server.';
      return;
    } finally {
      enteringGame = false;
    }

    try {
      const key = `chess:${newSession.id}`;
      sessionStorage.setItem(key, JSON.stringify(newSession));
    } catch {
      notice = 'Browser storage is unavailable. Your seat cannot be restored after a refresh.';
    }

    start(newSession);
  }

  function restoreSession() {
    if (!roomId) return;

    let saved;
    try {
      const stored = sessionStorage.getItem(`chess:${roomId}`);
      if (!stored) return;

      saved = JSON.parse(stored);
    } catch {
      error = 'Unable to read your saved session. Please join again.';
      return;
    }

    const validColor = saved?.color === 'white' || saved?.color === 'black';
    const validToken = typeof saved?.token === 'string' && saved.token.length > 0;
    if (saved?.id === roomId && validColor && validToken) {
      start(saved);
    }
  }

  function send(type: Action, move?: string) {
    if (!client || !game || connectionStatus !== 'connected' || awaitingReply) return;

    error         = '';
    awaitingReply = client.send({ type, revision: game.revision, move });

    if (!awaitingReply) {
      error = 'Check your connection and try again.';
    }
  }

  onMount(() => {
    restoreSession();
    return () => client?.close();
  });
</script>

<svelte:head>
  {#if !session}
    <title>Small Chess</title>
  {/if}
</svelte:head>

<main>
  <header>
    <a class="brand" href="/" aria-label="Small Chess home">
      <span aria-hidden="true">♞</span> Small Chess
    </a>
    <span class="muted">One move at a time.</span>
  </header>

  {#if error}
    <p class="message error" role="alert">{error}</p>
  {/if}
  {#if notice}
    <p class="message" role="status">{notice}</p>
  {/if}

  {#if !session}
    <section class="lobby panel">
      <div class="lobby-piece" aria-hidden="true">♟</div>
      <p class="eyebrow">Two players. No clock.</p>
      <h1>{roomId ? 'Ready for a game?' : 'Make your next move.'}</h1>
      <p class="muted">
        Create a game and share the link.<br />White moves first, then Black.
      </p>

      <div class="actions">
        {#if roomId}
          <button disabled={enteringGame || !validInvite} onclick={join}>
            {enteringGame ? 'Joining…' : 'Join as Black'}
          </button>
          <a class="text-button" href="/">Back to home</a>
        {:else}
          <button disabled={enteringGame} onclick={create}>
            {enteringGame ? 'Creating…' : 'New game'}
          </button>
        {/if}
      </div>

      {#if roomId && !validInvite}
        <p class="error">This invite link is invalid.</p>
      {/if}
      <small>No sign-up · Invite by link · Resume after a refresh</small>
    </section>
  {:else}
    {#key session.id}
      <GameView {game} color={session.color}
                {connectionStatus} {awaitingReply} {enteringGame}
                onsend={send} onnewgame={create} />
    {/key}
  {/if}

  <footer>Small Chess · Take your time.</footer>
</main>

<style>
  main {
    max-width : 1000px;
    margin    : auto;
    padding   : 28px 32px;
  }

  header {
    display         : flex;
    align-items     : center;
    justify-content : space-between;
    gap             : 12px;

    padding-bottom : 22px;
    border-bottom  : 1px solid #dce1d6;
  }

  .brand {
    display     : flex;
    align-items : center;
    gap         : 9px;

    font-size       : 1.15rem;
    font-weight     : 750;
    text-decoration : none;
  }

  .brand span { font-size: 2rem; line-height: 1; }

  .lobby { max-width: 600px; margin: 56px auto; padding: 44px 28px; text-align: center; }

  .lobby-piece {
    font-family   : 'DejaVu Sans', serif;
    font-size     : 4.5rem;
    line-height   : 1.1;
    margin-bottom : 26px;
  }

  .lobby .actions { justify-content: center; margin: 28px 0; }

  footer {
    margin     : 32px 0 6px;
    text-align : center;

    color     : #7b8678;
    font-size : .75rem;
  }

  @media (max-width: 720px) {
    main {
      max-width : 600px;
      padding   : 18px 16px;
    }

    .lobby { margin: 36px auto; }

    header .muted { font-size: .85rem; }
  }
</style>
