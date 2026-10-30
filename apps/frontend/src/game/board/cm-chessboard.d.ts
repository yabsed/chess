// cm-chessboard ships JavaScript only. These declarations cover the API used by chessboard.ts.
declare module 'cm-chessboard/src/Chessboard.js' {
  export interface MoveInput {
    type       : string;
    squareFrom : string;
    squareTo?  : string;
  }

  export const INPUT_EVENT_TYPE: {
    moveInputStarted  : string;
    validateMoveInput : string;
    movingOverSquare : string;
  };

  export class Chessboard {
    constructor(element: HTMLElement, options: {
      style: {
        cssClass          : string;
        animationDuration : number;
        pieces            : { file: string };
      };
      extensions: {
        class : Function;
        props : Record<string, unknown>;
      }[];
    });

    getExtension<T>(extension: { prototype: T }): T | null;
    getOrientation(): string;
    setOrientation(color: string): Promise<void>;
    setPosition(fen: string): Promise<void>;
    enableMoveInput(handler: (event: MoveInput) => boolean): void;
    disableMoveInput(): void;
    cancelMoveInput(): void;
    destroy(): void;
  }
}

declare module 'cm-chessboard/src/extensions/markers/Markers.js' {
  interface MarkerType {
    class     : string;
    slice     : string;
    position? : string;
  }

  export const MARKER_TYPE: {
    square       : MarkerType;
    circleDanger : MarkerType;
  };

  export class Markers {
    addMarker(type: MarkerType, square: string): void;
    removeMarkers(): void;
    addLegalMovesMarkers(moves: { to: string; promotion: string | null }[]): void;
    removeLegalMovesMarkers(): void;
  }
}

declare module 'cm-chessboard/src/extensions/accessibility/Accessibility.js' {
  export class Accessibility {}
}
