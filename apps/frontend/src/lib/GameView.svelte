<script lang="ts">
  import Board       from './Board.svelte';
  import ChatPanel   from './ChatPanel.svelte';
  import MoveHistory from './MoveHistory.svelte';

  import type { Action, Chat, Color, ConnectionStatus, Game } from './types';

  interface Props {
    game  : Game | null;
    color : Color;

    connectionStatus : ConnectionStatus;
    awaitingReply    : boolean;
    enteringGame     : boolean;

    chats           : Chat[];
    loadingHistory  : boolean;
    hasEarlierChats : boolean;

    onsend    : (action: Action, move?: string) => void;
    onnewgame : () => void;

    onsendchat : (content: string) => boolean;
    onhistory  : () => void;
  }

  let {
    game, color,
    connectionStatus, awaitingReply, enteringGame,
    chats, loadingHistory, hasEarlierChats,
    onsend, onnewgame,
    onsendchat, onhistory,
  }: Props = $props();

  const colors: Color[] = ['white', 'black'];
  const colorNames = { white: 'White', black: 'Black' };

  const connectionNames = {
    connecting   : 'Connecting…',
    connected    : 'Connected',
    disconnected : 'Reconnecting…',
    closed       : 'Disconnected',
  };

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

  let notice = $state('');

  let canSendCommand = $derived(connectionStatus === 'connected' && !awaitingReply);
  let canPlay        = $derived(canSendCommand && game?.connected.length === 2 && !game.outcome);

  let isMyTurn = $derived(game?.turn === color);
  let canMove  = $derived(canPlay && isMyTurn);

  let status = $derived.by(() => {
    if (!game) return 'Loading game';

    if (game.outcome) {
      if (game.outcome.result === '1/2-1/2') return 'Draw';
      if (game.outcome.result === '1-0')     return 'White wins';
      return 'Black wins';
    }

    if (game.joined.length < 2)            return 'Waiting for a friend';
    if (connectionStatus !== 'connected')  return 'Reconnecting to the server';
    if (game.connected.length < 2)         return 'Waiting for your opponent to reconnect';

    const turn = isMyTurn ? 'Your turn' : 'Opponent’s turn';
    return game.check ? `${turn} · Check!` : turn;
  });

  function playerStatus(player: Color) {
    if (game?.connected.includes(player)) return 'Online';
    if (game?.joined.includes(player))    return 'Offline';

    return 'Not joined';
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(location.href);
      notice = 'Invite link copied.';
    } catch {
      notice = 'Select and copy the invite link below.';
    }
  }

  function resign() {
    if (confirm('Resign this game?')) {
      onsend('resign');
    }
  }
</script>

<svelte:head><title>{status} · Small Chess</title></svelte:head>

{#if notice}
  <p class="message" role="status">{notice}</p>
{/if}

<div class="game-heading">
  <div>
    <p class="eyebrow">Playing as {colorNames[color]}</p>
    <h1 aria-live="polite">{status}</h1>
  </div>
  <span class="connection" class:online={connectionStatus === 'connected'}>
    {connectionNames[connectionStatus]}
  </span>
</div>

{#if connectionStatus === 'closed'}
  <p><a href="/">Back to home</a> · Refresh to reconnect to your seat.</p>
{/if}

{#if game}
  <div class="game-layout">
    <Board {game} {color} disabled={!canMove} onmove={(move) => onsend('move', move)} />

    <aside>
      <section class="panel">
        <h2>Game</h2>
        <div class="players">
          {#each colors as player}
            <div>
              <strong>{colorNames[player]}{player === color ? ' · You' : ''}</strong>
              <span class="muted">{playerStatus(player)}</span>
            </div>
          {/each}
        </div>

        {#if game.joined.length < 2}
          <label for="invite">Share this link with a friend</label>
          <input id="invite" aria-label="Invite link"
                 readonly value={location.href}
                 onclick={(event) => event.currentTarget.select()} />
          <button class="secondary full" onclick={copyLink}>Copy invite link</button>
        {/if}

        {#if game.outcome}
          <p class="result">
            {game.outcome.result} · {endReasons[game.outcome.reason] ?? game.outcome.reason}
          </p>
          <button class="full" disabled={enteringGame} onclick={onnewgame}>New game</button>
        {:else if game.joined.length === 2}
          {#if game.draw_offer && game.draw_offer !== color}
            <p>Your opponent offers a draw.</p>
            <div class="actions">
              <button disabled={!canPlay} onclick={() => onsend('accept_draw')}>Accept</button>
              <button class="secondary" disabled={!canPlay} onclick={() => onsend('decline_draw')}>Decline</button>
            </div>
          {:else}
            <button class="secondary full"
                    disabled={!canPlay || game.draw_offer !== null}
                    onclick={() => onsend('offer_draw')}>
              {game.draw_offer ? 'Draw offered · Waiting' : 'Offer draw'}
            </button>
          {/if}

          {#if game.can_claim_draw && isMyTurn}
            <button class="secondary full" disabled={!canPlay} onclick={() => onsend('claim_draw')}>
              Claim draw
            </button>
          {/if}
          <button class="text-button danger" disabled={!canSendCommand} onclick={resign}>Resign</button>
        {/if}
      </section>

      <ChatPanel messages={chats} you={color}
                 connected={connectionStatus === 'connected'}
                 {loadingHistory} hasEarlier={hasEarlierChats}
                 onsend={onsendchat} {onhistory} />

      <MoveHistory moves={game.moves} />
    </aside>
  </div>
{:else}
  <p class="muted">Loading the board…</p>
{/if}

<style>
  .danger { color: #9a4138; margin-top: 14px; }

  .game-heading {
    display         : flex;
    align-items     : center;
    justify-content : space-between;
    gap             : 12px;

    margin : 28px 0 18px;
  }

  .game-heading h1 { margin: 0; }

  .connection {
    font-size   : .75rem;
    color       : #87693c;
    white-space : nowrap;
  }

  .connection::before { content: '●'; margin-right: 6px; }

  .connection.online { color: #38714e; }

  .game-layout {
    display               : grid;
    grid-template-columns : minmax(0, 1fr) 280px;
    align-items           : start;
    gap                   : 24px;
  }

  aside { display: grid; gap: 16px; }

  .players {
    display : grid;
    gap     : 10px;
    margin  : 18px 0;
  }

  .players > div {
    display         : flex;
    justify-content : space-between;
    font-size       : .9rem;
  }

  label {
    display    : block;
    font-size  : .8rem;
    margin-top : 22px;
  }

  input {
    width         : 100%;
    padding       : 9px;
    border        : 1px solid #ccd4c8;
    border-radius : 4px;

    background : #f6f5ef;
    color      : #526451;

    font-size : .8rem;
  }

  .result {
    font-size   : .9rem;
    padding-top : 12px;
    border-top  : 1px solid #dce1d6;
  }

  @media (max-width: 720px) {
    .game-layout {
      grid-template-columns : 1fr;
      gap                   : 18px;
    }
  }
</style>
