<script lang="ts">
  import Board       from './board/Board.svelte';
  import MoveHistory from './MoveHistory.svelte';

  import type { Room }  from './room.svelte';
  import type { Color } from './protocol';

  let { room, color }: { room: Room; color: Color } = $props();

  let game = $derived(room.game);


  const connectionNames = {
    connecting   : 'Connecting…',
    connected    : 'Connected',
    disconnected : 'Reconnecting…',
    closed       : 'Disconnected',
  };

  let status = $derived.by(() => {
    if (game?.outcome) {
      if (game.outcome.result === '1/2-1/2') return 'Draw';
      if (game.outcome.result === '1-0')     return 'White wins';
      return 'Black wins';
    }

    if (room.connection === 'closed')       return game ? 'Disconnected' : 'Unable to connect';
    if (room.connection === 'disconnected') return 'Reconnecting to the server';
    if (!game)                             return 'Loading game';

    if (!room.hasOpponent)         return 'Waiting for a friend';
    if (game.connected.length < 2) return 'Waiting for your opponent to reconnect';

    const turn = room.isMyTurn ? 'Your turn' : 'Opponent’s turn';
    return game.check ? `${turn} · Check!` : turn;
  });


  const colors: Color[] = ['white', 'black'];
  const colorNames = { white: 'White', black: 'Black' };

  function playerStatus(player: Color) {
    if (game?.connected.includes(player)) return 'Online';
    if (game?.joined.includes(player))    return 'Offline';

    return 'Not joined';
  }


  let inviteNotice = $state('');

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(location.href);
      inviteNotice = 'Invite link copied.';
    } catch {
      inviteNotice = 'Select and copy the invite link below.';
    }
  }


  const endReasons: Record<string, string> = {
    checkmate             : 'Checkmate',
    stalemate             : 'Stalemate',
    insufficient_material : 'Insufficient material',
    seventyfive_moves     : '75-move rule',
    fivefold_repetition   : 'Fivefold repetition',
    resignation           : 'Resignation',
    draw_claim            : 'Threefold repetition or 50-move rule',
    agreement             : 'Draw by agreement',
  };

  function resign() {
    if (confirm('Resign this game?')) room.send('resign');
  }
</script>

<svelte:head>
  <title>{status} · Small Chess</title>
</svelte:head>

{#if inviteNotice}
  <p class="notice" role="status">{inviteNotice}</p>
{/if}

<div class="row heading">
  <div>
    <small>Playing as {colorNames[color]}</small>
    <h1 aria-live="polite">{status}</h1>
  </div>
  <small>{connectionNames[room.connection]}</small>
</div>

{#if room.connection === 'closed'}
  <p><a href="/">Back to home</a> · Refresh to reconnect to your seat.</p>
{/if}

{#if game}
  <div class="game-layout">
    <Board
      {game} {color}
      canMove={room.allowed.move}
      onmove={(uci) => room.send('move', uci)}
    />

    <div>
      <article>
        <h2>Game</h2>

        {#each colors as player}
          <p class="row">
            <strong>{colorNames[player]}{player === color ? ' · You' : ''}</strong>
            <small>{playerStatus(player)}</small>
          </p>
        {/each}

        {#if !room.hasOpponent}
          <label for="invite">Share this link with a friend</label>
          <input
            id="invite" readonly value={location.href}
            onclick={(event) => event.currentTarget.select()}
          />
          <button onclick={copyLink}>Copy invite link</button>
        {/if}

        {#if game.outcome}
          <p>{game.outcome.result} · {endReasons[game.outcome.reason] ?? game.outcome.reason}</p>
          <button disabled={room.entering} onclick={() => room.create()}>
            New game
          </button>
        {:else if room.inProgress}
          <div class="commands">
            {#if room.drawOfferedToMe}
              <p>Your opponent offers a draw.</p>

              <div class="actions">
                <button disabled={!room.allowed.accept_draw} onclick={() => room.send('accept_draw')}>
                  Accept
                </button>
                <button
                  class="secondary" disabled={!room.allowed.decline_draw}
                  onclick={() => room.send('decline_draw')}
                >
                  Decline
                </button>
              </div>
            {:else}
              <button
                class="secondary" disabled={!room.allowed.offer_draw}
                onclick={() => room.send('offer_draw')}
              >
                {game.draw_offer ? 'Draw offered · Waiting' : 'Offer draw'}
              </button>
            {/if}

            {#if room.hasDrawClaim}
              <button
                class="secondary" disabled={!room.allowed.claim_draw}
                onclick={() => room.send('claim_draw')}
              >
                Claim draw
              </button>
            {/if}

            <button class="secondary" disabled={!room.allowed.resign} onclick={resign}>
              Resign
            </button>
          </div>
        {/if}
      </article>

      <MoveHistory moves={game.moves} />
    </div>
  </div>
{/if}

<style>
  .heading { margin-bottom: 16px; }
  .heading h1 { margin: 0; }
  .commands { display: grid; }

  .game-layout {
    display               : grid;
    grid-template-columns : minmax(0, 1fr) 280px;
    align-items           : start;
    gap                   : 24px;
  }

  @media (max-width: 720px) {
    .game-layout { grid-template-columns: minmax(0, 1fr); }
  }
</style>
