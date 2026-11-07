<script lang="ts">
  import { onMount } from 'svelte';
  import { Room }    from './game/room.svelte';
  import { http, token, setToken } from './http';

  import Account  from './Account.svelte';
  import GameView from './game/GameView.svelte';
  import type { Profile, SessionResponse } from './protocol';

  const home       = import.meta.env.BASE_URL;
  const controller = new AbortController();
  const room       = new Room(checkSession);

  let user         = $state<Profile | null>(null);
  let ready        = $state(false);
  let accountError = $state('');


  function acceptSession(session: SessionResponse) {
    room.reset();
    setToken(session.token);
    user         = session.user;
    accountError = '';
    room.open();
  }

  function clearSession() {
    room.reset();
    setToken('');
    user         = null;
    accountError = '';
  }

  function updateProfile(profile: Profile) {
    // An upload can finish after the session has ended.
    if (user?.id === profile.id) user = profile;
  }


  async function checkSession() {
    const sessionToken = token;

    try {
      const profile = await http.get('me', { signal: controller.signal })
        .json<Profile | null>();

      // A previous session's response must not replace a newer login.
      if (controller.signal.aborted || token !== sessionToken) return;

      accountError = '';
      if (profile) user = profile;
      else         clearSession();
    } catch {
      if (controller.signal.aborted || token !== sessionToken) return;
      accountError = 'Unable to check your account. Refresh to try again.';
    }
  }

  onMount(() => {
    async function start() {
      await checkSession();
      if (controller.signal.aborted) return;

      if (user) room.open();
      ready = true;
    }

    void start();

    return () => {
      controller.abort();
      room.close();
    };
  });
</script>

<svelte:head>
  {#if !room.seat}
    <title>Small Chess</title>
  {/if}
</svelte:head>

<header class="row">
  <a href={home} aria-label="Small Chess home">
    <strong>♞ Small Chess</strong>
  </a>
  <small>One move at a time.</small>
</header>

<main>
  {#if ready}
    <Account
      {user}
      onlogin={acceptSession}
      onlogout={clearSession}
      onprofile={updateProfile}
    />
  {:else}
    <p class="notice" role="status">Loading your account…</p>
  {/if}

  {#if accountError}
    <p class="notice error" role="alert">{accountError}</p>
  {/if}

  {#if room.error}
    <p class="notice error" role="alert">{room.error}</p>
  {/if}

  {#if user && room.seat}
    <!-- Reset board orientation and invite feedback for each new game. -->
    {#key room.seat.id}
      <GameView {room} color={room.seat.color} />
    {/key}
  {:else if user}
    <article class="lobby">
      <small>Two players. No clock.</small>
      <h1>{room.waiting ? 'Finding an opponent…' : 'Make your next move.'}</h1>

      {#if room.connection !== 'connected'}
        <p role="status">
          {room.connection === 'closed' ? 'Disconnected. Refresh to reconnect.' : 'Connecting…'}
        </p>
      {/if}

      {#if room.waiting}
        <p role="status">Your game starts when another player joins.</p>
        <button
          class="secondary"
          disabled={!room.canSend}
          onclick={() => room.enter('cancel')}
        >
          Cancel
        </button>
      {:else if room.linkedRoomId}
        <p>A friend has invited you to play.</p>
        <button
          disabled={!room.canSend || !room.validInvite}
          onclick={() => room.enter('join')}
        >
          Join as Black
        </button>
        <p><a href={home}>Back to home</a></p>

        {#if !room.validInvite}
          <p class="error">This invite link is invalid.</p>
        {/if}
      {:else}
        <p>Find an opponent or invite a friend.</p>
        <div class="actions lobby-actions">
          <button
            disabled={!room.canSend}
            onclick={() => room.enter('match')}
          >
            Find opponent
          </button>
          <button
            class="secondary"
            disabled={!room.canSend}
            onclick={() => room.enter('create')}
          >
            Invite a friend
          </button>
        </div>
      {/if}

      <p><small>No clock · One game at a time · Resume after a refresh</small></p>
    </article>
  {/if}
</main>

<footer>
  Small Chess · Take your time. · <a href={`${home}third-party-notices.txt`}>Credits</a>
</footer>

<style>
  header {
    padding       : 24px 0;
    border-bottom : 1px solid var(--border);
  }

  header a { text-decoration: none; }

  .lobby {
    max-width  : 600px;
    margin     : 40px auto;
    padding    : 32px 24px;
    text-align : center;
  }

  .lobby-actions { justify-content: center; }

  footer {
    margin     : 32px 0;
    text-align : center;
    color      : var(--text-light);
    font-size  : .8rem;
  }
</style>
