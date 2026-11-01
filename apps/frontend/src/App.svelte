<script lang="ts">
  import { onMount } from 'svelte';
  import { Room }    from './game/room.svelte';
  import { http }    from './http';
  import Account     from './Account.svelte';
  import GameView    from './game/GameView.svelte';
  import type { User } from './game/protocol';

  const room = new Room();
  let user  = $state<User | null>(null);
  let ready = $state(false);

  function setUser(next: User | null) {
    const changed = user?.id !== next?.id;
    user = next;
    if (changed) {
      if (user) room.open();
      else      room.reset();
    }
  }

  onMount(() => {
    const controller = new AbortController();
    http.get('me', { signal: controller.signal }).json<User | null>()
      .then(setUser)
      .catch(() => { room.error = 'Unable to reach the server. Refresh to try again.'; })
      .finally(() => { ready = true; });

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
  <a href="/" aria-label="Small Chess home">
    <strong>♞ Small Chess</strong>
  </a>
  <small>One move at a time.</small>
</header>

<main>
  {#if ready}
    <Account {user} onuser={setUser} />
  {:else}
    <p class="notice" role="status">Loading your account…</p>
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
        <p role="status">{room.connection === 'closed' ? 'Disconnected. Refresh to reconnect.' : 'Connecting…'}</p>
      {/if}

      {#if room.waiting}
        <p role="status">Your game starts when another player joins.</p>
        <button class="secondary" disabled={!room.canSend} onclick={() => room.enter('cancel')}>Cancel</button>
      {:else if room.id}
        <p>A friend has invited you to play.</p>
        <button disabled={!room.canSend || !room.validInvite} onclick={() => room.enter('join')}>
          {room.canSend ? 'Join as Black' : 'Joining…'}
        </button>
        <p><a href="/">Back to home</a></p>

        {#if !room.validInvite}
          <p class="error">This invite link is invalid.</p>
        {/if}
      {:else}
        <p>Find an opponent or invite a friend.</p>
        <div class="actions lobby-actions">
          <button disabled={!room.canSend} onclick={() => room.enter('match')}>Find opponent</button>
          <button class="secondary" disabled={!room.canSend} onclick={() => room.enter('create')}>Invite a friend</button>
        </div>
      {/if}

      <p><small>No clock · One game at a time · Resume after a refresh</small></p>
    </article>
  {/if}
</main>

<footer>
  Small Chess · Take your time. · <a href="/third-party-notices.txt">Credits</a>
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
