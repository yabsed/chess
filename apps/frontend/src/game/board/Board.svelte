<script lang="ts">
  import { createBoard } from './chessboard';

  import type { Move }                from './chessboard';
  import type { Color, GameSnapshot } from '../protocol';

  interface Props {
    game    : GameSnapshot;
    color   : Color;
    canMove : boolean;
    onmove  : (uci: string) => void;
  }

  let { game, color, canMove, onmove }: Props = $props();


  // Board orientation
  const opposite: Record<Color, Color> = { white: 'black', black: 'white' };

  let flipped     = $state(false);
  let orientation = $derived(flipped ? opposite[color] : color);


  // Promotion
  const promotionNames: Record<string, string> = {
    q : 'Queen',
    r : 'Rook',
    b : 'Bishop',
    n : 'Knight',
  };

  let promotions = $state<Move[]>([]);
  let promotionDialog: HTMLDialogElement;

  function selectMove(moves: Move[]) {
    if (!canMove) return;

    // Moves share their from/to squares; only promotion pieces can differ.
    if (moves.length === 1) onmove(moves[0].uci);
    if (moves.length > 1)   promotions = moves;
  }

  function promote(move: Move) {
    if (canMove) onmove(move.uci);
    promotions = [];
  }

  $effect(() => {
    if (promotions.length) promotionDialog.showModal();
    else                   promotionDialog.close();
  });


  function mountBoard(element: HTMLDivElement) {
    const board = createBoard(element, selectMove);

    $effect(() => {
      // A new snapshot, orientation or input permission cancels the pending selection.
      promotions = [];
      board.update(game, orientation, canMove);
    });

    return { destroy: board.destroy };
  }
</script>

<div>
  <div use:mountBoard></div>

  <div class="row">
    <small>Click two squares, or use arrow keys and Enter.</small>
    <button class="secondary" onclick={() => (flipped = !flipped)}>
      Flip board
    </button>
  </div>
</div>

<dialog
  bind:this={promotionDialog}
  onclose={() => (promotions = [])}
  aria-labelledby="promotion-title"
>
  <h2 id="promotion-title">Choose a promotion piece</h2>

  <div class="actions">
    {#each promotions as move}
      {#if move.promotion}
        <button disabled={!canMove} onclick={() => promote(move)}>
          {promotionNames[move.promotion]}
        </button>
      {/if}
    {/each}

    <button class="secondary" onclick={() => (promotions = [])}>
      Cancel
    </button>
  </div>
</dialog>

<style>
  .row { margin-top: 12px; }
  .row button { flex-shrink: 0; }

  :global(.cm-chessboard-accessibility.visually-hidden) { position: absolute; }
</style>
