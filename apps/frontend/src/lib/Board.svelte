<script lang="ts">
  import type { Color, Game, Move } from './types';

  interface Props {
    game     : Game;
    color    : Color;
    disabled : boolean;
    onmove   : (move: string) => void;
  }

  let { game, color, disabled, onmove }: Props = $props();

  const pieces: Record<string, { symbol: string; name: string }> = {
    k: { symbol: '♚', name: 'King' },
    q: { symbol: '♛', name: 'Queen' },
    r: { symbol: '♜', name: 'Rook' },
    b: { symbol: '♝', name: 'Bishop' },
    n: { symbol: '♞', name: 'Knight' },
    p: { symbol: '♟', name: 'Pawn' },
  };

  let selected   = $state<string | null>(null);
  let promotions = $state<Move[]>([]);
  let flipped    = $state(false);

  let blackAtBottom = $derived((color === 'black') !== flipped);

  let squares = $derived.by(() => {
    const files   = blackAtBottom ? 'hgfedcba' : 'abcdefgh';
    const ranks   = blackAtBottom ? '12345678' : '87654321';
    const squares = [];

    for (let row = 0; row < 8; row++) {
      for (let column = 0; column < 8; column++) {
        squares.push({
          name      : files[column] + ranks[row],
          dark      : (row + column) % 2 === 1,
          rankLabel : column === 0 ? ranks[row]    : null,
          fileLabel : row    === 7 ? files[column] : null,
        });
      }
    }
    return squares;
  });

  let selectedMoves = $derived(game.legal_moves.filter((move) => move.from === selected));
  let destinations  = $derived(selectedMoves.map((move) => move.to));

  // Clear the previous selection when the server confirms a new state. Keep the board orientation.
  $effect(() => {
    game.revision;
    selected   = null;
    promotions = [];
  });

  function select(square: string) {
    if (disabled) return;

    const choices = selectedMoves.filter((move) => move.to === square);
    if (choices.length > 1) {
      promotions = choices;
      return;
    }
    if (choices.length === 1) {
      submitMove(choices[0]);
      return;
    }

    if (square === selected) {
      selected = null;
      return;
    }
    const canMove = game.legal_moves.some((move) => move.from === square);
    selected = canMove ? square : null;
  }

  function submitMove(move: Move) {
    onmove(move.uci);
    selected   = null;
    promotions = [];
  }

  function label(square: string) {
    const piece = game.board[square];
    if (!piece) return `${square} empty`;

    const colorName = piece === piece.toUpperCase() ? 'White' : 'Black';
    const pieceName = pieces[piece.toLowerCase()].name;
    return `${square} ${colorName} ${pieceName}`;
  }
</script>

<div class="board-wrap">
  <div class="board" role="group" aria-label="Chessboard">
    {#each squares as square (square.name)}
      {@const piece = game.board[square.name]}

      <button class="square" data-square={square.name}
              class:dark     ={square.dark}
              class:selected ={selected === square.name}
              class:last     ={game.last_move?.from === square.name ||
                               game.last_move?.to   === square.name}
              class:check    ={game.check === square.name}
              class:target   ={!disabled && destinations.includes(square.name)}

              aria-label={label(square.name)}
              aria-pressed={selected === square.name} aria-disabled={disabled}
              onclick={() => select(square.name)}>
        {#if square.rankLabel}
          <span class="rank" aria-hidden="true">{square.rankLabel}</span>
        {/if}
        {#if piece}
          <span class="piece" class:white={piece === piece.toUpperCase()} aria-hidden="true">
            {pieces[piece.toLowerCase()].symbol}
          </span>
        {/if}
        {#if square.fileLabel}
          <span class="file" aria-hidden="true">{square.fileLabel}</span>
        {/if}
      </button>
    {/each}
  </div>

  <div class="board-footer">
    <small>Choose a piece, then a square.</small>
    <button class="text-button" onclick={() => (flipped = !flipped)}>Flip board</button>
  </div>

  {#if promotions.length}
    <div class="promotion" role="group" aria-label="Pawn promotion">
      <strong>Choose a promotion piece</strong>
      <div class="actions">
        {#each promotions as move}
          {#if move.promotion}
            <button {disabled} onclick={() => submitMove(move)}>
              {pieces[move.promotion].name}
            </button>
          {/if}
        {/each}
        <button class="secondary" onclick={() => (promotions = [])}>Cancel</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .board-wrap { position: relative; min-width: 0; }

  .board {
    display               : grid;
    grid-template-columns : repeat(8, 1fr);

    border        : 1px solid #35473d;
    border-radius : 5px;
    overflow      : hidden;
  }

  .square {
    position      : relative;
    aspect-ratio  : 1;
    padding       : 0;
    border        : 0;
    border-radius : 0;

    background : #e9eddf;
    color      : #31473b;

    display     : grid;
    place-items : center;
  }

  .square.dark { background: #72917d; color: #122e1e; }

  .square.last      { background: #d5da91; }
  .square.dark.last { background: #a4b366; }
  .square.selected  { background: #f0cd6c; }
  .square.check     { background: #d98579; }

  .square:focus-visible {
    outline        : 3px solid #163bce;
    outline-offset : -3px;
    z-index        : 1;
  }

  .square.target::after {
    content       : '';
    position      : absolute;
    width         : 24%;
    aspect-ratio  : 1;
    border-radius : 50%;

    background     : #15362570;
    pointer-events : none;
  }

  .square.target:has(.piece)::after {
    width      : 88%;
    box-sizing : border-box;
    background : transparent;
    border     : 5px solid #15362570;
  }

  .piece {
    font-family : 'DejaVu Sans', 'Segoe UI Symbol', 'Arial Unicode MS', serif;
    font-size   : clamp(1.8rem, 7vw, 3.5rem);
    line-height : 1;

    color       : #17271f;
    user-select : none;
  }

  .piece.white {
    color               : #fffdf5;
    -webkit-text-stroke : 1px #273c2e;
    text-shadow         : 0 1px 1px #273c2e;
  }

  .rank, .file {
    position : absolute;

    font-size   : clamp(.55rem, 1.5vw, .75rem);
    font-weight : 700;
    line-height : 1;
  }

  .rank { top: 5px; left: 5px; }

  .file { bottom: 4px; right: 5px; }

  .board-footer {
    display         : flex;
    align-items     : center;
    justify-content : space-between;
    gap             : 8px;

    margin-top : 10px;
  }

  .promotion {
    position : absolute;
    inset    : 20% 12px auto;
    padding  : 24px;

    background    : #fffef9;
    border        : 1px solid #35473d;
    border-radius : 8px;
    box-shadow    : 0 8px 40px #0003;
  }

  .promotion .actions { margin-top: 16px; }
</style>

