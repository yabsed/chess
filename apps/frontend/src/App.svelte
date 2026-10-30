<script lang="ts">
  import { onMount } from 'svelte';
  import { Room }    from './game/room.svelte';
  import GameView    from './game/GameView.svelte';

  const room = new Room();

  onMount(() => {
    room.restore();
    return () => room.close();
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
  {#if room.error}
    <p class="notice error" role="alert">{room.error}</p>
  {/if}

  {#if room.notice}
    <p class="notice" role="status">{room.notice}</p>
  {/if}

  {#if room.seat}
    <!-- Reset board orientation and invite feedback for each new game. -->
    {#key room.seat.id}
      <GameView {room} color={room.seat.color} />
    {/key}
  {:else}
    <article class="lobby">
      <small>Two players. No clock.</small>
      <h1>{room.id ? 'Ready for a game?' : 'Make your next move.'}</h1>
      <p>
        Create a game and share the link.<br />
        White moves first, then Black.
      </p>

      {#if room.id}
        <button disabled={room.entering || !room.validInvite} onclick={() => room.join()}>
          {room.entering ? 'Joining…' : 'Join as Black'}
        </button>
        <p><a href="/">Back to home</a></p>

        {#if !room.validInvite}
          <p class="error">This invite link is invalid.</p>
        {/if}
      {:else}
        <button disabled={room.entering} onclick={() => room.create()}>
          {room.entering ? 'Creating…' : 'New game'}
        </button>
      {/if}

      <p><small>No sign-up · Invite by link · Resume after a refresh</small></p>
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

  footer {
    margin     : 32px 0;
    text-align : center;
    color      : var(--text-light);
    font-size  : .8rem;
  }
</style>
