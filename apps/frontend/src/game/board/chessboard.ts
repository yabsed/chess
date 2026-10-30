import { Chessboard, INPUT_EVENT_TYPE } from 'cm-chessboard/src/Chessboard.js';
import { Markers, MARKER_TYPE }         from 'cm-chessboard/src/extensions/markers/Markers.js';
import { Accessibility }               from 'cm-chessboard/src/extensions/accessibility/Accessibility.js';

import type { MoveInput }           from 'cm-chessboard/src/Chessboard.js';
import type { Color, GameSnapshot } from '../protocol';

import pieces       from 'cm-chessboard/assets/pieces/standard.svg?url&no-inline';
import markerSprite from 'cm-chessboard/assets/extensions/markers/markers.svg?url&no-inline';

import 'cm-chessboard/assets/chessboard.css';
import 'cm-chessboard/assets/extensions/markers/markers.css';

export interface Move {
  uci       : string;
  from      : string;
  to        : string;
  promotion : string | null;
}

export function createBoard(element: HTMLDivElement, onselect: (moves: Move[]) => void) {
  const board = new Chessboard(element, {
    style: {
      cssClass          : 'green',
      animationDuration : 0,
      pieces            : { file: pieces },
    },

    extensions: [
      { class: Markers, props: { sprite: markerSprite } },
      {
        class: Accessibility,
        props: {
          language          : 'en',
          keyboardMoveInput : true,
          movePieceForm     : false,
          piecesAsList      : false,
        },
      },
    ],
  });

  const markers = board.getExtension(Markers) as Markers;

  let moves: Move[] = [];
  let inputEnabled = false;

  // Clear targets before the library handles keyboard selection or cancellation.
  element.addEventListener('keydown', clearKeyboardTargets, true);

  return {
    // Each update discards unfinished input and replaces the server position.
    update(game: GameSnapshot, orientation: Color, canMove: boolean) {
      inputEnabled = false;
      board.cancelMoveInput();
      board.disableMoveInput();

      moves = game.legal_moves.map(parseMove);
      board.setPosition(game.fen);

      const side = orientation === 'white' ? 'w' : 'b';
      if (board.getOrientation() !== side) board.setOrientation(side);

      drawMarkers(game);

      inputEnabled = canMove;
      if (inputEnabled) board.enableMoveInput(handleMoveInput);
    },

    destroy() {
      element.removeEventListener('keydown', clearKeyboardTargets, true);
      board.destroy();
    },
  };


  function handleMoveInput({ type, squareFrom, squareTo }: MoveInput) {
    if (type === INPUT_EVENT_TYPE.movingOverSquare) return false;

    markers.removeLegalMovesMarkers();
    if (!inputEnabled) return false;

    const candidates = moves.filter((move) => move.from === squareFrom);

    if (type === INPUT_EVENT_TYPE.moveInputStarted) {
      markers.addLegalMovesMarkers(candidates);
      return candidates.length > 0;
    }

    if (type === INPUT_EVENT_TYPE.validateMoveInput) {
      const choices = candidates.filter((move) => move.to === squareTo);
      if (choices.length) onselect(choices);
    }

    // The server supplies the next position, including captures, castling and promotion.
    return false;
  }

  function clearKeyboardTargets(event: KeyboardEvent) {
    if (['Enter', ' ', 'Escape'].includes(event.key)) {
      markers.removeLegalMovesMarkers();
    }
  }

  function drawMarkers(game: GameSnapshot) {
    markers.removeMarkers();

    if (game.last_move) {
      const move = parseMove(game.last_move);
      markers.addMarker(MARKER_TYPE.square, move.from);
      markers.addMarker(MARKER_TYPE.square, move.to);
    }

    if (game.check) markers.addMarker(MARKER_TYPE.circleDanger, game.check);
  }
}

function parseMove(uci: string): Move {
  return {
    uci,
    from      : uci.slice(0, 2),
    to        : uci.slice(2, 4),
    promotion : uci[4] ?? null,
  };
}
